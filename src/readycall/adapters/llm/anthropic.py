"""Anthropic adapter (`D29`).

Structured output through **tool use**, not through "please reply with JSON": the schema
is handed over as a tool definition and `tool_choice` forces it, so the model cannot
return prose that happens to start with a brace. Parsing free text and hoping is the
failure mode `D16` cares about most — a confident, plausible, wrong object.

The SDK is imported **at point of use**, the same shape as the STT adapters, because this
module is imported on a CI box that installs no LLM extra.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from readycall.clock import Clock, SystemClock
from readycall.errors import ConfigError, DegradedError
from readycall.logging import get_logger
from readycall.ports.llm import LlmResult, LlmUsage, PromptRef
from readycall.prompts import PromptLibrary

log = get_logger(__name__)

T = TypeVar("T", bound=BaseModel)

#: Published per-million-token prices, used only to log an order of magnitude so the
#: "is this economically sane at scale" question has an answer (`INTEGRATIONS` 3). Never
#: read for a decision, and deliberately coarse - a wrong invoice is the vendor's number,
#: not ours.
#:
#: ⚠️ **Corrected 2026-09-09 (`D130`), and the previous table was wrong in three ways at
#: once** - Opus at 15/75, Sonnet at 3/15, and a Haiku key carrying a date suffix, which
#: meant the ordinary model id `claude-haiku-4-5` matched **nothing** and silently priced
#: at `None`. That table produced `D119`'s published "$0.0085 per call", roughly 1.5x the
#: real figure. A price table is a claim about the world and it goes stale on the vendor's
#: schedule rather than on ours: **re-verify against live pricing documentation before
#: quoting any number from it**, which is the same rule `D30` set for model ids and `B17`
#: taught the hard way. Longest prefix wins, so a dated snapshot id cannot fall through to
#: a shorter family name at the wrong rate.
_PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-fable-5": (10.0, 50.0),
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-opus-4-7": (5.0, 25.0),
    "claude-opus-4-6": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    """Longest matching prefix, so `claude-opus-4-8` never prices as `claude-opus-4`."""
    match = max(
        (prefix for prefix in _PRICES_PER_MTOK if model.startswith(prefix)),
        key=len,
        default=None,
    )
    if match is None:
        return None
    rate_in, rate_out = _PRICES_PER_MTOK[match]
    return (tokens_in * rate_in + tokens_out * rate_out) / 1_000_000


class AnthropicLlm:
    """`LlmClient` over the Anthropic Messages API."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        prompts: PromptLibrary,
        clock: Clock | None = None,
        max_tokens: int = 1024,
    ) -> None:
        if not api_key:
            raise ConfigError("ANTHROPIC_API_KEY is required for LLM_PROVIDER=anthropic")
        self._api_key = api_key
        self._model = model
        self._prompts = prompts
        self._clock = clock or SystemClock()
        self._max_tokens = max_tokens
        self._client: Any = None

    @property
    def name(self) -> str:
        return "anthropic"

    @property
    def model(self) -> str:
        return self._model

    def _ensure(self) -> Any:
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
            except ImportError as exc:  # pragma: no cover - depends on the extra
                raise ConfigError(
                    "LLM_PROVIDER=anthropic needs the `llm` extra: `uv sync --extra llm`"
                ) from exc
            self._client = AsyncAnthropic(api_key=self._api_key)
        return self._client

    async def complete_structured(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        schema: type[T],
        *,
        timeout_s: float,
    ) -> LlmResult[T]:
        template = self._prompts.get(prompt)
        text = template.render(variables)
        client = self._ensure()
        started = self._clock.monotonic_ms()

        tool = {
            "name": "record",
            "description": f"Return the result as {schema.__name__}.",
            "input_schema": schema.model_json_schema(),
        }
        try:
            response = await client.messages.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": text}],
                tools=[tool],
                # Forced, not suggested. Without this the model may answer in prose and
                # the parse below becomes a guess.
                tool_choice={"type": "tool", "name": "record"},
                timeout=timeout_s,
            )
        except Exception as exc:
            # Every failure degrades rather than raising upward: the call is never blocked
            # on AI (`D12`), and the caller has a rule-based brief to fall back to.
            raise DegradedError(
                f"anthropic call failed: {type(exc).__name__}: {exc}",
                stage="llm",
                fallback="rule-based brief",
            ) from exc

        payload: dict[str, Any] | None = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use":
                payload = dict(block.input)
                break
        if payload is None:
            raise DegradedError(
                "anthropic returned no tool_use block",
                stage="llm",
                fallback="rule-based brief",
            )

        try:
            output = schema.model_validate(payload)
        except ValidationError as exc:
            raise DegradedError(
                f"anthropic output failed the schema: {exc}",
                stage="llm",
                fallback="rule-based brief",
            ) from exc

        tokens_in = int(getattr(response.usage, "input_tokens", 0) or 0)
        tokens_out = int(getattr(response.usage, "output_tokens", 0) or 0)
        usage = LlmUsage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=_estimate_cost(self._model, tokens_in, tokens_out),
            latency_ms=self._clock.monotonic_ms() - started,
            model=self._model,
            provider=self.name,
        )
        log.info(
            "llm call",
            prompt=template.key,
            provider=self.name,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=round(usage.latency_ms or 0.0, 1),
        )
        return LlmResult(output=output, usage=usage, raw=payload)

    async def stream_text(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        *,
        timeout_s: float,
    ) -> AsyncIterator[str]:
        template = self._prompts.get(prompt)
        client = self._ensure()
        try:
            async with client.messages.stream(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": template.render(variables)}],
                timeout=timeout_s,
            ) as stream:
                async for chunk in stream.text_stream:
                    yield chunk
        except Exception as exc:
            raise DegradedError(
                f"anthropic stream failed: {exc}", stage="llm", fallback="no live summary"
            ) from exc

    async def health_check(self) -> bool:
        try:
            self._ensure()
        except ConfigError:
            return False
        return True


__all__ = ["AnthropicLlm"]
