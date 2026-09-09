"""DEMO ONLY: the persona picker that stands in for the bank's login (`D47`).

Everything here is a stand-in, gated behind `demo_login_enabled`. It exists because the
customer simulator needs *some* way to become an authenticated customer, and building a
real identity provider is both out of scope for the competition and beside the point — we
are a context layer on top of Krungsri's systems, not an auth vendor.

What is **not** a stand-in is the shape: a token issued server-side, a server-held mapping
to a customer, and an HttpOnly cookie. `/v1/calls/intents` cannot tell a demo session from
a real one, so replacing this with the bank's OIDC changes this file and nothing else.

Note what this module does *not* do: add a `list_customers` method to `CoreDataProvider`.
A browse endpoint is not something the real system needs, and putting it on the port would
oblige every future adapter — including one written under time pressure on hackathon
morning — to implement it. Instead the persona *ids* come from `config/demo_personas.yaml`
and everything displayed is read through port methods that already exist, so the picker
shows exactly the data an agent would see.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request, Response, status

from readycall.api.deps import ContainerDep, PrincipalDep
from readycall.api.schemas import (
    AppPlan,
    DemoLoginRequest,
    DemoLoginResponse,
    DemoPersona,
    IntakeOut,
    PlaceCallRequest,
    PlaceCallResponse,
)
from readycall.api.security import DemoSessionStore
from readycall.config import SttEngineName
from readycall.domain.enums import CallState, ProductLine, Urgency
from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.media.sources import WavFileSource
from readycall.services.identity.resolver import hash_token
from readycall.services.intake.service import HoldReport
from readycall.services.ivr.personalise import PersonalisationInputs
from readycall.services.ivr.service import ScriptedChoices
from readycall.services.matching.scoring import WaitingCall

log = get_logger(__name__)
router = APIRouter(prefix="/v1/demo", tags=["demo"])


@dataclass(frozen=True, slots=True)
class PersonaSpec:
    customer_id: str
    entry_screen: str | None
    app_intent: str | None
    product_code: str | None
    blurb_en: str
    blurb_th: str


def load_personas(config_dir: Path) -> tuple[PersonaSpec, ...]:
    path = Path(config_dir) / "demo_personas.yaml"
    if not path.exists():
        return ()
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    out: list[PersonaSpec] = []
    for entry in raw.get("personas") or []:
        try:
            out.append(
                PersonaSpec(
                    customer_id=entry["customer_id"],
                    entry_screen=entry.get("entry_screen"),
                    app_intent=entry.get("app_intent"),
                    product_code=entry.get("product_code"),
                    blurb_en=entry.get("blurb_en", ""),
                    blurb_th=entry.get("blurb_th", ""),
                )
            )
        except KeyError as exc:
            raise ConfigError(f"demo persona is malformed: missing {exc}") from exc
    return tuple(out)


def _llm_note_th(state: dict[str, Any]) -> str:
    """One sentence saying which model runs where (`B41`).

    Deliberately names the two stages apart. They can genuinely differ — `LLM_FAST_MODEL`
    builds a real client whatever `LLM_PROVIDER` says — and the single sentence this
    replaced hid exactly that, reporting *"no model configured"* on a machine that was
    calling one three times per call.
    """
    main = state["model"]
    fast = state["fast_model"]
    if main is None and fast is None:
        return "ปิดโมเดลอยู่ — ใช้สรุปแบบกฎ ซึ่งไม่เรียกโมเดลใดๆ"
    parts = []
    parts.append(f"สรุปหลังรับสายด้วย {main}" if main else "สรุปหลังรับสายแบบกฎ (ไม่เรียกโมเดล)")
    if fast:
        parts.append(f"สรุประหว่างรอรับสาย · แผงข้อมูลลูกค้า · เหตุผลเปรียบเทียบแผน ด้วย {fast}")
    return " · ".join(parts)


def _require_demo(container: ContainerDep) -> None:
    if not container.settings.demo_login_enabled:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="demo endpoints are disabled")


@router.get("/personas", response_model=list[DemoPersona], summary="Who you can log in as")
async def list_personas(container: ContainerDep) -> list[DemoPersona]:
    _require_demo(container)

    personas: list[DemoPersona] = []
    for spec in load_personas(container.settings.config_dir):
        customer = await container.core.get_customer(spec.customer_id)
        if customer is None:
            # A persona pointing at a customer the core does not have is a config error,
            # but not a reason to break the picker - skip it and say so.
            log.warning("demo persona has no customer", customer_id=spec.customer_id)
            continue
        policies = await container.core.list_policies(spec.customer_id, active_only=True)
        held = ", ".join(str(p.line) for p in policies) or "no active policies"
        personas.append(
            DemoPersona(
                customer_id=customer.customer_id,
                display_name=customer.polite_name_th,
                summary=f"{held} · {spec.blurb_th or spec.blurb_en}",
                product_code=spec.product_code,
                entry_screen=spec.entry_screen,
                app_intent=spec.app_intent,
            )
        )
    return personas


@router.get("/plans", response_model=list[AppPlan], summary="The signed-in customer's plans")
async def list_plans(principal: PrincipalDep, container: ContainerDep) -> list[AppPlan]:
    """Demo scaffolding. The real app already knows the customer's plans.

    Kept under `/v1/demo/` rather than `/v1/app/` precisely because a real client would
    never call it — the public surface stays exactly what the real Krungsri app would use.
    """
    _require_demo(container)

    plans: list[AppPlan] = []
    for policy in await container.core.list_policies(principal.customer_id, active_only=True):
        product = await container.core.get_product(policy.product_code)
        plans.append(
            AppPlan(
                product_code=policy.product_code,
                product_line=str(policy.line),
                name_th=product.name_th if product else policy.product_code,
                # Masked even here: this is a list view, and a policy number on screen is
                # a disclosure. The agent side gates it on assurance for the same reason.
                policy_no_masked="•••" + policy.policy_no[-4:],
                status=str(policy.status),
            )
        )
    return plans


@router.post("/session", response_model=DemoLoginResponse, summary="Log in as a persona")
async def demo_login(
    body: DemoLoginRequest, response: Response, container: ContainerDep
) -> DemoLoginResponse:
    _require_demo(container)

    allowed = {p.customer_id for p in load_personas(container.settings.config_dir)}
    if body.customer_id not in allowed:
        # Only the configured personas, so this never becomes "log in as any customer id".
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such persona")

    customer = await container.core.get_customer(body.customer_id)
    if customer is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such persona")

    sessions = container.sessions
    if not isinstance(sessions, DemoSessionStore):  # pragma: no cover - config guard
        raise HTTPException(status.HTTP_409_CONFLICT, detail="not running a demo session store")

    token = sessions.issue(customer.customer_id)
    response.set_cookie(
        key=container.settings.session_cookie_name,
        value=token,
        httponly=True,  # the page's own JS must never be able to read a session credential
        samesite="lax",
        max_age=int(container.settings.session_ttl_s),
    )
    log.info("demo login", customer_id=customer.customer_id)
    return DemoLoginResponse(customer_id=customer.customer_id, display_name=customer.polite_name_th)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Drop the session")
async def demo_logout(request: Request, response: Response, container: ContainerDep) -> None:
    _require_demo(container)
    # Revoke server-side as well as clearing the cookie. Deleting only the cookie leaves a
    # perfectly valid session sitting in the store for anyone who kept a copy of the token.
    token = request.cookies.get(container.settings.session_cookie_name)
    sessions = container.sessions
    if isinstance(sessions, DemoSessionStore):
        sessions.revoke(token)
    response.delete_cookie(container.settings.session_cookie_name)


# --- DEMO: a caller arriving, standing in for telephony -------------------------------
#
# Real calls arrive through `TelephonyProvider` at P5 (Asterisk/ARI). Until then the
# workstation needs *some* way to have a caller on the line, so this walks one call
# through the real lifecycle: identity resolution, IVR, queue, match, offer. Every step
# below is the production service - only the trigger is fake.
#
# As of **P3** the IVR here is the real one; the only thing this endpoint fakes is the
# arrival itself and the keypresses, because there is no audio until P5.


@router.post("/calls", response_model=PlaceCallResponse, summary="DEMO: place a call")
async def place_call(
    body: PlaceCallRequest, container: ContainerDep, request: Request
) -> PlaceCallResponse:
    _require_demo(container)

    # `D4` again, on the demo path: the correlation token is the only thing that binds a
    # call to an identity. A body field naming the customer would undo the whole ladder.
    resolution = await container.identity.resolve(
        correlation_token=body.correlation_token,
        caller_number=body.caller_number,
    )

    intent = None
    if body.correlation_token:
        intent = await container.intent_store.get_by_token_hash(hash_token(body.correlation_token))

    if intent is not None and resolution.customer_id:
        session = await container.orchestrator.start_from_intent(
            intent_id=intent.intent_id,
            customer_id=resolution.customer_id,
            product_code=intent.product_code,
            product_line=(
                container.pack.intent(body.intent_code).line
                if body.intent_code
                else ProductLine.UNKNOWN
            ),
        )
        # THEN DIAL (`B36`). `start_from_intent` leaves the call in `INTENT_CREATED`,
        # which is right: tapping Contact in the app produces a dial target, and the
        # customer has not yet rung. Only `CONNECTING` may enter the IVR, so this line is
        # the simulated dial — the thing telephony does at P5.
        #
        # Its absence meant the app path through this endpoint raised
        # `IllegalTransition: intent_created -> ivr` **every time**. Nothing had ever
        # driven it: the simulator minted a token and stopped, and every scenario and test
        # that reaches an agent comes in as a cold call.
        session = await container.orchestrator.transition(
            session, CallState.CONNECTING, reason="caller_dialled"
        )
    else:
        session = await container.orchestrator.start_cold_call(
            caller_number=body.caller_number,
            dialled_did=body.did,
        )

    await container.set_identity(session, resolution)

    # The context snapshot: reuse the one the app path already built (`D6`), or build one
    # now for a cold call. Reuse is the point - by the time the phone rings, the six reads
    # to the bank core have already happened.
    snapshot_id = None
    if intent is not None:
        snapshot_id = container.snapshot_for_intent.get(intent.intent_id)
    if snapshot_id is None and resolution.customer_id:
        # Pass the line. The app path above already does, and this path knew it just as
        # well and threw it away - so a cold caller holding more than one policy got
        # `relevant_policy: None` and an empty policy panel. It was invisible while the
        # demo customer held exactly one policy, because `_pick_relevant_policy` falls
        # back to "the only one they have"; giving that customer a real broker portfolio
        # (`D117`) removed the fallback and the gap showed immediately.
        snapshot = await container.assembler.build(
            customer_id=resolution.customer_id,
            product_line=(
                container.pack.intent(body.intent_code).line
                if body.intent_code
                else ProductLine.UNKNOWN
            ),
        )
        await container.snapshots.save(snapshot)
        snapshot_id = snapshot.snapshot_id
    if snapshot_id:
        container.snapshot_for_call[session.call_session_id] = snapshot_id

    # The real IVR since P3. Only the *keys* are faked - there is no audio until P5, so
    # the request body carries what a keypad would have sent. Everything else (greeting,
    # recording notice, menu order, reserved keys, retries, the queue decision) is the
    # production walk, and `ScriptedChoices` presses canonical keys so a demo script keeps
    # meaning what it says when a menu is reordered (`D81`).
    prefetched = await container.snapshots.get(snapshot_id) if snapshot_id else None
    ivr = await container.ivr.run(
        session,
        caller=ScriptedChoices(list(body.keys)),
        did=container.pack.did(body.did) if body.did else None,
        known_intent=body.intent_code,
        inputs=PersonalisationInputs.from_snapshot(
            prefetched.payload if prefetched else None, today=container.clock.now().date()
        ),
    )
    intent_code = ivr.intent_code
    queue_id = ivr.queue_id
    spec = container.pack.queues[queue_id]

    session.snapshot_id = snapshot_id
    await container.orchestrator.enqueue(session, queue_id=queue_id)

    # A closed queue is a routing outcome, not an error (`D25`). Say so plainly rather
    # than letting the caller sit in a queue nobody is staffing.
    hours = container.hours.state(spec.hours, container.clock.now())
    if not hours.is_open and not body.ignore_hours:
        await container.orchestrator.transition(
            session, CallState.VOICEMAIL, reason=f"queue_closed:{hours.closed_reason}"
        )
        return PlaceCallResponse(
            call_session_id=session.call_session_id,
            state=str(session.state),
            queue_id=queue_id,
            queue_open=False,
            closed_reason=hours.closed_reason,
            next_open_at=hours.next_open_at,
            assurance=str(resolution.assurance),
        )

    # Below the "queue is now known" line: the position, the offer, and the recording if
    # they want one (`ARCHITECTURE.md` section 6). It runs BEFORE matching for the same
    # reason a real caller hears it before an agent frees up - and it deliberately does
    # not run to completion. A caller still talking stays live, and `D21` ends them when
    # the agent presses Accept, which is a different request entirely.
    hold = await container.intake.run_offer(session, caller=ScriptedChoices(list(body.intake_keys)))

    # The recording actually starts here (`D107`). `run_offer` decides *whether* there is
    # one; nothing was turning the audio path on when there was, so `TranscriptionService`
    # was written, tested and reached only by its own suite — `B7`'s exact shape, and it
    # meant no `transcript.turn` was ever published by the running system.
    #
    # DEMO: at P5 the telephony adapter opens the leg and pushes real RTP. Until then this
    # endpoint does both, and `audio` plays a file down it so the whole path — normalise,
    # endpoint, transcribe, publish, deliver — runs with no phone in the room.
    if hold.recording:
        # The source is resolved BEFORE the leg opens, because the leg is opened in the
        # file's own format and the gateway is what resamples (`D96`). Opening at the
        # default 16 kHz and then feeding 8 kHz telephone audio would not fail — it would
        # transcribe a call played at double speed, which is the kind of wrong that reads
        # as a bad model.
        source = _demo_audio_source(container, body.audio) if body.audio else None
        fmt = source.fmt if source is not None else None
        await container.transcription.open(session.call_session_id, fmt=fmt)
        # And the recorder joins the same leg (`D110`). Two subscribers to one stream of
        # normalised frames, neither depending on the other: the transcript is built even
        # when storage is down, and the recording is kept even when the model is.
        container.recording.open(session.call_session_id, fmt=fmt)
        if source is not None:
            await _play_demo_audio(
                container, session.call_session_id, source, realtime=body.audio_realtime
            )

    await container.orchestrator.transition(session, CallState.MATCHED, reason="ready_for_matching")
    container.dispatch.admit(
        session,
        WaitingCall(
            call_session_id=session.call_session_id,
            queue_id=queue_id,
            required_skill=spec.required_skill,
            intent_code=intent_code or "unknown",
            intent_urgency=(
                container.pack.intent(intent_code).default_urgency
                if intent_code
                else Urgency.NORMAL
            ),
            # `waiting_s` is now recomputed from `queued_at` on every tick (`B12`), so a
            # value set here would be overwritten. The demo's "already waited N seconds"
            # affordance therefore rides on `waiting_credit_s`, which is exactly what that
            # field is for - accrued wait that survives being re-scored.
            waiting_s=0.0,
            waiting_credit_s=body.waited_s,
            sla_seconds=spec.sla_seconds,
        ),
    )
    result = await container.dispatch.tick()

    return PlaceCallResponse(
        call_session_id=session.call_session_id,
        state=str(session.state),
        queue_id=queue_id,
        queue_open=True,
        assurance=str(resolution.assurance),
        offered_to=result.offered[0] if result.offered else None,
        unplaced_reason=result.unplaced.get(session.call_session_id),
        intake=_intake_out(hold),
    )


def _demo_audio_source(container: ContainerDep, name: str) -> WavFileSource:
    """DEMO: resolve a caller's voice to a file on disk (`D107`).

    **A bare filename, resolved inside `demo_audio_dir`, and checked after resolution.**
    A path in a request body is a file-read primitive; rejecting `..` by inspecting the
    string is the version of this check that keeps getting bypassed, so the comparison is
    between *resolved* paths. That the endpoint exists only when `demo_login_enabled` is
    not the argument — a demo flag left on is precisely the configuration this would be
    exploited through, so the check is here as well as there.
    """
    if "/" in name or "\\" in name or name in {"", ".", ".."}:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="audio must be a bare filename")
    root = Path(container.settings.demo_audio_dir).resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="no such demo audio file")
    try:
        return WavFileSource(path)
    except ConfigError as exc:
        # A 16-bit-PCM-only message is a *useful* 400: it says what to run to fix it.
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


async def _play_demo_audio(
    container: ContainerDep,
    call_session_id: str,
    source: WavFileSource,
    *,
    realtime: bool,
) -> None:
    """DEMO: push the file down the open leg, the way telephony will at P5.

    In 20 ms packets, in the file's own format, letting the gateway normalise — which is
    what `sources.py` was built for (`D96`). Decoding straight to 16 kHz here would skip
    the exact code path that carries the call on the day.
    """
    packets = 0
    async for packet in source.stream(realtime=realtime):
        await container.transcription.push(call_session_id, packet)
        packets += 1
    log.info(
        "demo audio played",
        call_session_id=call_session_id,
        packets=packets,
        seconds=round(source.duration_s, 2),
        realtime=realtime,
    )


def _intake_out(hold: HoldReport) -> IntakeOut:
    return IntakeOut(
        outcome=str(hold.kind) if hold.kind else None,
        consented=hold.consented,
        recording=hold.recording,
        offers_made=hold.offers_made,
        intake_id=hold.intake_id,
        turn_count=len(hold.result.turns) if hold.result else 0,
        is_partial=hold.result.is_partial if hold.result else False,
        degraded=str(hold.degraded),
        played=hold.played,
        pressed=hold.pressed,
    )


def _scripted_intent(settings: Any) -> str | None:
    """Which intent `config/demo_transcript.yaml` says its lines are about (`D132`).

    Read here rather than threaded through `ScriptedSttEngine`, because it is a fact about
    the *demo script* rather than about transcription — the engine's job is to hand back
    lines, not to know what they mean.
    """
    path = getattr(settings, "demo_transcript_file", None)
    if not path or not Path(path).exists():
        return None
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except Exception:  # pragma: no cover - a malformed file is the loader's error to raise
        return None
    value = raw.get("matches_intent") if isinstance(raw, dict) else None
    return str(value) if value else None


@router.get("/call-options", summary="DEMO: what a test call can be configured with")
async def call_options(container: ContainerDep) -> dict[str, Any]:
    """Everything the workstation's test-call dialog needs to build itself (`D132`).

    **It reports the machine rather than describing it.** Every list here is read from the
    loaded domain pack, the real fixtures and the live `Settings` — so a dialog built on it
    cannot offer an intent that does not exist, a persona with no customer behind them, or
    an audio file that is not on disk. The alternative is a hand-written list in the client
    that goes stale silently, which is `D48`'s argument for one menu in one file pointed at
    the demo surface.

    ⚠️ **Assurance is not a setting here, and that is deliberate.** `D20`'s ladder is
    *derived from evidence*: a number nobody holds is L0, a number the core holds is L1, and
    the app's correlation token is L3. A dropdown that let somebody pick "L3" would assert a
    level with nothing behind it — exactly what `D84` removed from the IVR and what `D44`
    refuses. So each caller option carries the level it will **produce**, and the dialog
    shows that as a consequence rather than a choice, which also makes the ladder legible to
    anybody driving it.
    """
    _require_demo(container)
    settings = container.settings

    intents = [
        {
            "code": spec.code,
            "label_th": spec.label_th,
            "line": str(spec.line),
            "urgency": str(spec.default_urgency),
            "skill": spec.skill,
            "handoff_to_insurer": spec.handoff_to_insurer,
        }
        for spec in sorted(container.pack.intents.values(), key=lambda i: i.code)
    ]

    # Callers, and the assurance each one produces. An ANI match is *probable*, never
    # verified (`D20`), so the best a phone number alone can ever reach here is L1.
    callers: list[dict[str, Any]] = [
        {
            "key": "unknown",
            "label_th": "เบอร์ที่ระบบไม่รู้จัก",
            "caller_number": "0899999999",
            "assurance": "l0_anonymous",
            "note_th": "ไม่มีลูกค้าผูกกับเบอร์นี้ — หน้าจอจะไม่แสดงข้อมูลส่วนบุคคลเลย",
        }
    ]
    for spec in load_personas(settings.config_dir):
        customer = await container.core.get_customer(spec.customer_id)
        if customer is None or not customer.phones:
            continue
        policies = await container.core.list_policies(spec.customer_id, active_only=True)
        callers.append(
            {
                "key": spec.customer_id,
                "label_th": customer.polite_name_th,
                "caller_number": customer.phones[0],
                "assurance": "l1_probable",
                "note_th": (f"{len(policies)} กรมธรรม์ · เบอร์ตรงกับที่ระบบมี จึงได้แค่ L1 (ยังไม่ยืนยันตัวตน)"),
            }
        )

    audio_dir = settings.demo_audio_dir
    audio = sorted(f.name for f in audio_dir.glob("*.wav")) if audio_dir.is_dir() else []

    # What the AI stages ACTUALLY are, so the dialog can say whether ticking "transcribe"
    # and "summarise" will do anything real. A control that silently does nothing is worse
    # than one greyed out with its reason (`D71`).
    # ⚠️ **ASK THE CONTAINER, NOT `Settings`** (`B41`, `D138`). This read
    # `settings.llm_provider is not RULEBASED` and was wrong in the most expensive
    # direction: `build_fast_llm` builds a REAL client whenever `LLM_FAST_MODEL` is set,
    # whatever `LLM_PROVIDER` says — and the preview summary, the customer-context panel
    # and the comparison's reason sentence all run on `fast_llm or llm`. So a machine
    # configured `LLM_PROVIDER=rulebased` with `LLM_FAST_MODEL=gpt-5.4-mini` was calling a
    # hosted model for three of the four AI features while this dialog said
    # *"ยังไม่ได้ตั้งค่าโมเดล ... ซึ่งไม่เรียกโมเดลใดๆ"*. Since `D138` the container is also
    # the only thing that knows the answer after a runtime switch.
    llm_state = container.llm_state()
    llm_real = bool(llm_state["enabled"]) or llm_state["fast_model"] is not None
    scripted = settings.stt_engine is SttEngineName.SCRIPTED
    return {
        "intents": intents,
        "callers": callers,
        "audio": audio,
        "audio_dir": str(audio_dir),
        "stt": {
            "engine": str(settings.stt_engine),
            "scripted": scripted,
            # Which intent the scripted lines are ABOUT, straight out of the file that
            # holds them (`D132`). The dialog uses it to warn about a mismatch, because a
            # good model correctly REFUSES to summarise a motor crash filed as a health
            # claim (`D119`'s `is_clear`) — and a refusal renders as no AI summary at all,
            # which looks like a broken feature and is the opposite.
            "script_intent": _scripted_intent(settings) if scripted else None,
            "note_th": (
                "เอนจินชุดสาธิต — บทพูดมาจาก config/demo_transcript.yaml"
                if scripted
                else f"ถอดเสียงจริงด้วย {settings.stt_engine}"
            ),
        },
        "llm": {
            "real": llm_real,
            "provider": str(llm_state["provider"]),
            "model": llm_state["model"],
            "fast_model": llm_state["fast_model"],
            # Says which stages are real SEPARATELY, because they genuinely can differ:
            # the fast model can be configured while the main one is rule-based, and
            # collapsing that into one sentence is what made `B41` invisible.
            "note_th": _llm_note_th(llm_state),
        },
        "defaults": {
            # ⚠️ A RECOGNISED caller by default, not the anonymous one (`D132`, `Q38`).
            # An unrecognised number produces no context snapshot, so `render_brief`
            # returns nothing — and the AI summary rides on the brief, so it cannot
            # appear at all. Defaulting to the path where the feature is invisible is how
            # somebody concludes it is broken.
            "caller_key": next((c["key"] for c in callers if c["key"] != "unknown"), "unknown"),
            "waited_s": 40.0,
            "ignore_hours": True,
            "record": False,
            "audio": None,
            "audio_realtime": False,
        },
    }


@router.get("/agents", summary="DEMO: who you can sign in as on the workstation")
async def list_agents(container: ContainerDep) -> list[dict[str, str]]:
    """The staff-side persona picker. Ids and display names only — no skills, no
    contact details; the workstation asks for its own agent's detail after signing in."""
    _require_demo(container)
    return [
        {"agent_id": a.agent_id, "display_name": a.display_name, "team": a.team}
        for a in await container.agents.list_agents()
    ]
