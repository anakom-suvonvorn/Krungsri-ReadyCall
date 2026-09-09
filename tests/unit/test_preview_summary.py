"""The two-stage AI summary: preview during the offer, final after Accept (`D131`).

`test_llm.py` proves the summariser. This proves the **wiring**, which is the half this
project keeps getting wrong: `B7`, `B24`, `B36`, `B37` and `B38` were all correct code that
nothing called, or that was called only on a path nothing executes.

⚠️ The specific trap this file exists to hold down: **`DispatchService.tick()` has two
callers.** The sweep drives it in a running server, and `POST /v1/demo/calls` drives it
itself — and the demo endpoint is the path the whole demo and every test actually take. A
preview started by reading `DispatchResult` inside `sweep_once` would have been dead on
arrival for exactly that reason, which is `B36` restated. So the tests below drive the
**demo endpoint**, not the sweep.

Nothing here needs a key or a network: the container's summarisers are replaced with a fake
that answers instantly and counts, which is what lets the ordering assertions be exact.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from readycall.api.app import create_app, pump_once
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.ports.llm import LlmResult, LlmUsage, PromptRef
from readycall.services.analysis import ContextSummariser, IntakeSummariser
from tests.conftest import REPO_ROOT


class _CountingLlm:
    """Answers instantly, records what it was asked, and can be made slow on demand."""

    def __init__(self, text: str, *, delay_s: float = 0.0) -> None:
        self._text = text
        self._delay_s = delay_s
        #: The transcript of every call, so a test can assert the SECOND pass saw more
        #: than the first — which is the entire point of running it twice.
        self.transcripts: list[str] = []

    @property
    def name(self) -> str:
        return "counting"

    @property
    def model(self) -> str:
        return "counting-1"

    async def complete_structured(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        schema: type[Any],
        *,
        timeout_s: float,
    ) -> LlmResult[Any]:
        # Whichever input this prompt takes: `summarize_intake` passes a transcript,
        # `summarize_context` passes a fact list (`D134`).
        self.transcripts.append(str(variables.get("transcript") or variables.get("facts", "")))
        if self._delay_s:
            await asyncio.sleep(self._delay_s)
        return LlmResult(
            output=schema.model_validate({"summary_th": self._text, "is_clear": True}),
            usage=LlmUsage(model=self.model, provider=self.name, latency_ms=1.0),
        )

    async def stream_text(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover - unused
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


@pytest.fixture
def clock() -> ManualClock:
    """A Monday morning in Bangkok. `ManualClock()`'s default is a public holiday and
    every `business` queue is shut, which costs an hour every time somebody forgets."""
    return ManualClock(datetime(2026, 8, 24, 3, 0, tzinfo=UTC))


@pytest.fixture
def settings() -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        prompt_dir=REPO_ROOT / "prompts" / "th",
        demo_login_enabled=True,
        demo_agent_login_enabled=True,
        # Both drivers are stepped by hand: a test that waits on a background loop is slow
        # when it passes and unreadable when it fails.
        agent_sweep_interval_s=0,
        bus_drain_interval_s=0,
    )


@pytest.fixture
def client(settings: Settings, clock: ManualClock) -> Any:
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client


def container(client: Any) -> Any:
    return client.app.state.container


def install(client: Any, preview: _CountingLlm, final: _CountingLlm) -> None:
    """Swap both summarisers. Two distinct fakes, so 'which model ran' is assertable."""
    box = container(client)
    box.preview_summariser = IntakeSummariser(llm=preview, timeout_s=4.0)
    box.summariser = IntakeSummariser(llm=final, timeout_s=8.0)


def sign_in(client: Any, agent_id: str = "A002") -> None:
    """Sign in WITHOUT going ready. `D51`: signing in is not being ready."""
    assert client.post("/v1/agent/demo-login", json={"agent_id": agent_id}).status_code == 200


def go_ready(client: Any) -> None:
    """Declare ready, which is what makes this agent offerable on the next tick.

    ⚠️ Kept separate from `sign_in` because `POST /v1/demo/calls` ticks the dispatcher
    itself: an agent who is already ready is offered the call *inside* that request, before
    the caller has said a word. The real sequence is the other way round — the caller waits
    and talks, and a desk frees up afterwards — and the preview only has anything to
    summarise in that order.
    """
    assert client.post("/v1/agent/state", json={"agent_intent": "ready"}).status_code == 200


def feed(client: Any, call_id: str, *lines: str) -> None:
    """Put turns where the delivery service holds them, as the transcriber would.

    Reaching into `transcript_delivery` rather than replaying audio is deliberate: the
    audio path already has `test_transcript_over_http.py`, and what is under test here is
    what happens to those turns once they exist.
    """
    delivery = container(client).transcript_delivery
    held = list(delivery.turns_for(call_id))
    for line in lines:
        held.append(
            {
                "turn_id": f"t{len(held) + 1}",
                "seq": len(held) + 1,
                "speaker_role": "customer",
                "text": line,
                "t_start_ms": 0,
                "t_end_ms": 1000,
            }
        )
    # Private on purpose: this is the transcriber's own store, and the test is standing
    # in for the transcriber.
    delivery._turns[call_id] = held


def place(client: Any, **kw: Any) -> Any:
    body = {
        "intent_code": "motor.service.policy",
        "caller_number": "0812345678",
        "intake_keys": ["1"],
        "ignore_hours": True,
        **kw,
    }
    response = client.post("/v1/demo/calls", json=body)
    assert response.status_code == 200, response.text
    return response.json()


async def drain(client: Any, tries: int = 30) -> None:
    """Let the fire-and-forget preview task run. Not a sleep — `asyncio.sleep(0)` yields
    to the loop, and under `TestClient` the loop only advances while we ask it to."""
    for _ in range(tries):
        await asyncio.sleep(0)


# --- the preview -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_preview_runs_on_the_demo_path_not_only_on_the_sweep(client: Any) -> None:
    """⚠️ **The test that would have caught the bug I nearly shipped.**

    `POST /v1/demo/calls` ticks the dispatcher itself, so a preview triggered from
    `sweep_once` would never have fired here — on the one path the demo actually uses.
    """
    preview, final = _CountingLlm("สรุปตัวอย่าง"), _CountingLlm("สรุปฉบับเต็ม")
    install(client, preview, final)
    sign_in(client)

    placed = place(client)
    call_id = placed["call_session_id"]
    # Words arrive while the caller waits, then a desk frees up and the offer goes out.
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    assert preview.transcripts, (
        "no preview ran when a desk started ringing - the `on_offer` hook is not wired, "
        "or it is wired somewhere this path does not reach (`B36`)"
    )
    assert not final.transcripts, "the final pass must wait for Accept, not for the offer"


@pytest.mark.asyncio
async def test_the_preview_reaches_the_offer_card(client: Any) -> None:
    """The whole point: the summary is on the card BEFORE the agent presses Accept.

    No schema change was needed for this — `OfferOut.summary_th` already renders whatever
    `render_brief` produced, and `render_brief` already prefers the AI summary. The seam
    existed; nothing had ever put a summary into it early enough.
    """
    preview = _CountingLlm("ลูกค้าแจ้งเคลมรถยนต์ ชนเมื่อเช้านี้")
    install(client, preview, _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    offer = client.get("/v1/agent/me").json()["offer"]
    assert offer is not None
    assert offer["summary_th"] == "ลูกค้าแจ้งเคลมรถยนต์ ชนเมื่อเช้านี้"


@pytest.mark.asyncio
async def test_accepting_re_summarises_the_whole_transcript(client: Any) -> None:
    """`D21`: the caller keeps talking through the offer window, so the preview is always
    a summary of PART of a call. The final pass is what makes the screen right."""
    preview, final = _CountingLlm("สรุปตัวอย่าง"), _CountingLlm("สรุปฉบับเต็ม")
    install(client, preview, final)
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ")
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)
    assert len(preview.transcripts) == 1

    # They go on talking while the card rings, which is the whole of `D21`.
    feed(client, call_id, "อยู่แถวรัชดา ตอนนี้จอดข้างทางแล้ว", "ไม่มีใครบาดเจ็บครับ")
    offer = client.get("/v1/agent/me").json()["offer"]
    assert client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").status_code == 200
    await drain(client)
    await pump_once(container(client))

    assert final.transcripts, "accepting must re-summarise; the preview saw half the call"
    assert "ไม่มีใครบาดเจ็บ" in final.transcripts[0], (
        "the final pass must see the WHOLE transcript, including what was said while the "
        "offer card was on screen"
    )
    assert "ไม่มีใครบาดเจ็บ" not in preview.transcripts[0], "the preview could not have seen it"

    brief = client.get("/v1/agent/me").json()["brief"]
    assert brief["summary_th"] == "สรุปฉบับเต็ม", "the final summary must replace the preview"
    assert brief["summary_is_preview"] is False


@pytest.mark.asyncio
async def test_the_screen_says_a_preview_is_a_preview(client: Any) -> None:
    """`D68`: the server knows which pass wrote this, so the server says so. Without it
    the summary silently rewords itself and the agent cannot tell an upgrade from a bug."""
    install(client, _CountingLlm("สรุปตัวอย่าง"), _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    rendered = await container(client).render_brief(call_id)
    assert rendered["summary_th"] == "สรุปตัวอย่าง"
    assert rendered["summary_is_preview"] is True


@pytest.mark.asyncio
async def test_a_quiet_caller_is_not_re_summarised_on_every_tick(client: Any) -> None:
    """The supersede key is the TURN COUNT, not the passage of time.

    Without it a caller who says nothing for ninety seconds while being re-offered would
    pay for one model call per tick, forever, for an identical summary.
    """
    preview = _CountingLlm("สรุปตัวอย่าง")
    install(client, preview, _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    for _ in range(4):
        await container(client).summarise_call(call_id, "A002", preview=True)
    assert len(preview.transcripts) == 1, "a silent caller must cost exactly one call"

    feed(client, call_id, "ไม่มีใครบาดเจ็บครับ")
    await container(client).summarise_call(call_id, "A002", preview=True)
    assert len(preview.transcripts) == 2, "but a caller who said more is worth asking again"


@pytest.mark.asyncio
async def test_a_late_preview_never_overwrites_the_final_summary(client: Any) -> None:
    """⚠️ The race, and it is a real one: the agent presses Accept while the fast model is
    still thinking. Losing that race must not downgrade a whole-transcript summary to a
    summary of the first half of the call."""
    box = container(client)
    install(client, _CountingLlm("สรุปตัวอย่าง"), _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")

    # The final pass lands first...
    await box.summarise_call(call_id, "A002", preview=False)
    assert box.ai_summaries[call_id].text_th == "สรุปฉบับเต็ม"
    # ...and the slow preview arrives afterwards, as it would from a task started earlier.
    await box.summarise_call(call_id, "A002", preview=True)

    assert box.ai_summaries[call_id].text_th == "สรุปฉบับเต็ม", (
        "a late preview overwrote the final summary - the screen just lost half the call"
    )
    assert box.ai_summaries[call_id].is_final is True


@pytest.mark.asyncio
async def test_nothing_on_the_call_path_waits_for_the_model(client: Any) -> None:
    """`D12`, stated as a measurement rather than as a comment.

    The preview model is made to take five seconds. The dispatch tick that starts it, and
    the accept that follows, must both return immediately anyway.
    """
    install(client, _CountingLlm("สรุปตัวอย่าง", delay_s=5.0), _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    go_ready(client)

    loop = asyncio.get_running_loop()
    started = loop.time()
    await container(client).dispatch.tick()
    tick_s = loop.time() - started

    offer = client.get("/v1/agent/me").json()["offer"]
    started = loop.time()
    assert client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept").status_code == 200
    accept_s = loop.time() - started

    assert tick_s < 1.0, f"the dispatch tick waited {tick_s:.1f}s for a model (`D12`)"
    assert accept_s < 1.0, f"accept waited {accept_s:.1f}s for a model (`D12`)"


@pytest.mark.asyncio
async def test_the_preview_catches_up_while_the_card_is_still_ringing(client: Any) -> None:
    """⚠️ The case the natural flow actually produces, and the first design missed it.

    A person presses พร้อมรับสาย and *then* places a test call, so the offer fires before a
    single word has been transcribed — the first preview attempt has nothing to summarise.
    `D21` says the caller keeps talking through the whole offer window, so the refresh on
    each sweep is what turns that into a real summary before Accept.
    """
    preview = _CountingLlm("สรุประหว่างรอ")
    install(client, preview, _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)
    go_ready(client)  # ready FIRST: the offer will fire with an empty transcript

    call_id = place(client)["call_session_id"]
    await drain(client)
    assert not preview.transcripts, "there was nothing said yet, so nothing to summarise"

    offer = client.get("/v1/agent/me").json()["offer"]
    assert offer is not None, "the ready agent should already be ringing"

    # Now the caller starts talking, as they do for the whole offer window.
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    await container(client).refresh_previews()

    assert preview.transcripts, (
        "the preview never caught up - a caller who starts talking after the desk rings "
        "would show no AI summary at all on the card (`D21`)"
    )
    assert client.get("/v1/agent/me").json()["offer"]["summary_th"] == "สรุประหว่างรอ"


@pytest.mark.asyncio
async def test_a_talkative_caller_cannot_buy_a_model_call_every_second(client: Any) -> None:
    """The refresh runs on every sweep, so it needs a ceiling as well as a turn-count gate.

    Without one, a caller still speaking through a 20-second offer window would pay for
    twenty summaries of very nearly the same thing.
    """
    preview = _CountingLlm("สรุประหว่างรอ")
    install(client, preview, _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client)
    go_ready(client)

    call_id = place(client)["call_session_id"]
    await drain(client)
    for i in range(12):
        feed(client, call_id, f"ประโยคที่ {i} ที่ลูกค้าพูดระหว่างรอสาย")
        await container(client).refresh_previews()

    assert len(preview.transcripts) <= 3, (
        f"{len(preview.transcripts)} preview calls for one offer - the cap is not applied"
    )
    assert preview.transcripts, "but it must still have run at least once"


@pytest.mark.asyncio
async def test_the_refresh_leaves_an_accepted_call_alone(client: Any) -> None:
    """Once the final summary has landed there is nothing a preview can add, and running
    one would only risk the late-preview race the write guard exists to catch."""
    preview, final = _CountingLlm("สรุประหว่างรอ"), _CountingLlm("สรุปฉบับเต็ม")
    install(client, preview, final)
    sign_in(client)
    go_ready(client)

    call_id = place(client)["call_session_id"]
    feed(client, call_id, "สวัสดีครับ ผมโทรมาเรื่องเคลมรถ", "รถชนเมื่อเช้านี้ครับ")
    offer = client.get("/v1/agent/me").json()["offer"]
    client.post(f"/v1/agent/offers/{offer['assignment_id']}/accept")
    await drain(client)
    await pump_once(container(client))
    before = len(preview.transcripts)

    feed(client, call_id, "อีกประโยคหนึ่งหลังรับสายแล้ว")
    await container(client).refresh_previews()

    assert len(preview.transcripts) == before, "an accepted call must not be re-previewed"


# --- what else we know about this person (`D134`) ----------------------------------------


@pytest.mark.asyncio
async def test_the_context_summary_is_started_by_the_offer_hook(client: Any) -> None:
    """⚠️ **The test that would have caught what I nearly shipped.**

    `summarise_context` was written, correct, typechecked — and called by nothing, because
    the patch that was supposed to add it to the `on_offer` hook silently did not apply.
    The panel rendered its facts and simply never had a sentence, which looks like a model
    that declined rather than a wire that was never connected.

    So this asserts the **hook**, not the method: `B24`'s rule, and the reason every other
    driver in this system has a test that drives the driver.
    """
    context = _CountingLlm("ลูกค้าเพิ่งมีสินเชื่อบ้านและเพิ่งมีบุตร")
    box = container(client)
    box.context_summariser = ContextSummariser(llm=context, timeout_s=4.0)
    install(client, _CountingLlm("สรุประหว่างรอ"), _CountingLlm("สรุปฉบับเต็ม"))
    sign_in(client, "A003")  # the renewal desk; `general.renewal` needs `renewal.retention`

    # C000002 holds the mortgage + new-child signals, so there are facts to summarise.
    call_id = place(client, caller_number="+66898887777", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    assert context.transcripts, (
        "nothing asked the model about what we already know - `summarise_context` is not "
        "wired to the `on_offer` hook (`D134`)"
    )
    rendered = await box.render_brief(call_id)
    assert rendered["context"]["summary_th"] == "ลูกค้าเพิ่งมีสินเชื่อบ้านและเพิ่งมีบุตร"


@pytest.mark.asyncio
async def test_the_panel_carries_its_sources_and_marks_inferences(client: Any) -> None:
    """`D18`: a broker who cannot say where a fact came from cannot use it in a
    conversation. And a life-event signal is an INFERENCE — `income_pattern` at 0.6 is a
    guess — so it carries a confidence where a stored record carries none."""
    sign_in(client, "A003")
    call_id = place(client, caller_number="+66898887777", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()

    facts = (await container(client).render_brief(call_id))["context"]["facts"]
    assert facts, "C000002 has life events, holdings and interactions in the fixtures"
    assert all(f["source"] for f in facts), "every fact must name where it came from"

    inferred = [f for f in facts if f["kind"] == "life_event"]
    records = [f for f in facts if f["kind"] == "holding"]
    assert inferred and all(f["confidence"] is not None for f in inferred)
    assert records and all(f["confidence"] is None for f in records), (
        "a stored row has no confidence, and inventing 1.0 for one would make an "
        "inference and a record indistinguishable"
    )
    # Life events first: they are the only thing here that answers "why now".
    assert facts[0]["kind"] == "life_event"


@pytest.mark.asyncio
async def test_a_summary_that_recommends_a_product_is_refused(client: Any) -> None:
    """`D116` moves the consent gate from HOLDING data to RECOMMENDING from it.

    The prompt forbids it and this guard assumes the prompt will one day fail — the same
    belt-and-braces `_FIGURE` gets for `D16`.
    """
    box = container(client)
    pushy = _CountingLlm("ลูกค้าเพิ่งมีสินเชื่อบ้าน ควรซื้อประกันคุ้มครองสินเชื่อ")
    box.context_summariser = ContextSummariser(llm=pushy, timeout_s=4.0)
    sign_in(client, "A003")
    call_id = place(client, caller_number="+66898887777", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    assert pushy.transcripts, "the model was asked"
    assert box.context_summaries.get(call_id) is None, (
        "a summary that told the broker what to sell reached the screen (`D116`)"
    )
    assert box.context_summariser.refused == 1


@pytest.mark.asyncio
async def test_there_is_no_panel_at_all_for_an_unidentified_caller(client: Any) -> None:
    """L0 renders nothing, because at L0 there is nobody to render (`D74`)."""
    sign_in(client, "A003")
    call_id = place(client, caller_number="0899999999", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()

    rendered = await container(client).render_brief(call_id)
    assert rendered is None or rendered.get("context") is None


@pytest.mark.asyncio
async def test_a_buddhist_year_is_not_mistaken_for_a_coverage_figure(client: Any) -> None:
    """⚠️ Found on the first live run, and it threw away a correct summary.

    `_FIGURE` refuses any run of four or more digits, which is right for an intake summary
    and wrong for a context one: this project renders dates in the **Buddhist era**, so an
    ordinary sentence about a loan taken in 30/08/2565 tripped it. Dates come out before
    the money check now — and the money check still has to fire.
    """
    box = container(client)
    dated = _CountingLlm("ลูกค้าเพิ่งมีสินเชื่อบ้านเมื่อ 30/08/2565 และเพิ่งมีบุตรเมื่อปี 2567")
    box.context_summariser = ContextSummariser(llm=dated, timeout_s=4.0)
    sign_in(client, "A003")
    call_id = place(client, caller_number="+66898887777", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    assert box.context_summaries.get(call_id) is not None, (
        "a summary quoting a Buddhist-era date was refused as though it stated money"
    )
    assert box.context_summariser.refused == 0


@pytest.mark.asyncio
async def test_a_real_money_figure_is_still_refused(client: Any) -> None:
    """The other half of the same guard. Loosening it for dates must not open it for
    amounts — a coverage figure is read from a record, never written by a model (`D16`)."""
    box = container(client)
    money = _CountingLlm("ลูกค้ามีสินเชื่อบ้านวงเงิน 3,500,000 บาท")
    box.context_summariser = ContextSummariser(llm=money, timeout_s=4.0)
    sign_in(client, "A003")
    call_id = place(client, caller_number="+66898887777", intent_code="general.renewal")[
        "call_session_id"
    ]
    go_ready(client)
    await container(client).dispatch.tick()
    await drain(client)

    assert box.context_summaries.get(call_id) is None, "a stated amount reached the screen"
    assert box.context_summariser.refused == 1
