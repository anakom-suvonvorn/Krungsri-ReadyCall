"""LLM adapter selection (`D29`, `D119`).

`build_llm` is the only place an `LlmClient` may be constructed — the same enforcement
point `build_blob_storage` is for the crypto wrapper, and for the same reason: the choice
is a config decision that must not be made at a call site.

Until 2026-09-07 there was **no factory at all**. `Settings.llm_provider` accepted
`anthropic` and `openai_compatible`, the startup check even validated their keys, and
nothing behind either name existed — `B23`'s shape, a config surface that cannot select
anything. `RuleBasedLlm` was written, correct, and instantiated nowhere, which is `B7`'s
family from the other end.
"""

from __future__ import annotations

from readycall.clock import Clock
from readycall.config import LlmProviderName, Settings
from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.llm import LlmClient
from readycall.prompts import PromptLibrary

log = get_logger(__name__)


def build_llm(
    settings: Settings,
    *,
    prompts: PromptLibrary | None = None,
    clock: Clock | None = None,
) -> LlmClient:
    """Pick the client named by `LLM_PROVIDER`.

    The rule-based one is a real adapter and the shipped default, not a stub: it needs no
    key, no network and no extra, which is what keeps the whole system runnable on a
    strange laptop (`D3`) and is the rung every other provider degrades to (`D12`).
    """
    library = prompts if prompts is not None else PromptLibrary.load(settings.prompt_dir)

    if settings.llm_provider is LlmProviderName.RULEBASED:
        from readycall.adapters.llm.rulebased import RuleBasedLlm

        return RuleBasedLlm(clock=clock)

    if settings.llm_provider is LlmProviderName.ANTHROPIC:
        from readycall.adapters.llm.anthropic import AnthropicLlm

        return AnthropicLlm(
            api_key=settings.anthropic_api_key or "",
            model=settings.llm_model,
            prompts=library,
            clock=clock,
        )

    if settings.llm_provider is LlmProviderName.OPENAI_COMPATIBLE:
        from readycall.adapters.llm.openai_compatible import OpenAiCompatibleLlm

        return OpenAiCompatibleLlm(
            base_url=settings.llm_base_url or "",
            api_key=settings.llm_api_key,
            model=settings.llm_model,
            prompts=library,
            clock=clock,
        )

    raise ConfigError(
        f"LLM_PROVIDER={settings.llm_provider.value} has no adapter. `GeminiAdapter` is "
        "defined in `D29` and not built; everything else the ecosystem offers speaks the "
        "OpenAI wire format, so `openai_compatible` with a base URL is usually the answer."
    )


__all__ = ["build_llm"]
