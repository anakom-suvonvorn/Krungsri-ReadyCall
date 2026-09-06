"""RuleBasedLlm - the degradation rung, and the P0 default.

Not a toy. This is what runs when the model is down, slow, or unconfigured, and the
brief it produces has to be genuinely useful rather than empty (`ARCHITECTURE.md`
section 16): intent from the DID/menu/tapped plan plus Thai keyword matching, entities
by pattern, and a template summary.

It is also the reason P0 needs no API key: the whole pipeline runs, and swapping in
`AnthropicAdapter` or `OpenAiCompatibleAdapter` at P4 changes one env var (`D29`).

Deliberately never produces a number that is not already in its input (`D16`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, TypeVar

from pydantic import BaseModel

from readycall.clock import Clock, SystemClock
from readycall.ports.llm import LlmResult, LlmUsage, PromptRef

T = TypeVar("T", bound=BaseModel)

#: Thai keyword -> intent code. The taxonomy lives in `config/intents.yaml` (`D117`);
#: this map is only the offline fallback's view of it, and it is deliberately partial -
#: anything it cannot match falls through to the menu's answer, which is the reliable one
#: (`D37`). Ordering matters: the first key whose keyword appears wins, so the specific
#: claim words sit above the generic ones.
#:
#: Two entries were briefly one after `D117`'s rename merged `health.ipd.preauth` and
#: `health.claim.submit` onto the same code, and a duplicate dict key silently discards
#: the first tuple - the admission words would have stopped matching with nothing to say
#: so. They are merged explicitly now.
KEYWORDS: dict[str, tuple[str, ...]] = {
    "motor.claim.notify": ("ชน", "อุบัติเหตุ", "เฉี่ยว", "รถชน"),
    "motor.roadside_assist": ("รถเสีย", "ยกรถ", "แบตหมด", "ยางแบน"),
    "health.claim.notify": ("นอนโรงพยาบาล", "แอดมิท", "ค่าห้อง", "ผู้ป่วยใน", "เคลม", "สินไหม", "เบิก"),
    # The broker's own work, which the old insurer-shaped map had no codes for at all.
    "health.advice.compare": ("เทียบ", "คุ้มครองพอ", "ประกันกลุ่ม", "ซื้อเพิ่ม"),
    "motor.advice.compare": ("เทียบเบี้ย", "ราคาประกันรถ", "ย้ายบริษัท"),
    "life.advice.mortgage": ("กู้บ้าน", "สินเชื่อบ้าน", "ผ่อนบ้าน"),
    "travel.advice.quote": ("เดินทาง", "ไปต่างประเทศ", "วีซ่า"),
    "general.advice.review": ("ทบทวน", "มีประกันอะไรบ้าง", "ดูทั้งหมด"),
    "general.renewal": ("ต่ออายุ", "หมดอายุ"),
    "general.billing": ("ชำระ", "จ่ายเบี้ย", "ใบเสร็จ"),
}


class RuleBasedLlm:
    """Keyword and template based. No network, no key, fully deterministic."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()
        self.calls: list[str] = []

    @property
    def name(self) -> str:
        return "rulebased"

    @property
    def model(self) -> str:
        return "rulebased-v1"

    def classify_intent(self, text: str, *, fallback: str = "unknown") -> tuple[str, float]:
        """Return (intent_code, confidence).

        Confidence is capped low on purpose. A keyword hit is weak evidence, and the
        workstation must show "intent unclear" rather than a confident wrong label
        when this is what produced it (`D13`).
        """
        hits: dict[str, int] = {}
        for code, words in KEYWORDS.items():
            count = sum(1 for w in words if w in text)
            if count:
                hits[code] = count
        if not hits:
            return fallback, 0.2 if fallback != "unknown" else 0.0
        best = max(hits.items(), key=lambda kv: kv[1])
        confidence = min(0.55, 0.3 + 0.1 * best[1])
        return best[0], confidence

    async def complete_structured(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        schema: type[T],
        *,
        timeout_s: float,
    ) -> LlmResult[T]:
        self.calls.append(prompt.key)
        stopwatch_start = self._clock.monotonic_ms()
        payload = self._build(prompt, variables, schema)
        usage = LlmUsage(
            latency_ms=self._clock.monotonic_ms() - stopwatch_start,
            model=self.model,
            provider=self.name,
        )
        return LlmResult(output=schema.model_validate(payload), usage=usage)

    def _build(
        self, prompt: PromptRef, variables: dict[str, Any], schema: type[T]
    ) -> dict[str, Any]:
        text = str(variables.get("transcript", ""))
        prior = str(variables.get("context_intent", "unknown"))
        fields = set(schema.model_fields)
        out: dict[str, Any] = {}

        if {"intent_code", "confidence"} <= fields:
            code, confidence = self.classify_intent(text, fallback=prior)
            out["intent_code"] = code
            out["confidence"] = confidence
            if "label_th" in fields:
                out["label_th"] = code
            if "alternatives" in fields:
                out["alternatives"] = []
        if "summary_th" in fields:
            # Quote, never paraphrase: a template cannot hallucinate.
            snippet = text.strip()[:180]
            out["summary_th"] = (
                f"(สรุปอัตโนมัติแบบไม่ใช้ AI) ลูกค้าแจ้งว่า: {snippet}" if snippet else "(ไม่มีข้อมูลเสียงจากลูกค้า)"
            )
        for name, field_info in schema.model_fields.items():
            if name not in out and field_info.is_required():
                out[name] = _empty_for(field_info.annotation)
        return out

    async def stream_text(
        self,
        prompt: PromptRef,
        variables: dict[str, Any],
        *,
        timeout_s: float,
    ) -> AsyncIterator[str]:
        self.calls.append(prompt.key)
        yield str(variables.get("transcript", ""))[:180]

    async def health_check(self) -> bool:
        return True


def _empty_for(annotation: Any) -> Any:
    origin = getattr(annotation, "__origin__", None)
    if annotation is str:
        return ""
    if annotation is int:
        return 0
    if annotation is float:
        return 0.0
    if annotation is bool:
        return False
    if origin in (list, tuple) or annotation in (list, tuple):
        return []
    if origin is dict or annotation is dict:
        return {}
    return None
