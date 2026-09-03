"""ThonburianHfEngine — the reference default (`D9`, `D30`), through HF transformers.

This is the model the team already proved on this hardware in their earlier project, so it
is the **known-good baseline the bake-off measures everything else against** — not
necessarily the one that ships. `D30` is explicit that the choice is made on WER, latency
and VRAM measured on the same audio, rather than on which one is familiar.

**It is the memory-hungry option**, and on a card with ~3.2 GiB free (`D95`) that is the
whole tension. fp16 is the default here because fp32 medium does not comfortably fit
alongside anything else; `FasterWhisperEngine` exists because even fp16 may not be enough.

What is deliberately *not* reused from the reference project (`D9`): the batch shape. That
one VAD-ed a whole file, wrote chunks to disk, and ran a pipeline over the directory. Here
the segment arrives already endpointed and in memory, and nothing touches the disk — the
audio is a caller's health information and it does not need a temp file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult

log = get_logger(__name__)

DEFAULT_MODEL = "biodatlab/whisper-th-medium-combined"


def _warmup_tone(seconds: float = 1.0) -> list[float]:
    """A tone, not silence — see `B14`. Whisper hallucinates on zeros, slowly."""
    import math

    return [0.2 * math.sin(2 * math.pi * 220 * i / 16000) for i in range(int(16000 * seconds))]


class ThonburianHfEngine:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        dtype: str = "float16",
        language: str = "th",
    ) -> None:
        self._model_name = model
        self._device = device
        self._dtype = dtype
        self._language = language
        self._pipe: Any = None
        self._np: Any = None
        self._warned_vocabulary = False

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(
            name="thonburian_hf",
            version=self._dtype,
            device=self._device,
            model=self._model_name,
            extra={"language": self._language},
        )

    async def warmup(self) -> None:
        if self._pipe is not None:  # pragma: no cover - the worker warms once
            return
        try:
            import numpy
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise ConfigError(
                "ThonburianHfEngine needs the `ml` extra: uv sync --extra ml"
            ) from exc
        self._np = numpy

        def load() -> Any:
            return pipeline(
                task="automatic-speech-recognition",
                model=self._model_name,
                torch_dtype=getattr(torch, self._dtype),
                device=self._device,
            )

        self._pipe = await asyncio.to_thread(load)
        log.info(
            "thonburian loaded",
            model=self._model_name,
            device=self._device,
            dtype=self._dtype,
        )
        await self.transcribe_utterance([AudioFrame(samples=_warmup_tone(), t_start_ms=0)])
        log.info("thonburian warm")

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        if self._pipe is None:
            await self.warmup()
        samples: list[float] = []
        for frame in frames:
            samples.extend(frame.samples)
        if not samples:
            return SttResult(text="", confidence=0.0, engine="thonburian_hf")

        t_start = frames[0].t_start_ms
        t_end = t_start + int(len(samples) / 16000 * 1000)
        audio = self._np.asarray(samples, dtype=self._np.float32)
        language = hint.language if hint else self._language
        if hint is not None and hint.vocabulary and not self._warned_vocabulary:
            # SAY SO rather than dropping it silently (`B19`). The port advertises the
            # vocabulary as a "domain nudge that measurably helps on insurance jargon",
            # `FasterWhisperEngine` honours it through `initial_prompt`, and this adapter
            # does not - it needs `processor.get_prompt_ids()` fed as `prompt_ids`, which
            # the pipeline API does not take directly.
            #
            # It was found by measuring: hint and no-hint produced CER identical to three
            # decimals on four files, which is not what "off-domain vocabulary does not
            # help" looks like. It is what "the argument is discarded" looks like.
            self._warned_vocabulary = True
            log.warning(
                "vocabulary hint IGNORED by this adapter - the number you are about to "
                "read is unhinted (`B19`)",
                terms=len(hint.vocabulary),
                engine="thonburian_hf",
            )

        def run() -> str:
            out = self._pipe(
                audio,
                generate_kwargs={"language": language, "task": "transcribe"},
            )
            return str(out.get("text", "")).strip()

        text = await asyncio.to_thread(run)
        return SttResult(
            text=text,
            # The HF pipeline does not return a usable per-utterance score, and inventing
            # one would be worse than admitting there is none: `D13` says a number on the
            # agent's screen has to mean something, and this one would not.
            confidence=None,
            t_start_ms=t_start,
            t_end_ms=t_end,
            engine="thonburian_hf",
            engine_version=f"{self._model_name}:{self._dtype}",
            is_final=True,
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        raise NotImplementedError(
            "Whisper is not streaming; use TranscriptionStream with transcribe_utterance"
        )
        yield  # type: ignore[unreachable]  # pragma: no cover - makes this a generator

    async def close(self) -> None:
        self._pipe = None


__all__ = ["DEFAULT_MODEL", "ThonburianHfEngine"]
