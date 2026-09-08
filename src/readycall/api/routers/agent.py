"""The agent workstation's HTTP + WebSocket surface (`D32`).

Thin, like every router here: validate, delegate, shape a response. The rules worth
naming are the ones the *shape* enforces rather than the code:

* **No request body carries an `agent_id`.** It comes from the agent session cookie, the
  same discipline `D4` applies to customers. A workstation that could name its own agent
  id would let any signed-in agent accept someone else's offer, end their call, or attest
  an identity in their name — and the disclosure log would record it as them.
* **The assurance gate is here, at the wire** (`D42`). The brief is rendered server-side
  for the *current* level, so a locked field is not in the response at all. Sending the
  full brief and hiding fields in React would put someone's coverage one devtools panel
  away.
* **Every mutation returns the new state**, so the workstation never has to guess what a
  click did or race a WebSocket push to find out.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi import status as http_status
from starlette.websockets import WebSocketDisconnect

from readycall.api.deps import ContainerDep, get_container
from readycall.api.schemas import (
    AgentLoginRequest,
    AgentPresenceOut,
    AgentSkillOut,
    AssistPushRequest,
    AttestIdentityRequest,
    CaptureKeysRequest,
    CaptureLabelRequest,
    CaptureLookupRequest,
    CaptureOut,
    ChallengeOut,
    DeclareStateRequest,
    DeclineOfferRequest,
    EndCallRequest,
    HandoffOut,
    HandoffRequest,
    IdentityOut,
    OfferOut,
    PendingWrapupOut,
    QueueOut,
    TranscriptTurnOut,
    WorkstationSnapshot,
    WrapupRequest,
)
from readycall.api.security import AgentPrincipal, AuthenticationRequired
from readycall.domain import events as ev
from readycall.domain.enums import AgentIntent, CallState, OfferOutcome
from readycall.domain.models import Assignment, CallWrapup
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.services.agents.dispatch import sole_candidate
from readycall.services.capture.keypad import Capture
from readycall.services.identity.attestation import AttestationOutcome

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agent", tags=["agent"])

#: Fire-and-forget work started by a request. `asyncio` keeps only a weak reference to a
#: task, so one that nobody holds can be collected while it is still running - the summary
#: would then vanish sometimes, on a timer nobody could reproduce.
_BACKGROUND: set[asyncio.Task[None]] = set()


async def get_agent(request: Request) -> AgentPrincipal:
    container = get_container(request)
    token = request.cookies.get(container.settings.agent_session_cookie_name)
    return await container.agent_sessions.resolve(token)


AgentDep = Annotated[AgentPrincipal, Depends(get_agent)]


def _bad_request(exc: PermanentError) -> HTTPException:
    """A refused domain rule is a 400, not a 500 — the request was wrong, not the server.

    The message is the domain's own, which is safe here: these are staff-facing rules
    ("confirming an identity requires naming the challenge used"), not anything that
    leaks another customer's data.
    """
    return HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc))


# --- session ------------------------------------------------------------------------------


@router.post("/demo-login", response_model=AgentPresenceOut)
async def demo_login(
    body: AgentLoginRequest, response: Response, container: ContainerDep
) -> AgentPresenceOut:
    """DEMO ONLY. Sign in as a roster agent (`D47`'s reasoning, staff side)."""
    if not container.settings.demo_agent_login_enabled:
        raise HTTPException(status_code=404, detail="not found")
    agent = await container.agents.get_agent(body.agent_id)
    if agent is None:
        raise HTTPException(status_code=404, detail="unknown agent")

    token = container.agent_sessions.issue(agent.agent_id)
    response.set_cookie(
        container.settings.agent_session_cookie_name,
        token,
        httponly=True,
        samesite="lax",
        max_age=int(container.settings.agent_session_ttl_s),
    )
    # A new session at this desk, so the push sequence starts again (`B27`).
    await container.hub.reset(agent.agent_id)
    await container.presence.sign_in(agent.agent_id, session_id=token[:8])
    return await _presence_out(container, agent.agent_id)


@router.post("/logout", status_code=http_status.HTTP_204_NO_CONTENT)
async def logout(request: Request, response: Response, container: ContainerDep) -> None:
    token = request.cookies.get(container.settings.agent_session_cookie_name)
    if token:
        try:
            who = await container.agent_sessions.resolve(token)
        except AuthenticationRequired:
            who = None
        if who is not None:
            try:
                await container.presence.sign_out(who.agent_id)
            except PermanentError:
                # Signing out mid-call is refused by the presence service. Clearing the
                # cookie anyway would leave a live call attached to a session nobody
                # holds, so the refusal has to reach the client.
                raise _bad_request(
                    PermanentError("cannot sign out while on a call; end the call first")
                ) from None
        container.agent_sessions.revoke(token)
    response.delete_cookie(container.settings.agent_session_cookie_name)


# --- presence -----------------------------------------------------------------------------


@router.get("/me", response_model=WorkstationSnapshot)
async def me(who: AgentDep, container: ContainerDep) -> WorkstationSnapshot:
    """Render the whole workstation from cold — the REST fallback for the socket."""
    return await _snapshot(container, who.agent_id)


@router.post("/state", response_model=AgentPresenceOut)
async def declare_state(
    body: DeclareStateRequest, who: AgentDep, container: ContainerDep
) -> AgentPresenceOut:
    """The person says what they are doing next. **This is what ends ACW** (`D45`)."""
    try:
        intent = AgentIntent(body.agent_intent)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown agent_intent") from None

    before = container.presence.get(who.agent_id)
    was_wrapping = before is not None and before.in_after_call_work
    try:
        await container.presence.declare(who.agent_id, intent)
    except PermanentError as exc:
        raise _bad_request(exc) from exc

    if was_wrapping:
        # Copy the measured duration onto the assignment it belongs to. Presence ends
        # ACW; this just files the number next to the call.
        assignment = _latest_wrapping_assignment(container, who.agent_id)
        if assignment is not None:
            await container.assignments.note_acw_ended(
                assignment_id=assignment.assignment_id, declared_intent=str(intent)
            )
            # And the call itself must leave `WRAP_UP`, saved or not (`B10`). Leaving it
            # open kept it "active", so the workstation went on rendering that customer
            # long after the agent had moved on — and the moment a LATER call closed, the
            # stale one came back and stayed for the rest of the shift.
            session = await container.calls.get(assignment.call_session_id)
            if session is not None and session.state is CallState.WRAP_UP:
                await container.assignments.close_unwrapped(
                    session, assignment_id=assignment.assignment_id
                )

    # **Match immediately, rather than on the next sweep** (`B27`). Pressing "พร้อมรับสาย"
    # while somebody is already in the queue should ring this desk now; waiting for the
    # sweep is up to a second of a caller sitting in front of an agent who is free, and
    # the matcher costs under 50 ms. Any declaration can change the matrix - going on
    # break can free a caller the solver had reserved for this agent - so it is not
    # conditional on `ready`.
    await container.dispatch.tick()

    out = await _presence_out(container, who.agent_id)
    await container.hub.send(who.agent_id, "presence", out.model_dump(mode="json"))
    return out


# --- the offer handshake --------------------------------------------------------------------


@router.post("/offers/{assignment_id}/accept", response_model=WorkstationSnapshot)
async def accept_offer(
    assignment_id: str, who: AgentDep, container: ContainerDep
) -> WorkstationSnapshot:
    assignment = _require_own(container, assignment_id, who)
    session = await container.calls.get(assignment.call_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown call")
    # `D21` lands here: the offer window IS the intake's grace period, so a caller who
    # was still talking when this agent was rung has kept talking until this moment. The
    # intake finalises as **partial** - everything said is kept, the brief renders it as
    # unfinished, and nobody waited a second longer for it. Before `accept`, so the last
    # of the transcript is attached to the call the agent is about to see.
    # **Close the transcriber BEFORE finalising the intake, and the order is the whole
    # point.** `finish()` transcribes the segment that was still open and drains the queue
    # before it returns, and those turns go to `IntakeService.on_turn` — which hands them
    # to the strategy only while it is still running. Finalising first makes every one of
    # them arrive after the intake closed, where `PassiveRecordIntake.on_turn` correctly
    # logs and drops them: the caller's last sentence, the one they were saying as the
    # agent picked up, silently missing from the brief. `D21` says the offer window IS the
    # grace period; this is what makes that true rather than merely intended.
    await container.transcription.close(session.call_session_id)
    # The recording is SEALED here and uploaded from the sweep (`D110`). Sealing is a
    # list handed to a queue; the object-store round trip is not something to put between
    # this agent pressing Accept and the caller hearing them (`D12`). Consent is read off
    # the session at this moment, so a caller who pressed 2 is transcribed and not stored.
    container.recording.close(session)
    await container.intake.on_agent_accepted(session.call_session_id)
    try:
        await container.assignments.accept(session, assignment_id=assignment_id)
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    container.dispatch.release(session.call_session_id)
    # The AI summary is asked for HERE and awaited NOWHERE (`D119`, `D12`). The agent is
    # connected the moment this endpoint returns, already reading the rule-based summary;
    # if the model answers in time the screen upgrades on the push, and if it never
    # answers nothing at all happens. Fire-and-forget is the only shape that keeps
    # "the call is never blocked on AI" true at the one moment it would be tempting to
    # break it, and the task is held so it cannot be garbage-collected mid-flight.
    task = asyncio.create_task(container.summarise_call(session.call_session_id, who.agent_id))
    _BACKGROUND.add(task)
    task.add_done_callback(_BACKGROUND.discard)
    return await _snapshot(container, who.agent_id)


@router.post("/offers/{assignment_id}/decline", response_model=WorkstationSnapshot)
async def decline_offer(
    assignment_id: str,
    body: DeclineOfferRequest,
    who: AgentDep,
    container: ContainerDep,
) -> WorkstationSnapshot:
    assignment = _require_own(container, assignment_id, who)
    session = await container.calls.get(assignment.call_session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown call")
    try:
        await container.assignments.decline(
            session, assignment_id=assignment_id, reason=body.reason
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    if body.stop_offering:
        # Before the tick, not after (`D109`). The tick is what re-matches this caller,
        # and an agent who has just said "stop offering" must not be a candidate for the
        # very call they declined, nor for the next one that lands in the same pass.
        await container.presence.stop_offering(
            who.agent_id, call_session_id=session.call_session_id
        )
    # Straight back into the pool: the caller keeps their accrued wait, and this agent is
    # now excluded from re-matching them (`D52`).
    await container.dispatch.tick()
    return await _snapshot(container, who.agent_id)


# --- the live call, and what follows ----------------------------------------------------------


@router.post("/calls/{call_session_id}/end", response_model=WorkstationSnapshot)
async def end_call(
    call_session_id: str,
    body: EndCallRequest,
    who: AgentDep,
    container: ContainerDep,
) -> WorkstationSnapshot:
    """Media disconnected. The after-call-work clock starts **here** (`D45`)."""
    session, assignment = await _require_active_call(container, call_session_id, who)
    try:
        await container.assignments.end_call(
            session, assignment_id=assignment.assignment_id, reason=body.reason
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    # The CUSTOMER's call ends here (`B37`). `D45` separates "the media stopped" from "the
    # agent finished their paperwork", and the customer is on the far side of that line:
    # they have hung up. Until this call existed, their screen went on saying
    # *"กำลังสนทนากับเจ้าหน้าที่"* for the whole of after-call work.
    #
    # `close()` does not delete the pairing — it shortens it to `PAIRING_GRACE`, so a form
    # somebody was halfway through when the broker rang off can still be submitted.
    container.assist.close(call_session_id)
    return await _snapshot(container, who.agent_id)


# --- handing the call to another company (D124) -------------------------------------------


async def _policy_insurer_for(container: Any, call_session_id: str) -> str | None:
    """The carrier that underwrote the policy this call is about, from the snapshot.

    The **frozen** snapshot rather than the bank core, for `_prefill_for`'s reason: it is
    what the system knew when it decided, it needs no round trip, and it cannot move
    underneath the call. It is also the answer that beats `insurers.yaml` when the two
    disagree (`D124`).
    """
    snapshot_id = container.snapshot_for_call.get(call_session_id)
    if snapshot_id is None:
        return None
    snapshot = await container.snapshots.get(snapshot_id)
    if snapshot is None:
        return None
    policy = snapshot.payload.relevant_policy
    return policy.insurer if policy is not None else None


@router.get("/calls/{call_session_id}/handoff/options")
async def handoff_options(
    call_session_id: str, who: AgentDep, container: ContainerDep
) -> dict[str, Any]:
    """Who this call may be handed to, and why (`D124`).

    Served rather than built in the client, for `assist_tools`' reason: a rail that
    renders its own list eventually offers something the server would refuse. It also
    carries the two facts the client cannot work out — the carrier on this customer's own
    policy, and whether we hold a policy at all, which is what makes half the reasons
    available or not.
    """
    session, _ = await _require_active_call(container, call_session_id, who)
    policy_insurer = await _policy_insurer_for(container, call_session_id)
    line = str(session.product_line) if session.product_line else None
    return {
        "policy_insurer": policy_insurer,
        "handoff_expected": _handoff_expected(container, session),
        "insurers": [
            {"code": spec.code, "name_th": spec.name_th}
            for spec in container.pack.insurers_for(line)
        ],
        "reasons": [
            {
                "code": spec.code,
                "label_th": spec.label_th,
                "requires_policy": spec.requires_policy,
                # The client greys a reason we cannot support and says why. The server
                # refuses it independently (`D121`): this is the label, not the gate.
                "available": bool(policy_insurer) or not spec.requires_policy,
            }
            for spec in container.pack.handoff_reasons.values()
        ],
    }


def _handoff_expected(container: Any, session: Any) -> bool:
    """Whether the intent this call was routed on is one the insurer owns (`D117`).

    The same `handoff_to_insurer` flag that already puts the banner above the policy
    panel. Read from the pack rather than re-derived, so the button and the banner can
    never disagree about whether this call ends with us.
    """
    code = session.menu_intent_code
    spec = container.pack.intents.get(code) if code else None
    return bool(spec is not None and spec.handoff_to_insurer)


@router.post("/calls/{call_session_id}/handoff", response_model=WorkstationSnapshot)
async def hand_off_call(
    call_session_id: str,
    body: HandoffRequest,
    who: AgentDep,
    container: ContainerDep,
) -> WorkstationSnapshot:
    """Record the handoff, then end the call — in that order, and the order matters.

    The wrap-up form renders the instant the state changes, so a prefill computed
    afterwards is a prefill nobody sees.

    ⚠️ **This does not use `CallState.TRANSFERRED`** (`D124`). That state is terminal, so
    a call in it could never reach `WRAP_UP`, and after-call work on a handoff is real
    work. Making it non-terminal instead would leave two states both meaning "the media is
    over and the agent is filing", which is `B25`/`B26`'s shape. What makes this a handoff
    rather than a hang-up is the record, not a state.
    """
    session, assignment = await _require_active_call(container, call_session_id, who)

    reason = container.pack.handoff_reasons.get(body.reason_code)
    if reason is None:
        raise _bad_request(PermanentError(f"unknown handoff reason {body.reason_code!r}"))

    policy_insurer = await _policy_insurer_for(container, call_session_id)
    if body.use_policy_insurer:
        if not policy_insurer:
            raise _bad_request(
                PermanentError("this call has no policy, so there is no carrier to hand it to")
            )
        insurer_name, insurer_code = policy_insurer, None
    else:
        spec = container.pack.insurers.get(body.insurer_code or "")
        if spec is None:
            raise _bad_request(PermanentError(f"unknown insurer {body.insurer_code!r}"))
        insurer_name, insurer_code = spec.name_th, spec.code

    try:
        record = container.transfer.record_handoff(
            call_session_id,
            agent_id=who.agent_id,
            insurer_name_th=insurer_name,
            insurer_code=insurer_code,
            reason=reason,
            note=body.note,
            holds_policy=bool(policy_insurer),
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc

    await container.bus.publish(
        ev.CallHandedOff(
            call_session_id=call_session_id,
            occurred_at=record.at,
            trace_id=session.trace_id,
            agent_id=who.agent_id,
            insurer_name_th=record.insurer_name_th,
            insurer_code=record.insurer_code,
            reason_code=record.reason_code,
        )
    )

    try:
        await container.assignments.end_call(
            session,
            assignment_id=assignment.assignment_id,
            reason=f"handed_to_insurer:{record.insurer_code or 'from_policy'}",
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    # The customer's side ends here exactly as it does on วางสาย (`B37`): they have been
    # passed on, and their screen must stop saying they are talking to us.
    container.assist.close(call_session_id)
    return await _snapshot(container, who.agent_id)


# --- the tool rail: what the broker can put on the customer's screen (D120) ---------------


async def _prefill_for(
    container: Any, call_session_id: str, wanted: tuple[str, ...]
) -> dict[str, Any]:
    """Fill a personal form from the frozen context snapshot (`D121`).

    Only ever reached for a tool declared `personal`, which `AssistService.push` has
    already refused unless the screen is signed in — so the customer seeing these values
    has proved who they are, not merely that they are holding a phone.

    Reads the **frozen snapshot** rather than the bank core, for `render_brief`'s reason:
    it is what the system knew when it decided, it needs no round trip, and it cannot
    move underneath the call.
    """
    snapshot_id = container.snapshot_for_call.get(call_session_id)
    if snapshot_id is None:
        return {}
    snapshot = await container.snapshots.get(snapshot_id)
    if snapshot is None:
        return {}
    payload = snapshot.payload
    policy = payload.relevant_policy
    customer = payload.customer
    available: dict[str, Any] = {}
    if policy is not None:
        available["policy_no"] = policy.policy_no
        available["insurer"] = policy.insurer
    if customer is not None:
        available["holder_name"] = " ".join(
            part for part in (customer.first_name_th, customer.last_name_th) if part
        )
    # Only what the tool asked for, and only what we actually hold. A field we cannot
    # fill is left for the customer rather than guessed at.
    return {k: v for k, v in available.items() if k in wanted and v}


@router.post("/calls/{call_session_id}/assist/link")
async def open_assist_link(
    call_session_id: str, who: AgentDep, container: ContainerDep
) -> dict[str, Any]:
    """Mint the pairing link for this call, so the broker can send it.

    Idempotent per call: pressing it twice - once because the SMS was slow and once
    because the customer said they had not got it - must not create a second live token
    with half the pushes going to a screen nobody is looking at.

    Nothing is sent from here. `NotifierPort` (SMS / LINE) is P5; until then the link
    comes back for the agent to read out, which is also exactly what a rehearsal needs.
    """
    session, _assignment = await _require_active_call(container, call_session_id, who)
    identity = container.identity_for_call.get(call_session_id)
    pairing = container.assist.open_for_call(
        call_session_id,
        customer_id=identity.customer_id if identity else None,
    )
    return {
        "token": pairing.token,
        "link": container.assist.link_for(pairing),
        "tier": str(pairing.tier),
        "paired": pairing.is_paired,
        "caller_number": getattr(session, "caller_number", None),
    }


@router.get("/calls/{call_session_id}/assist")
async def assist_state(
    call_session_id: str, who: AgentDep, container: ContainerDep
) -> dict[str, Any]:
    """What the customer's screen currently holds, and what they have sent back."""
    await _require_active_call(container, call_session_id, who)
    pairing = container.assist.for_call(call_session_id)
    if pairing is None:
        return {"paired": False, "tier": None, "items": []}
    return {
        "paired": pairing.is_paired,
        "tier": str(pairing.tier),
        "link": container.assist.link_for(pairing),
        "items": [
            {
                "item_id": i.item_id,
                "tool_id": i.tool_id,
                "kind": str(i.kind),
                "title_th": i.title_th,
                "personal": i.personal,
                "stub": i.stub,
                "responded": i.response is not None,
                "response": i.response,
            }
            for i in pairing.items
        ],
    }


@router.post("/calls/{call_session_id}/assist/push")
async def push_to_customer(
    call_session_id: str, body: AssistPushRequest, who: AgentDep, container: ContainerDep
) -> dict[str, Any]:
    """Put something on the paired screen.

    A personal push to a screen that has only tapped a link is REFUSED, and the refusal
    is the feature (`D120`): the broker is told why and asks the customer to sign in,
    rather than a stranger's policy appearing on whoever is holding that phone. That is
    `D74`'s rule - assurance gates what may be said and done - applied to the customer's
    own screen rather than to the agent's.
    """
    await _require_active_call(container, call_session_id, who)
    tool = container.pack.assist_tools.get(body.tool_id)
    if tool is None:
        raise HTTPException(status_code=400, detail=f"unknown tool {body.tool_id!r}")
    payload = dict(body.payload)
    if tool.fields:
        # The field list is the tool's, not the request's. A client that could send its
        # own field set could put any label it liked in front of the customer.
        payload["fields"] = [
            {"name": f.name, "label_th": f.label_th, "type": f.type, "required": f.required}
            for f in tool.fields
        ]
    if tool.prefill:
        payload["prefill"] = await _prefill_for(container, call_session_id, tool.prefill)
    try:
        item = container.assist.push(call_session_id, tool=tool, payload=payload)
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    return {
        "item_id": item.item_id,
        "tool_id": item.tool_id,
        "kind": str(item.kind),
        "title_th": item.title_th,
        "personal": item.personal,
        "stub": item.stub,
    }


@router.get("/assist/tools")
async def assist_tools(who: AgentDep, container: ContainerDep) -> dict[str, Any]:
    """The tool rail's catalogue, grouped, from `assist_tools.yaml` (`D121`).

    Served rather than hardcoded in the client, the same reason `D72` served the
    challenge list: a rail that renders its own list will eventually offer a tool the
    server would refuse, and the client is the copy that is wrong.
    """
    groups = sorted(container.pack.assist_groups.values(), key=lambda g: (g.order, g.group_id))
    return {
        "groups": [
            {
                "group_id": g.group_id,
                "label_th": g.label_th,
                "tools": [
                    {
                        "tool_id": t.tool_id,
                        "label_th": t.label_th,
                        "hint_th": t.hint_th,
                        "kind": t.kind,
                        # The client greys a personal tool on a guest screen and says why.
                        # The SERVER still refuses it — this is the label, not the gate.
                        "personal": t.personal,
                        "stub": t.stub,
                    }
                    for t in container.pack.assist_tools.values()
                    if t.group == g.group_id
                ],
            }
            for g in groups
        ]
    }


@router.post("/calls/{call_session_id}/wrapup", response_model=WorkstationSnapshot)
async def save_wrapup(
    call_session_id: str,
    body: WrapupRequest,
    who: AgentDep,
    container: ContainerDep,
) -> WorkstationSnapshot:
    """Closes the **call record**. Does not end after-call work (`D45`).

    The workstation's *Save & Ready* button calls this and then `/state` — two requests,
    on purpose, because they are two different statements and either may happen alone.
    """
    assignment = _assignment_for_call(container, call_session_id, who)
    session = await container.calls.get(call_session_id)
    if session is None or assignment is None:
        raise HTTPException(status_code=404, detail="unknown call")
    # Two states may be wrapped up. The obvious one is `WRAP_UP` — the agent is sitting
    # in after-call work right now. The other is a call they walked away from without
    # filing (`D87`): `D45` lets them leave, so the record has to stay fileable
    # afterwards, or "free to leave" quietly means "the note is lost".
    from_backlog = session.state is CallState.CLOSED and call_session_id not in container.wrapups
    if session.state is not CallState.WRAP_UP and not from_backlog:
        raise HTTPException(status_code=400, detail=f"call is {session.state}, not in wrap-up")
    try:
        if from_backlog:
            # Already closed, so there is no transition to make — only the record to file.
            await container.assignments.note_wrapup_filed_late(
                assignment_id=assignment.assignment_id,
                call_session_id=call_session_id,
                disposition=body.disposition,
                was_edited=body.was_edited,
                trace_id=session.trace_id,
            )
        else:
            await container.assignments.save_wrapup(
                session,
                assignment_id=assignment.assignment_id,
                disposition=body.disposition,
                was_edited=body.was_edited,
            )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    wrapup = CallWrapup(
        call_session_id=call_session_id,
        agent_id=who.agent_id,
        saved_at=container.clock.now(),
        disposition=body.disposition,
        notes=body.notes,
        follow_up_required=body.follow_up_required,
        was_edited=body.was_edited,
    )
    container.wrapups[call_session_id] = wrapup
    # Durable, because this is a person's statement about a customer's file. Nothing else
    # in the system may write one (`D45`), so losing it loses the only copy.
    await container.storage.wrapups.save(wrapup)
    return await _snapshot(container, who.agent_id)


# --- identity (D42) ----------------------------------------------------------------------------


@router.post("/calls/{call_session_id}/identity", response_model=WorkstationSnapshot)
async def attest_identity(
    call_session_id: str,
    body: AttestIdentityRequest,
    who: AgentDep,
    container: ContainerDep,
) -> WorkstationSnapshot:
    """The three-way control. Promotion is a **re-render, not a re-fetch** (`D42`)."""
    session, _assignment = await _require_active_call(container, call_session_id, who)
    if container.attestations.history(call_session_id) and not body.amend:
        # The control LOCKS after an attestation, and the server enforces it rather than
        # trusting a disabled button (`D60`). But it does not lock *forever*: an agent who
        # pressed "not this person" and then had the caller produce ID must be able to
        # confirm. Reopening is an explicit, separate action that appends a correction to
        # the disclosure log — both statements survive, which is the honest record and the
        # reason this is not just an editable field.
        raise HTTPException(
            status_code=409,
            detail="identity is already attested on this call; reopen it to amend",
        )
    try:
        outcome = AttestationOutcome(body.outcome)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown outcome") from None

    current = container.identity_for_call.get(call_session_id)
    if current is None:
        raise HTTPException(status_code=400, detail="this call has no identity to attest")

    try:
        updated, _record = await container.attestations.attest(
            call_session_id=call_session_id,
            agent_id=who.agent_id,
            current=current,
            outcome=outcome,
            challenge=body.challenge,
            challenge_note=body.challenge_note,
            caller_name=body.caller_name,
            relationship=body.relationship,
            note=body.note,
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc

    await container.set_identity(session, updated)
    return await _snapshot(container, who.agent_id)


# --- keypad capture (D44) -----------------------------------------------------------------------


@router.post("/calls/{call_session_id}/capture", response_model=CaptureOut)
async def start_capture(call_session_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    """Start capture. Untyped — the agent has not said what these digits will be."""
    await _require_active_call(container, call_session_id, who)
    try:
        capture = await container.captures.start(
            call_session_id=call_session_id, agent_id=who.agent_id
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    return _capture_out(capture)


@router.post("/captures/{capture_id}/keys", response_model=CaptureOut)
async def capture_keys(
    capture_id: str, body: CaptureKeysRequest, who: AgentDep, container: ContainerDep
) -> CaptureOut:
    """DTMF from the customer's handset, forwarded by telephony.

    Lives on the agent surface for now because the only source is the simulator. When
    Asterisk delivers real DTMF at P5 it calls the same service, not this route.
    """
    try:
        capture = container.captures.key(capture_id, body.digits)
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    out = _capture_out(capture)
    await container.hub.send(who.agent_id, "capture", out.model_dump(mode="json"))
    return out


@router.post("/captures/{capture_id}/backspace", response_model=CaptureOut)
async def backspace_capture(capture_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    """Callers mistype. Without this the only correction is discard-and-start-again."""
    try:
        capture = container.captures.backspace(capture_id)
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    out = _capture_out(capture)
    await container.hub.send(who.agent_id, "capture", out.model_dump(mode="json"))
    return out


@router.post("/captures/{capture_id}/stop", response_model=CaptureOut)
async def stop_capture(capture_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    try:
        return _capture_out(await container.captures.stop(capture_id))
    except PermanentError as exc:
        raise _bad_request(exc) from exc


@router.post("/captures/{capture_id}/discard", response_model=CaptureOut)
async def discard_capture(capture_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    """One click and the digits are gone — the inverted default of `D44`."""
    try:
        return _capture_out(await container.captures.discard(capture_id))
    except PermanentError as exc:
        raise _bad_request(exc) from exc


@router.post("/captures/{capture_id}/label", response_model=CaptureOut)
async def label_capture(
    capture_id: str, body: CaptureLabelRequest, who: AgentDep, container: ContainerDep
) -> CaptureOut:
    try:
        return _capture_out(await container.captures.label(capture_id, body.labelled_as))
    except PermanentError as exc:
        raise _bad_request(exc) from exc


@router.post("/captures/{capture_id}/lookup", response_model=CaptureOut)
async def lookup_capture(
    capture_id: str, body: CaptureLookupRequest, who: AgentDep, container: ContainerDep
) -> CaptureOut:
    """Run a lookup. **Returns evidence, never an action** (`D44`).

    Note what this endpoint does not do: it does not touch assurance, unlock a field, or
    write to the identity record. If a match were enough, a daughter holding her father's
    documents would be recorded as her father.
    """
    capture = container.captures.get(capture_id)
    if capture is None:
        raise HTTPException(status_code=404, detail="unknown capture")
    matched, matched_value, detail = await container.lookup_digits(
        capture.call_session_id, kind=body.kind, digits=capture.digits
    )
    await container.captures.record_lookup(
        capture_id, kind=body.kind, matched=matched, matched_value=matched_value, detail=detail
    )
    return _capture_out(capture)


# --- queues ---------------------------------------------------------------------------------------


@router.get("/queues", response_model=list[QueueOut])
async def queues(who: AgentDep, container: ContainerDep) -> list[QueueOut]:
    return _queues_out(container)


# --- the socket -----------------------------------------------------------------------


@router.websocket("/ws")
async def agent_socket(websocket: WebSocket) -> None:
    """One socket per workstation. Sequenced, replayable, and never authoritative.

    Auth is the cookie, read from the handshake — a token in the query string would end
    up in every proxy log between here and the desk.
    """
    container = websocket.app.state.container
    token = websocket.cookies.get(container.settings.agent_session_cookie_name)
    try:
        who = await container.agent_sessions.resolve(token)
    except AuthenticationRequired:
        await websocket.close(code=4401, reason="authentication required")
        return

    await websocket.accept()
    since = 0
    try:
        hello = await websocket.receive_json()
        since = int(hello.get("last_seq", 0)) if isinstance(hello, dict) else 0
    except (WebSocketDisconnect, ValueError, TypeError):
        # A client that says nothing gets everything in the buffer, which is the safe
        # direction: a duplicate offer is ignorable by seq, a missing one is a lost call.
        since = 0

    backlog = await container.hub.connect(who.agent_id, websocket, since_seq=since)
    try:
        for message in backlog:
            await websocket.send_json(message)
        snapshot = await _snapshot(container, who.agent_id)
        await websocket.send_json(
            {"seq": 0, "type": "snapshot", "payload": snapshot.model_dump(mode="json")}
        )

        while True:
            message = await websocket.receive_json()
            kind = message.get("type") if isinstance(message, dict) else None
            if kind == "heartbeat":
                await container.presence.heartbeat(who.agent_id)
                await websocket.send_json({"seq": 0, "type": "heartbeat_ack", "payload": {}})
            elif kind == "ack":
                await container.hub.ack(who.agent_id, websocket, int(message.get("seq", 0)))
    except WebSocketDisconnect:
        pass
    finally:
        await container.hub.disconnect(who.agent_id, websocket)


# --- shaping --------------------------------------------------------------------------


def _require_own(container: Any, assignment_id: str, who: AgentPrincipal) -> Assignment:
    assignment = container.assignments.get(assignment_id)
    if assignment is None:
        raise HTTPException(status_code=404, detail="unknown assignment")
    if assignment.agent_id != who.agent_id:
        # 404 rather than 403: whether *someone else's* assignment id exists is not this
        # agent's business either.
        raise HTTPException(status_code=404, detail="unknown assignment")
    return assignment  # type: ignore[no-any-return]


def _assignment_for_call(
    container: Any, call_session_id: str, who: AgentPrincipal
) -> Assignment | None:
    """This agent's **live** assignment for a call — never a superseded one (`B28`).

    Since `D113` a caller the whole floor declined comes round again, so one agent can
    hold **two** assignments for one call: the round-1 decline and the round-2 accept.
    This used to return whichever the store yielded first, which is the *decline* — so
    `end_call` checked an assignment that had never been accepted and refused to end a
    call the agent was demonstrably on.

    A declined, timed-out or cancelled offer is history, not a key to the call. Treating
    it as one also let an agent who said no keep acting on the caller somebody else took
    (`D52`) — `attest_identity` and `start_capture` authorise through this function.
    """
    live = [
        a
        for a in container.assignments.for_agent(who.agent_id)
        if a.call_session_id == call_session_id
        and a.outcome in (OfferOutcome.PENDING, OfferOutcome.ACCEPTED)
    ]
    if not live:
        return None
    # Newest wins: `D113` can only ever leave one un-superseded, but ordering the answer
    # is what stops this depending on dict insertion order again.
    return max(live, key=lambda a: a.offered_at)  # type: ignore[no-any-return]


async def _require_active_call(
    container: Any, call_session_id: str, who: AgentPrincipal
) -> tuple[Any, Assignment]:
    assignment = _assignment_for_call(container, call_session_id, who)
    session = await container.calls.get(call_session_id)
    if session is None or assignment is None:
        raise HTTPException(status_code=404, detail="unknown call")
    return session, assignment


def _latest_wrapping_assignment(container: Any, agent_id: str) -> Assignment | None:
    candidates = [
        a
        for a in container.assignments.for_agent(agent_id)
        if a.acw_started_at is not None and a.acw_ended_at is None
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda a: a.acw_started_at)  # type: ignore[no-any-return]


def _capture_out(capture: Capture) -> CaptureOut:
    return CaptureOut(
        capture_id=capture.capture_id,
        state=str(capture.state),
        length=capture.length,
        # The agent's own screen gets the real digits (`D58`); `masked` is what logs and
        # transcripts get. A discarded capture has none of either, by construction.
        digits=capture.digits,
        masked=capture.masked,
        labelled_as=capture.labelled_as,
        lookups=tuple(
            {
                "kind": lookup.kind,
                "matched": lookup.matched,
                "matched_value": lookup.matched_value,
                "detail": lookup.detail,
                "at": lookup.at.isoformat(),
            }
            for lookup in capture.lookups
        ),
    )


def _queues_out(container: Any, *, agent_skills: frozenset[str] = frozenset()) -> list[QueueOut]:
    """Every queue, each flagged with whether **this** agent could take from it (`D70`).

    The strip used to list all nine queues identically, so a health agent watched motor
    and life fill up and had no way to tell which of those numbers were theirs to act on.
    The flag is computed here rather than in the client for the usual reason: the client
    would need the skill-to-queue mapping to work it out, and a second copy of that
    mapping is a second thing that can disagree with the matcher.
    """
    now = container.clock.now()
    waiting = container.dispatch.waiting()
    out: list[QueueOut] = []
    for spec in container.pack.queues.values():
        state = container.hours.state(spec.hours, now)
        queued = [c for c in waiting if c.queue_id == spec.queue_id]
        out.append(
            QueueOut(
                queue_id=spec.queue_id,
                label_th=spec.label_th,
                sla_seconds=spec.sla_seconds,
                is_open=state.is_open,
                closed_reason=state.closed_reason,
                next_open_at=state.next_open_at,
                waiting=len(queued),
                longest_wait_s=max((c.total_wait_s for c in queued), default=0.0),
                # The EARLIEST anchor is the LONGEST wait — the same caller seen from the
                # two ends. Computed from the same list so the number and the thing the
                # client counts from cannot end up describing different people.
                longest_wait_since=min(
                    (c.waiting_since for c in queued if c.waiting_since is not None),
                    default=None,
                ),
                mine=spec.required_skill in agent_skills,
            )
        )
    return out


async def _presence_out(container: Any, agent_id: str) -> AgentPresenceOut:
    view = container.presence.view(agent_id)
    agent = await container.agents.get_agent(agent_id)
    if view is None or agent is None:
        raise HTTPException(status_code=404, detail="unknown agent")
    return AgentPresenceOut(
        agent_id=agent_id,
        display_name=agent.display_name,
        system_state=str(view.presence.system_state),
        agent_intent=str(view.presence.agent_intent),
        offerable=view.offerable,
        since=view.presence.since,
        current_load=view.presence.current_load,
        max_concurrent=agent.max_concurrent,
        skills=tuple(
            AgentSkillOut(
                skill_code=skill.skill_code,
                label_th=(
                    container.pack.skills[skill.skill_code].label_th
                    if skill.skill_code in container.pack.skills
                    else skill.skill_code
                ),
                proficiency=skill.proficiency,
            )
            for skill in agent.skills
        ),
        acw_seconds=view.acw_seconds,
        acw_since=view.acw_since,
        long_acw=view.long_acw,
        declarable=tuple(str(intent) for intent in view.declarable),
        awaiting_declaration=view.awaiting_declaration,
        intent_reason=view.intent_reason,
    )


async def _snapshot(container: Any, agent_id: str) -> WorkstationSnapshot:
    presence = await _presence_out(container, agent_id)
    agent_skills = frozenset(skill.skill_code for skill in presence.skills)

    offer_out: OfferOut | None = None
    # An offer is on THIS agent's screen iff THEIR assignment is still pending. The first
    # version asked "does this call have an open offer", which is a different question:
    # after a decline the call's open offer belongs to somebody else, and the agent who
    # said no kept staring at the card for a caller who had already moved on.
    open_offer = next(
        (a for a in container.assignments.for_agent(agent_id) if a.outcome is OfferOutcome.PENDING),
        None,
    )
    if open_offer is not None:
        session = await container.calls.get(open_offer.call_session_id)
        decision = container.dispatch.last_decision_for(open_offer.call_session_id)
        queue_id = getattr(session, "queue_id", None) or "q_service"
        spec = container.pack.queues.get(queue_id)
        identity = container.identity_for_call.get(open_offer.call_session_id)
        # Urgency and wait come from the POOL's record of the caller, not from the call
        # session, which has neither field. The first version read them off the session
        # with `getattr(..., "normal")` defaults, so every offer card claimed a normal,
        # zero-second wait — beside a rationale that said "เรื่องเร่งด่วน". A card that
        # contradicts its own reason is worse than one with no reason.
        waiting = container.dispatch.waiting_call(open_offer.call_session_id)
        # A gated preview, so the agent knows what the call is ABOUT before accepting
        # (`D69`). This is the same `BriefOut` the panel renders, built by the same
        # assurance-gated path — so at L1 `customer` is absent and there is nothing here
        # to leak. Reaching into the raw brief instead would reintroduce `B5`.
        preview = await container.render_brief(open_offer.call_session_id)
        preview_customer = (preview or {}).get("customer") or {}
        preview_actions = (preview or {}).get("actions_th") or []
        offer_out = OfferOut(
            assignment_id=open_offer.assignment_id,
            call_session_id=open_offer.call_session_id,
            accept_mode=open_offer.accept_mode,
            timeout_s=container.settings.offer_timeout_s,
            offered_at=open_offer.offered_at,
            queue_id=queue_id,
            queue_label_th=spec.label_th if spec else queue_id,
            intent_code=waiting.intent_code if waiting else None,
            intent_label_th=(
                container.pack.intents[waiting.intent_code].label_th
                if waiting and waiting.intent_code in container.pack.intents
                else None
            ),
            urgency=str(waiting.intent_urgency) if waiting else "normal",
            waited_s=waiting.total_wait_s if waiting else 0.0,
            waited_since=waiting.waiting_since if waiting else None,
            assurance=str(identity.assurance) if identity else "l0_anonymous",
            rationale_th=decision.rationale_th if decision else None,
            offer_round=container.assignments.rounds_for(open_offer.call_session_id),
            sole_candidate=sole_candidate(decision) if decision else False,
            summary_th=(preview or {}).get("summary_th"),
            customer_name_th=preview_customer.get("display_name_th"),
            first_action_th=preview_actions[0] if preview_actions else None,
        )

    active_id = await _active_call_id(container, agent_id)
    answered_at = None
    identity_out: IdentityOut | None = None
    brief: dict[str, Any] | None = None
    captures: tuple[CaptureOut, ...] = ()
    if active_id:
        resolution = container.identity_for_call.get(active_id)
        if resolution is not None:
            history = container.attestations.history(active_id)
            latest = history[-1] if history else None
            identity_out = IdentityOut(
                assurance=str(resolution.assurance),
                customer_id=resolution.customer_id,
                method=str(resolution.method),
                may_act_on_policy=resolution.may_act_on_policy,
                may_see_record=resolution.may_see_record,
                authority_check_required=bool(
                    resolution.evidence.get("authority_check_required", False)
                ),
                attested=latest is not None,
                attested_outcome=str(latest.outcome) if latest else None,
                third_party_name=latest.caller_name if latest else None,
                relationship=latest.relationship if latest else None,
                attestation_count=len(history),
                attestable=tuple(
                    str(o) for o in container.attestations.attestable(active_id, resolution)
                ),
                system_verified=container.attestations.system_verified(active_id, resolution),
            )
        # Rendered for the CURRENT assurance level, server-side (`D42`). A locked field
        # is absent from the payload, not hidden by the client.
        brief = await container.render_brief(active_id)
        captures = tuple(_capture_out(c) for c in container.captures.for_call(active_id))
        live = await container.calls.get(active_id)
        answered_at = getattr(live, "answered_at", None)

    wrapping_id = _wrapping_call_id(container, agent_id)
    return WorkstationSnapshot(
        presence=presence,
        offer=offer_out,
        active_call_session_id=active_id,
        identity=identity_out,
        brief=brief,
        captures=captures,
        queues=tuple(_queues_out(container, agent_skills=agent_skills)),
        challenges=tuple(
            ChallengeOut(code=c.code, label_th=c.label_th, requires_note=c.requires_note)
            for c in container.pack.challenges.values()
        ),
        server_time=container.clock.now(),
        call_answered_at=answered_at,
        wrapup_call_session_id=wrapping_id,
        # The server has always known this — it is the key it writes the wrap-up under.
        # Not saying so forced the client to remember it locally, which lost it on refresh
        # and never showed it at all once the call id went away on save (`D68`).
        wrapup_saved=wrapping_id is not None and wrapping_id in container.wrapups,
        pending_wrapups=await _pending_wrapups(container, agent_id),
        # The call being handled OR wrapped up (`D106`). `active_call_session_id` drops to
        # null the instant a wrap-up is saved, and the transcript is what the agent writes
        # the wrap-up FROM (`ARCHITECTURE` §12) — so falling back to `wrapping_id` is the
        # difference between a useful panel and one that empties at the worst moment. Same
        # trap `D68` found with `wrapup_saved`.
        transcript=tuple(
            TranscriptTurnOut(**turn)
            for turn in container.transcript_delivery.turns_for(active_id or wrapping_id)
        ),
        # Keyed on the call being WRAPPED UP, not the active one (`D124`). By the time
        # the form is on screen the handoff has already ended the call, so
        # `active_call_session_id` is the wrong key and reading it there would produce a
        # prefill that is never once visible.
        handoff=_handoff_out(container, wrapping_id),
    )


def _handoff_out(container: Any, call_session_id: str | None) -> HandoffOut | None:
    record = container.transfer.handoff_for(call_session_id) if call_session_id else None
    if record is None:
        return None
    return HandoffOut(
        insurer_name_th=record.insurer_name_th,
        insurer_code=record.insurer_code,
        reason_code=record.reason_code,
        reason_label_th=record.reason_label_th,
        at=record.at,
        note=record.note,
        disposition_th=record.disposition_th,
    )


async def _active_call_id(container: Any, agent_id: str) -> str | None:
    """The call this agent is on, or wrapping up. Not one they were merely offered."""
    live_states = {CallState.IN_CALL, CallState.WRAP_UP}
    for assignment in sorted(
        container.assignments.for_agent(agent_id), key=lambda a: a.offered_at, reverse=True
    ):
        session = await container.calls.get(assignment.call_session_id)
        if session is not None and session.state in live_states:
            return str(assignment.call_session_id)
    return None


async def _pending_wrapups(container: Any, agent_id: str) -> tuple[PendingWrapupOut, ...]:
    """Calls this agent handled and never filed anything for (`D87`).

    Derived, not stored (`D78`): "ACW has ended AND there is no wrap-up" is the backlog
    entry. Nothing flags a call as owing one, so nothing can disagree about whether it does.
    """
    rows: list[PendingWrapupOut] = []
    for assignment in container.assignments.for_agent(agent_id):
        if assignment.acw_ended_at is None:
            continue  # still in after-call work, or never got there
        if assignment.call_session_id in container.wrapups:
            continue
        session = await container.calls.get(assignment.call_session_id)
        if session is None:
            continue
        intent_code = session.menu_intent_code
        spec = container.pack.intents.get(intent_code) if intent_code else None

        # A backlog row is a disclosure surface like any other, and it renders long after
        # the call — so the name is gated on that call's identity, not on the current one.
        resolution = container.identity_for_call.get(assignment.call_session_id)
        name: str | None = None
        if resolution is not None and resolution.may_see_record:
            snapshot_id = container.snapshot_for_call.get(assignment.call_session_id)
            snapshot = await container.snapshots.get(snapshot_id) if snapshot_id else None
            customer = snapshot.payload.customer if snapshot else None
            name = customer.polite_name_th if customer else None

        rows.append(
            PendingWrapupOut(
                call_session_id=str(assignment.call_session_id),
                ended_at=assignment.acw_started_at,
                acw_seconds=assignment.acw_seconds,
                intent_code=intent_code,
                intent_label_th=spec.label_th if spec else None,
                customer_name_th=name,
                assurance=str(resolution.assurance) if resolution else "l0_anonymous",
            )
        )
    # Oldest first: the one that has been waiting longest is the one to clear.
    return tuple(sorted(rows, key=lambda row: (row.ended_at is None, row.ended_at)))


def _wrapping_call_id(container: Any, agent_id: str) -> str | None:
    """The call this agent is in after-call work for, whether or not its record is closed.

    Deliberately *not* `_active_call_id`, which stops at `WRAP_UP` and so returns `None`
    the instant a wrap-up is saved and the call goes `CLOSED`. That is the right answer to
    "which call can I still act on" and the wrong one to "which call am I wrapping up" —
    and conflating them is why the saved-confirmation never rendered (`D68`). ACW runs
    from media disconnect until the agent declares a next state (`D45`), so it outlives
    the record by design.
    """
    for assignment in sorted(
        container.assignments.for_agent(agent_id), key=lambda a: a.offered_at, reverse=True
    ):
        if assignment.acw_started_at is not None and assignment.acw_ended_at is None:
            return str(assignment.call_session_id)
    return None


__all__ = ["router"]
