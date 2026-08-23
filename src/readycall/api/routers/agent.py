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

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, WebSocket
from fastapi import status as http_status
from starlette.websockets import WebSocketDisconnect

from readycall.api.deps import ContainerDep, get_container
from readycall.api.schemas import (
    AgentLoginRequest,
    AgentPresenceOut,
    AgentSkillOut,
    AttestIdentityRequest,
    CaptureKeysRequest,
    CaptureLabelRequest,
    CaptureLookupRequest,
    CaptureOut,
    DeclareStateRequest,
    DeclineOfferRequest,
    EndCallRequest,
    IdentityOut,
    OfferOut,
    QueueOut,
    WorkstationSnapshot,
    WrapupRequest,
)
from readycall.api.security import AgentPrincipal, AuthenticationRequired
from readycall.domain.enums import AgentIntent, CallState, OfferOutcome
from readycall.domain.models import Assignment
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.services.capture.keypad import Capture
from readycall.services.identity.attestation import AttestationOutcome

log = get_logger(__name__)

router = APIRouter(prefix="/v1/agent", tags=["agent"])


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
    try:
        await container.assignments.accept(session, assignment_id=assignment_id)
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    container.dispatch.release(session.call_session_id)
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
    return await _snapshot(container, who.agent_id)


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
    if session.state is not CallState.WRAP_UP:
        raise HTTPException(status_code=400, detail=f"call is {session.state}, not in wrap-up")
    try:
        await container.assignments.save_wrapup(
            session,
            assignment_id=assignment.assignment_id,
            disposition=body.disposition,
            was_edited=body.was_edited,
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc
    container.wrapups[call_session_id] = {
        "disposition": body.disposition,
        "notes": body.notes,
        "follow_up_required": body.follow_up_required,
    }
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
    await _require_active_call(container, call_session_id, who)
    try:
        outcome = AttestationOutcome(body.outcome)
    except ValueError:
        raise HTTPException(status_code=400, detail="unknown outcome") from None

    current = container.identity_for_call.get(call_session_id)
    if current is None:
        raise HTTPException(status_code=400, detail="this call has no identity to attest")

    try:
        updated, _record = container.attestations.attest(
            call_session_id=call_session_id,
            agent_id=who.agent_id,
            current=current,
            outcome=outcome,
            challenge=body.challenge,
            relationship=body.relationship,
            note=body.note,
        )
    except PermanentError as exc:
        raise _bad_request(exc) from exc

    container.identity_for_call[call_session_id] = updated
    return await _snapshot(container, who.agent_id)


# --- keypad capture (D44) -----------------------------------------------------------------------


@router.post("/calls/{call_session_id}/capture", response_model=CaptureOut)
async def start_capture(call_session_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    """Start capture. Untyped — the agent has not said what these digits will be."""
    await _require_active_call(container, call_session_id, who)
    try:
        capture = container.captures.start(call_session_id=call_session_id, agent_id=who.agent_id)
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
    # The live panel needs the digits themselves; nothing else ever does.
    await container.hub.send(
        who.agent_id,
        "capture",
        {**out.model_dump(mode="json"), "digits": capture.digits},
    )
    return out


@router.post("/captures/{capture_id}/stop", response_model=CaptureOut)
async def stop_capture(capture_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    try:
        return _capture_out(container.captures.stop(capture_id))
    except PermanentError as exc:
        raise _bad_request(exc) from exc


@router.post("/captures/{capture_id}/discard", response_model=CaptureOut)
async def discard_capture(capture_id: str, who: AgentDep, container: ContainerDep) -> CaptureOut:
    """One click and the digits are gone — the inverted default of `D44`."""
    try:
        return _capture_out(container.captures.discard(capture_id))
    except PermanentError as exc:
        raise _bad_request(exc) from exc


@router.post("/captures/{capture_id}/label", response_model=CaptureOut)
async def label_capture(
    capture_id: str, body: CaptureLabelRequest, who: AgentDep, container: ContainerDep
) -> CaptureOut:
    try:
        return _capture_out(container.captures.label(capture_id, body.labelled_as))
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
    container.captures.record_lookup(
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
    for assignment in container.assignments.for_agent(who.agent_id):
        if assignment.call_session_id == call_session_id:
            return assignment  # type: ignore[no-any-return]
    return None


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


def _queues_out(container: Any) -> list[QueueOut]:
    now = container.clock.now()
    waiting = container.dispatch.waiting()
    out: list[QueueOut] = []
    for spec in container.pack.queues.values():
        state = container.hours.state(spec.hours, now)
        mine = [c for c in waiting if c.queue_id == spec.queue_id]
        out.append(
            QueueOut(
                queue_id=spec.queue_id,
                label_th=spec.label_th,
                sla_seconds=spec.sla_seconds,
                is_open=state.is_open,
                closed_reason=state.closed_reason,
                next_open_at=state.next_open_at,
                waiting=len(mine),
                longest_wait_s=max((c.total_wait_s for c in mine), default=0.0),
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
        long_acw=view.long_acw,
    )


async def _snapshot(container: Any, agent_id: str) -> WorkstationSnapshot:
    presence = await _presence_out(container, agent_id)

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
        queue_id = getattr(session, "queue_id", None) or "q_general"
        spec = container.pack.queues.get(queue_id)
        identity = container.identity_for_call.get(open_offer.call_session_id)
        offer_out = OfferOut(
            assignment_id=open_offer.assignment_id,
            call_session_id=open_offer.call_session_id,
            accept_mode=open_offer.accept_mode,
            timeout_s=container.settings.offer_timeout_s,
            offered_at=open_offer.offered_at,
            queue_id=queue_id,
            queue_label_th=spec.label_th if spec else queue_id,
            intent_code=getattr(session, "menu_intent_code", None),
            urgency=str(getattr(session, "urgency", "normal")),
            waited_s=0.0,
            assurance=str(identity.assurance) if identity else "l0_anonymous",
            rationale_th=decision.rationale_th if decision else None,
        )

    active_id = await _active_call_id(container, agent_id)
    identity_out: IdentityOut | None = None
    brief: dict[str, Any] | None = None
    captures: tuple[CaptureOut, ...] = ()
    if active_id:
        resolution = container.identity_for_call.get(active_id)
        if resolution is not None:
            identity_out = IdentityOut(
                assurance=str(resolution.assurance),
                customer_id=resolution.customer_id,
                method=str(resolution.method),
                may_disclose_policy_details=resolution.may_disclose_policy_details,
                authority_check_required=bool(
                    resolution.evidence.get("authority_check_required", False)
                ),
            )
        # Rendered for the CURRENT assurance level, server-side (`D42`). A locked field
        # is absent from the payload, not hidden by the client.
        brief = await container.render_brief(active_id)
        captures = tuple(_capture_out(c) for c in container.captures.for_call(active_id))

    return WorkstationSnapshot(
        presence=presence,
        offer=offer_out,
        active_call_session_id=active_id,
        identity=identity_out,
        brief=brief,
        captures=captures,
        queues=tuple(_queues_out(container)),
        server_time=container.clock.now(),
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


__all__ = ["router"]
