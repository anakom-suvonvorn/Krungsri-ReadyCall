"""The AI summary of what the caller said while they waited (`D119`).

The first thing in this project that asks a model to write a sentence a human will read,
and the rules around it come from decisions made long before there was a model to break
them:

* **`D12` — the call is never blocked on AI.** `summarise()` takes a deadline and returns
  `None` on breach, on failure, and on an unconfigured provider. Every caller must be
  correct when it returns `None`, because that is the ordinary case on a laptop with no
  key.
* **`D16` — no number the model produced ever reaches the screen.** The prompt forbids
  coverage figures, and `_FIGURE` refuses the output if one appears
  anyway. Coverage amounts are read from the record; the model may reference them
  and may not state them.
* **`D13` — a confidently wrong brief is worse than no brief.** A refused summary leaves
  the rule-based one in place, which quotes the caller verbatim and cannot hallucinate.

It writes nothing and owns nothing. The result goes back to the caller, which keeps this
service out of the accept path's dependency graph and makes it trivially skippable.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from dataclasses import dataclass

from pydantic import BaseModel, Field

from readycall.clock import Clock, SystemClock
from readycall.errors import DegradedError
from readycall.logging import get_logger
from readycall.ports.llm import LlmClient, PromptRef

log = get_logger(__name__)

#: A Thai or Arabic-numeral money figure: four or more digits, or any digit run followed
#: by a currency word. Deliberately broad — a false refusal costs a nicer sentence, a
#: false accept puts an invented coverage limit in front of a customer (`D16`).
_FIGURE = re.compile(r"\d[\d,]{3,}|\d+\s*(?:บาท|ล้าน|แสน|หมื่น|พัน|%|เปอร์เซ็นต์)")


class IntakeSummary(BaseModel):
    """What the model is allowed to return. Nothing else is read."""

    summary_th: str = Field(min_length=1, max_length=600)
    #: The model's own view of whether the caller said enough to summarise. A model that
    #: can say "they were unclear" produces fewer confident fictions than one that must
    #: always produce three sentences.
    is_clear: bool = True


@dataclass(frozen=True, slots=True)
class SummaryResult:
    text_th: str
    provider: str
    model: str
    prompt_version: str
    latency_ms: float | None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None


class IntakeSummariser:
    """Turns a call's transcript into two or three Thai sentences, or into nothing."""

    PROMPT = PromptRef(id="summarize_intake", version="v1")

    def __init__(
        self,
        *,
        llm: LlmClient,
        clock: Clock | None = None,
        timeout_s: float = 8.0,
        min_characters: int = 12,
    ) -> None:
        self._llm = llm
        self._clock = clock or SystemClock()
        self._timeout_s = timeout_s
        self._min_characters = min_characters
        #: Counters a test can assert on without reaching into the logger.
        self.attempted = 0
        self.refused = 0
        self.failed = 0

    async def summarise(
        self,
        texts: Sequence[str],
        *,
        intent_label_th: str,
        line_label_th: str,
    ) -> SummaryResult | None:
        """`texts` is what the caller said, in order — plain strings, not turn objects.

        The live transcript is held as wire dicts and the durable one as ORM rows; taking
        strings means this service does not have to know which, and cannot be broken by
        either changing shape.
        """
        transcript = " ".join(t.strip() for t in texts if t and t.strip()).strip()
        if len(transcript) < self._min_characters:
            # Not a failure. Most callers who take the recording then wait quietly, and
            # summarising two syllables produces a sentence that sounds like information
            # (`D111` made the same distinction for the degradation reason).
            return None

        self.attempted += 1
        try:
            result = await asyncio.wait_for(
                self._llm.complete_structured(
                    self.PROMPT,
                    {
                        "transcript": transcript,
                        "intent_label_th": intent_label_th,
                        "line_label_th": line_label_th,
                    },
                    IntakeSummary,
                    timeout_s=self._timeout_s,
                ),
                # Belt and braces: the adapter is given the deadline AND the whole
                # exchange is bounded, because a vendor SDK that ignores its own timeout
                # would otherwise hold the accept path (`D12`, and `D112`'s argument for
                # covering the send as well as the reply).
                timeout=self._timeout_s + 1.0,
            )
        except (DegradedError, TimeoutError) as exc:
            self.failed += 1
            log.warning("intake summary unavailable", error=str(exc)[:200], provider=self._llm.name)
            return None
        except Exception as exc:
            self.failed += 1
            log.warning("intake summary raised", error=f"{type(exc).__name__}: {exc}"[:200])
            return None

        output = result.output
        if not output.is_clear:
            self.refused += 1
            log.info("intake summary declined by the model as unclear")
            return None
        text = output.summary_th.strip()
        if _FIGURE.search(text):
            # `D16`. The prompt forbids this and the guard assumes the prompt will one day
            # fail: an invented coverage limit is exactly the kind of plausible wrong
            # number that makes a brief look credible.
            self.refused += 1
            log.warning("intake summary refused: it stated a figure", sample=text[:120])
            return None

        return SummaryResult(
            text_th=text,
            provider=result.usage.provider,
            model=result.usage.model,
            prompt_version=self.PROMPT.version,
            latency_ms=result.usage.latency_ms,
            tokens_in=result.usage.tokens_in,
            tokens_out=result.usage.tokens_out,
            cost_usd=result.usage.cost_usd,
        )


__all__ = ["IntakeSummariser", "IntakeSummary", "SummaryResult"]
