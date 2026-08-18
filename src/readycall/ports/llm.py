"""LlmClient port.

Plain SDK calls behind a Protocol — no LangChain, no agent framework (`D31`). Our AI
stage is four to six short, independent, individually-timed-out structured calls, and
we are required to persist the exact prompt version, output, latency, tokens and cost
for each one (`D18`). A layer that hides the request would be fighting us.

Two invariants every adapter must respect:

* **Structured output only** — a Pydantic schema in, a validated instance out. Free
  text is only allowed where a human reads it (summary, suggested opening).
* **Timeouts are the caller's, not the vendor's.** On breach we degrade to a lesser
  brief rather than making the customer wait (`D12`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class PromptRef:
    """Prompts live in versioned files, never inline in code (`D31`)."""

    id: str  # e.g. "intent_classify"
    version: str  # e.g. "v1"

    @property
    def key(self) -> str:
        return f"{self.id}.{self.version}"


@dataclass(frozen=True, slots=True)
class LlmUsage:
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None
    latency_ms: float | None = None
    model: str = "unknown"
    provider: str = "unknown"
    cached: bool = False


@dataclass(frozen=True, slots=True)
class LlmResult(Generic[T]):
    """The parsed output plus everything we must account for."""

    output: T
    usage: LlmUsage
    raw: dict[str, Any] = field(default_factory=dict)


@runtime_checkable
class LlmClient(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def model(self) -> str: ...

    async def complete_structured(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        schema: type[T],
        *,
        timeout_s: float,
    ) -> LlmResult[T]:
        """Render the prompt, call the model, validate against `schema`.

        Raises `DegradedError` on timeout or unrecoverable failure so the caller can
        fall back (rule-based brief) instead of blocking the call.
        """
        ...

    def stream_text(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        *,
        timeout_s: float,
    ) -> AsyncIterator[str]:
        """Token stream, for the summary appearing on the workstation as it is written."""
        ...

    async def health_check(self) -> bool: ...
