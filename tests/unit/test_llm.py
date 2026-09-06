"""The LLM seam: the factory, the prompt library, and the summary that must never block.

`D119`. Until this landed, `Settings.llm_provider` accepted `anthropic` and
`openai_compatible`, the startup check validated their keys, and **nothing behind either
name existed** — `B23`'s shape. `RuleBasedLlm` was written, correct, and constructed
nowhere, which is `B7`'s from the other end.

Nothing here needs a key or a network. The two real adapters are exercised through their
seams only; a live provider is `scripts/compare_llm.py`'s job, not the suite's.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from readycall.config import LlmProviderName, Settings
from readycall.errors import ConfigError, DegradedError
from readycall.ports.llm import LlmClient, LlmResult, LlmUsage, PromptRef
from readycall.prompts import PromptLibrary
from readycall.services.analysis import IntakeSummariser
from tests.conftest import REPO_ROOT


@pytest.fixture
def library() -> PromptLibrary:
    return PromptLibrary.load(REPO_ROOT / "prompts" / "th")


def settings(**kw: Any) -> Settings:
    return Settings(
        config_dir=REPO_ROOT / "config",
        core_fixtures_dir=REPO_ROOT / "mock" / "bank_core" / "fixtures",
        prompt_dir=REPO_ROOT / "prompts" / "th",
        **kw,
    )


# --- the factory ---------------------------------------------------------------------


def test_the_default_provider_is_a_real_adapter_not_a_stub(library: PromptLibrary) -> None:
    """`D3`: the whole system runs with no key, no network and no GPU.

    `rulebased` is the rung everything else degrades to (`D12`), so it has to be a real
    implementation of the port rather than something that raises "not configured".
    """
    from readycall.adapters.llm import build_llm

    client = build_llm(settings(), prompts=library)

    assert isinstance(client, LlmClient), "it must satisfy the port, not merely exist"
    assert client.name == "rulebased"


def test_every_provider_name_settings_accepts_can_actually_be_built(
    library: PromptLibrary,
) -> None:
    """`B23`: a config surface that cannot select anything is worse than no surface.

    This is the test that would have failed for the six phases when `LLM_PROVIDER` named
    two adapters that did not exist.
    """
    from readycall.adapters.llm import build_llm

    built = {
        LlmProviderName.RULEBASED: build_llm(settings(), prompts=library),
        LlmProviderName.ANTHROPIC: build_llm(
            settings(llm_provider=LlmProviderName.ANTHROPIC, anthropic_api_key="k-test"),
            prompts=library,
        ),
        LlmProviderName.OPENAI_COMPATIBLE: build_llm(
            settings(
                llm_provider=LlmProviderName.OPENAI_COMPATIBLE,
                llm_base_url="http://127.0.0.1:11434/v1",
            ),
            prompts=library,
        ),
    }
    for name, client in built.items():
        assert isinstance(client, LlmClient), f"{name} does not satisfy the port"
    assert len({c.name for c in built.values()}) == 3, "three distinct providers"


def test_building_anthropic_without_a_key_is_refused_at_construction(
    library: PromptLibrary,
) -> None:
    """Loudly, and before a call. A missing key discovered mid-call is a degraded brief
    for a customer instead of a startup error for us."""
    from readycall.adapters.llm.anthropic import AnthropicLlm

    with pytest.raises(ConfigError):
        AnthropicLlm(api_key="", model="claude-sonnet-5", prompts=library)


# --- the prompt library --------------------------------------------------------------


def test_prompts_are_loaded_and_carry_their_version(library: PromptLibrary) -> None:
    """`D18`: the version is part of the identity, because it is stored against results."""
    template = library.get(PromptRef(id="summarize_intake", version="v1"))

    assert template.version == "v1"
    assert template.key == "summarize_intake.v1"
    assert "transcript" in template.variables


def test_a_prompt_refuses_to_render_with_a_missing_variable(library: PromptLibrary) -> None:
    """The failure this prevents is silent: a prompt that quietly loses its transcript
    still returns a confident-looking summary of nothing (`D16`)."""
    template = library.get(PromptRef(id="summarize_intake", version="v1"))

    with pytest.raises(ConfigError, match="missing variables"):
        template.render({"intent_label_th": "x", "line_label_th": "y"})


def test_a_prompt_refuses_variables_it_never_declared(library: PromptLibrary) -> None:
    """A typo'd key that happens to exist renders the wrong thing and nobody ever sees it."""
    template = library.get(PromptRef(id="summarize_intake", version="v1"))

    with pytest.raises(ConfigError, match="undeclared"):
        template.render(
            {
                "transcript": "a",
                "intent_label_th": "b",
                "line_label_th": "c",
                "transcrpit": "typo",
            }
        )


def test_asking_for_a_prompt_that_does_not_exist_names_the_ones_that_do(
    library: PromptLibrary,
) -> None:
    with pytest.raises(ConfigError, match="summarize_intake"):
        library.get(PromptRef(id="nope", version="v1"))


# --- the summary, and D12 ------------------------------------------------------------


class _Fake:
    """An `LlmClient` that answers however a test needs it to."""

    def __init__(self, payload: dict[str, Any] | None = None, *, behaviour: str = "ok") -> None:
        self._payload = payload or {"summary_th": "ลูกค้าแจ้งว่าจะเข้ารักษาพรุ่งนี้", "is_clear": True}
        self._behaviour = behaviour
        self.calls = 0

    @property
    def name(self) -> str:
        return "fake"

    @property
    def model(self) -> str:
        return "fake-1"

    async def complete_structured(
        self, prompt: PromptRef, variables: dict[str, Any], schema: type[Any], *, timeout_s: float
    ) -> LlmResult[Any]:
        self.calls += 1
        if self._behaviour == "hang":
            await asyncio.sleep(60)
        if self._behaviour == "degraded":
            raise DegradedError("provider down", stage="llm", fallback="rule-based brief")
        if self._behaviour == "explode":
            raise RuntimeError("something nobody predicted")
        return LlmResult(
            output=schema.model_validate(self._payload),
            usage=LlmUsage(model=self.model, provider=self.name, latency_ms=1.0),
        )

    async def stream_text(self, *a: Any, **kw: Any) -> Any:  # pragma: no cover - unused here
        raise NotImplementedError

    async def health_check(self) -> bool:
        return True


def _summariser(fake: _Fake, **kw: Any) -> IntakeSummariser:
    return IntakeSummariser(llm=fake, **kw)


SAID = ["สวัสดีครับ ผมจะเข้ารักษาที่โรงพยาบาลพรุ่งนี้", "อยากทราบว่าต้องเตรียมเอกสารอะไรบ้าง"]


@pytest.mark.asyncio
async def test_a_summary_comes_back_when_the_model_answers() -> None:
    fake = _Fake()
    result = await _summariser(fake).summarise(
        SAID, intent_label_th="แจ้งเคลม", line_label_th="health"
    )

    assert result is not None
    assert result.text_th
    assert result.provider == "fake"
    assert result.prompt_version == "v1", "the version must travel with the text (`D18`)"


@pytest.mark.asyncio
async def test_a_model_that_hangs_does_not_hang_the_call() -> None:
    """`D12`, at the one moment it is tempting to break it.

    The agent is already connected when this runs. A summariser that waited for a wedged
    provider would hold whatever awaited it — so it must come back on its own deadline
    with nothing, and the rule-based summary stays on the screen.
    """
    fake = _Fake(behaviour="hang")
    summariser = _summariser(fake, timeout_s=0.05)

    result = await asyncio.wait_for(
        summariser.summarise(SAID, intent_label_th="แจ้งเคลม", line_label_th="health"),
        timeout=5.0,
    )

    assert result is None
    assert summariser.failed == 1
    assert fake.calls == 1, "and it really did try, rather than skipping on a guess"


@pytest.mark.asyncio
@pytest.mark.parametrize("behaviour", ["degraded", "explode"])
async def test_no_provider_failure_of_any_kind_reaches_the_caller(behaviour: str) -> None:
    """Including one nobody predicted. An AI stage may never take the call down (`D12`)."""
    summariser = _summariser(_Fake(behaviour=behaviour))

    result = await summariser.summarise(SAID, intent_label_th="แจ้งเคลม", line_label_th="health")

    assert result is None
    assert summariser.failed == 1


@pytest.mark.asyncio
async def test_a_summary_that_states_a_figure_is_refused() -> None:
    """`D16`, and the guard exists because the prompt will one day fail.

    Coverage amounts are read from the record. A model that writes "คุ้มครองค่าห้อง 3,000
    บาท" has produced exactly the plausible, credible-looking number that makes a brief
    dangerous — and the caller may never have said it.
    """
    fake = _Fake({"summary_th": "ลูกค้าถามเรื่องค่าห้อง คุ้มครองสูงสุด 3,000 บาทต่อวัน", "is_clear": True})
    summariser = _summariser(fake)

    result = await summariser.summarise(SAID, intent_label_th="แจ้งเคลม", line_label_th="health")

    assert result is None, "a stated figure is refused outright"
    assert summariser.refused == 1


@pytest.mark.asyncio
async def test_a_model_that_says_it_could_not_tell_is_believed() -> None:
    """A model allowed to say "unclear" invents fewer things than one that must always
    produce three sentences (`D13`)."""
    summariser = _summariser(_Fake({"summary_th": "ไม่ชัดเจน", "is_clear": False}))

    assert await summariser.summarise(SAID, intent_label_th="x", line_label_th="y") is None
    assert summariser.refused == 1


@pytest.mark.asyncio
async def test_a_caller_who_barely_spoke_is_not_summarised_at_all() -> None:
    """Not a failure, and the model is not even asked.

    Most callers who take the recording then wait quietly. Summarising two syllables
    produces a sentence that *sounds* like information, which is the same distinction
    `D111` drew for the degradation reason.
    """
    fake = _Fake()
    summariser = _summariser(fake)

    assert await summariser.summarise(["ครับ"], intent_label_th="x", line_label_th="y") is None
    assert fake.calls == 0, "no provider call, so no cost and no latency"
    assert summariser.failed == 0, "and it is not recorded as a failure, because it is not one"
