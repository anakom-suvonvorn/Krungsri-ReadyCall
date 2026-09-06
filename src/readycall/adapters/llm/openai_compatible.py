"""One adapter, five back ends (`D29`).

Typhoon's hosted API, OpenAI, vLLM, Ollama and LM Studio all speak the OpenAI wire
format, so the only thing that differs is `base_url` and whether a key is needed at all.
That is why this is one file rather than five: the comparison the pitch promises —
Claude against a Thai-native model, measured — costs one adapter, not a project.

Structured output uses **JSON schema response format** where the server supports it, and
falls back to a schema-in-the-prompt instruction where it does not (Ollama and older vLLM
builds). The fallback is marked in the result so a comparison table can say which
mechanism produced a number, because "the model was worse" and "the server could not
constrain it" are different findings.
"""

from __future__ import annotations

import json
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


class OpenAiCompatibleLlm:
    """`LlmClient` over anything that speaks the OpenAI chat-completions format."""

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str | None,
        model: str,
        prompts: PromptLibrary,
        clock: Clock | None = None,
        max_tokens: int = 1024,
        use_json_schema: bool = True,
    ) -> None:
        if not base_url:
            raise ConfigError("LLM_BASE_URL is required for LLM_PROVIDER=openai_compatible")
        self._base_url = base_url
        # Ollama and LM Studio need no key and reject an empty string, so a placeholder is
        # the documented convention rather than an oversight.
        self._api_key = api_key or "not-needed"
        self._model = model
        self._prompts = prompts
        self._clock = clock or SystemClock()
        self._max_tokens = max_tokens
        self._use_json_schema = use_json_schema
        self._client: Any = None

    @property
    def name(self) -> str:
        return "openai_compatible"

    @property
    def model(self) -> str:
        return self._model

    def _ensure(self) -> Any:
        if self._client is None:
            try:
                from openai import AsyncOpenAI
            except ImportError as exc:  # pragma: no cover - depends on the extra
                raise ConfigError(
                    "LLM_PROVIDER=openai_compatible needs the `llm` extra: `uv sync --extra llm`"
                ) from exc
            self._client = AsyncOpenAI(api_key=self._api_key, base_url=self._base_url)
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

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": self._max_tokens,
            "messages": [{"role": "user", "content": text}],
            "timeout": timeout_s,
        }
        constrained = self._use_json_schema
        if constrained:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "schema": schema.model_json_schema(),
                    "strict": False,
                },
            }
        else:
            kwargs["messages"] = [
                {
                    "role": "user",
                    "content": (
                        f"{text}\n\nตอบกลับเป็น JSON เท่านั้น ตามโครงสร้างนี้:\n"
                        f"{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
                    ),
                }
            ]

        try:
            response = await client.chat.completions.create(**kwargs)
        except Exception as exc:
            raise DegradedError(
                f"{self._base_url} call failed: {type(exc).__name__}: {exc}",
                stage="llm",
                fallback="rule-based brief",
            ) from exc

        content = (response.choices[0].message.content or "").strip()
        if not content:
            raise DegradedError(
                "openai-compatible server returned an empty message",
                stage="llm",
                fallback="rule-based brief",
            )
        try:
            payload = json.loads(_strip_code_fence(content))
        except json.JSONDecodeError as exc:
            raise DegradedError(
                f"openai-compatible output was not JSON: {content[:200]!r}",
                stage="llm",
                fallback="rule-based brief",
            ) from exc
        try:
            output = schema.model_validate(payload)
        except ValidationError as exc:
            raise DegradedError(
                f"openai-compatible output failed the schema: {exc}",
                stage="llm",
                fallback="rule-based brief",
            ) from exc

        usage_obj = getattr(response, "usage", None)
        tokens_in = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        tokens_out = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        usage = LlmUsage(
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            # Deliberately None: a self-hosted model has no per-token price, and inventing
            # one would put a fabricated figure in a cost comparison.
            cost_usd=None,
            latency_ms=self._clock.monotonic_ms() - started,
            model=self._model,
            provider=self.name,
        )
        log.info(
            "llm call",
            prompt=template.key,
            provider=self.name,
            model=self._model,
            base_url=self._base_url,
            constrained=constrained,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            latency_ms=round(usage.latency_ms or 0.0, 1),
        )
        return LlmResult(
            output=output, usage=usage, raw={"payload": payload, "constrained": constrained}
        )

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
            stream = await client.chat.completions.create(
                model=self._model,
                max_tokens=self._max_tokens,
                messages=[{"role": "user", "content": template.render(variables)}],
                timeout=timeout_s,
                stream=True,
            )
            async for chunk in stream:
                piece = chunk.choices[0].delta.content
                if piece:
                    yield piece
        except Exception as exc:
            raise DegradedError(
                f"openai-compatible stream failed: {exc}",
                stage="llm",
                fallback="no live summary",
            ) from exc

    async def health_check(self) -> bool:
        try:
            self._ensure()
        except ConfigError:
            return False
        return True


def _strip_code_fence(text: str) -> str:
    """Models fence JSON in ```json blocks even when told not to. Cheap to survive."""
    if not text.startswith("```"):
        return text
    body = text.split("\n", 1)[-1]
    return body.rsplit("```", 1)[0].strip()


__all__ = ["OpenAiCompatibleLlm"]
