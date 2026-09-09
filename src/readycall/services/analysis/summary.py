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
    #: False while this was built from a **partial** transcript — the preview that runs
    #: during the offer window, before the caller has finished talking (`D131`). The
    #: caller keeps speaking through the whole offer window by design (`D21`), so a
    #: preview is a summary of the first part of a call and the screen says so.
    #:
    #: Set by the caller rather than in here, because this service does not know whether
    #: the transcript it was handed is finished — only the thing driving it does. Same
    #: reason `degraded` is passed *into* `IntakeStrategy.finalize` rather than inferred
    #: (`D88`): a component that cannot observe a fact must not assert it.
    is_final: bool = True


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

    def with_timeout(self, timeout_s: float) -> IntakeSummariser:
        """The same client and clock, a different deadline (`D137`).

        For the startup warm-up, which is background work nobody waits for and therefore
        wants a far more generous deadline than a preview racing the Accept button. A new
        instance rather than a mutation, because the caller's summariser is the live one
        every call uses — widening its deadline for a warm-up would widen it for the offer
        card too, which is the opposite of what `LLM_PREVIEW_TIMEOUT_S` is for.
        """
        return IntakeSummariser(
            llm=self._llm,
            clock=self._clock,
            timeout_s=timeout_s,
            min_characters=self._min_characters,
        )

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


class ContextSummary(BaseModel):
    """What the model may return about what we already know (`D134`)."""

    summary_th: str = Field(min_length=1, max_length=600)
    #: The model's own view of whether there was enough to be worth a sentence. Believed,
    #: for `D13`'s reason: a model allowed to say "not much here" invents less than one
    #: obliged to produce three sentences about two rows.
    is_clear: bool = True


#: Words that turn a description into a recommendation. `D116` moves the consent gate from
#: HOLDING data to RECOMMENDING from it, so this panel may say what we know and may not say
#: what to sell — and the prompt forbidding it is not enough on its own, for exactly the
#: reason `_FIGURE` exists: the guard assumes the prompt will one day fail.
_RECOMMENDS = re.compile(
    r"ควรซื้อ|ควรทำประกัน|น่าจะสนใจ|แนะนำให้ซื้อ|เสนอขาย|ควรเสนอ|เหมาะกับแผน|ควรพิจารณาซื้อ"
)

#: ⚠️ **A DATE IS NOT A FIGURE**, and reusing `_FIGURE` unchanged here refused a correct
#: summary on its first live run.
#:
#: `_FIGURE` matches any run of four or more digits, which is right for the intake summary
#: — a caller rarely says a bare four-digit number that is not money. A *context* summary
#: is mostly dates, and this project renders them in the **Buddhist era**, so an ordinary
#: sentence about a home loan taken in `30/08/2565` trips it. Measured: `gpt-5.4-mini`
#: produced a faithful summary and the guard threw it away.
#:
#: So dates are removed **before** the money check runs, rather than the money check being
#: loosened. Narrow on purpose: `2565` alone is a year, `2,565,000` is not, and the second
#: still has to be refused.
_DATE_LIKE = re.compile(
    r"\d{1,2}/\d{1,2}/\d{4}"  # 30/08/2565, as `context_facts` renders them
    r"|(?<![\d,])(?:19|20|24|25|26)\d{2}(?![\d,])"  # a bare CE or BE year
)


class ContextSummariser:
    """Two or three Thai sentences over what the bank already holds (`D134`).

    Same shape and same three inherited rules as `IntakeSummariser`, over different input:
    that one summarises what the caller **said**, this one summarises what we **knew before
    they called**. Kept as its own class rather than a mode on the other because the guards
    differ — this one must also refuse to recommend (`D116`) — and a boolean argument that
    switches which safety check runs is how one of them eventually stops running.
    """

    #: `v2` since `D135`. `v1` shipped with `D134` and was measured over 6 live runs: it
    #: led with a conversation-history line every time and never with the life events, one
    #: run dropped the mortgage entirely, and one asserted that a renewal record was *not
    #: found* — a claim about data this prompt is never given. `v1` stays on disk because
    #: `analyses.prompt_version` names the file a result came from (`D18`).
    PROMPT = PromptRef(id="summarize_context", version="v2")

    def __init__(
        self,
        *,
        llm: LlmClient,
        clock: Clock | None = None,
        timeout_s: float = 8.0,
        min_facts: int = 2,
    ) -> None:
        self._llm = llm
        self._clock = clock or SystemClock()
        self._timeout_s = timeout_s
        #: Below this, say nothing. One holding and no history is not a paragraph, and a
        #: model asked to write one produces something that sounds like insight.
        self._min_facts = min_facts
        self.attempted = 0
        self.refused = 0
        self.failed = 0

    async def summarise(
        self,
        facts_text: str,
        *,
        fact_count: int,
        customer_name_th: str,
        intent_label_th: str,
    ) -> SummaryResult | None:
        if fact_count < self._min_facts or not facts_text.strip():
            return None

        self.attempted += 1
        try:
            result = await asyncio.wait_for(
                self._llm.complete_structured(
                    self.PROMPT,
                    {
                        "facts": facts_text,
                        "customer_name_th": customer_name_th,
                        "intent_label_th": intent_label_th,
                    },
                    ContextSummary,
                    timeout_s=self._timeout_s,
                ),
                timeout=self._timeout_s + 1.0,
            )
        except (DegradedError, TimeoutError) as exc:
            self.failed += 1
            log.warning("context summary unavailable", error=str(exc)[:200])
            return None
        except Exception as exc:
            self.failed += 1
            log.warning("context summary raised", error=f"{type(exc).__name__}: {exc}"[:200])
            return None

        output = result.output
        if not output.is_clear:
            self.refused += 1
            return None
        text = output.summary_th.strip()
        # `D16` still binds — a coverage amount is read from a record and never written by
        # a model — but dates come out first, or every sentence mentioning a Buddhist year
        # is refused as though it had quoted a premium.
        if _FIGURE.search(_DATE_LIKE.sub(" ", text)):
            self.refused += 1
            log.warning("context summary refused: it stated a figure", sample=text[:120])
            return None
        if _RECOMMENDS.search(text):
            # `D116`. Describing what we hold is internal processing; recommending from it
            # needs consent this panel does not have.
            self.refused += 1
            log.warning("context summary refused: it recommended a product", sample=text[:120])
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


__all__ = [
    "ContextSummariser",
    "ContextSummary",
    "IntakeSummariser",
    "IntakeSummary",
    "SummaryResult",
]
