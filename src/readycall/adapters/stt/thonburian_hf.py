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

    async def transcribe_batch(
        self,
        utterances: Sequence[Sequence[AudioFrame]],
        *,
        hint: SttHint | None = None,
    ) -> list[SttResult]:
        """Several utterances through the GPU in one pass (`D101`, `BatchSttEngine`).

        This is the mechanism behind the team's earlier Thai project being fast: it passed
        `batch_size=4` to this same pipeline. Whisper encodes a fixed 30-second window per
        clip whatever its length, so four short utterances cost four full windows either
        way — batching overlaps them on the GPU rather than paying for them one after the
        other.

        **The empty-utterance case is handled by position, not by filtering.** Dropping an
        empty one before the call and appending the results back would silently shift every
        later result onto the wrong segment, which is `B20`'s failure — a fluent sentence
        attributed to the wrong moment — reappearing in a new place. Empties get a
        placeholder result so the returned list stays index-for-index with the input.
        """
        if self._pipe is None:
            await self.warmup()
        language = hint.language if hint else self._language
        self._warn_about_unused_vocabulary(hint)

        audios: list[object] = []
        bounds: list[tuple[int, int]] = []
        keep: list[int] = []
        for index, frames in enumerate(utterances):
            samples: list[float] = []
            for frame in frames:
                samples.extend(frame.samples)
            if not samples:
                bounds.append((0, 0))
                continue
            t_start = frames[0].t_start_ms
            bounds.append((t_start, t_start + int(len(samples) / 16000 * 1000)))
            audios.append(self._np.asarray(samples, dtype=self._np.float32))
            keep.append(index)

        texts: dict[int, str] = {}
        if audios:

            def run() -> list[str]:
                out = self._pipe(
                    audios,
                    batch_size=len(audios),
                    generate_kwargs={"language": language, "task": "transcribe"},
                )
                # The pipeline returns a list when handed a list, and a bare dict when
                # handed one array. Normalised rather than assumed.
                items = out if isinstance(out, list) else [out]
                return [str(item.get("text", "")).strip() for item in items]

            produced = await asyncio.to_thread(run)
            for index, text in zip(keep, produced, strict=False):
                texts[index] = text

        results: list[SttResult] = []
        for index in range(len(utterances)):
            t_start, t_end = bounds[index]
            results.append(
                SttResult(
                    text=texts.get(index, ""),
                    confidence=None if index in texts else 0.0,
                    t_start_ms=t_start,
                    t_end_ms=t_end,
                    engine="thonburian_hf",
                    engine_version=self._model_name,
                    is_final=True,
                )
            )
        return results

    def _warn_about_unused_vocabulary(self, hint: SttHint | None) -> None:
        """`B19`, factored out so the batch path cannot forget to say it."""
        if hint is None or not hint.vocabulary or self._warned_vocabulary:
            return
        self._warned_vocabulary = True
        log.warning(
            "vocabulary hint IGNORED by this adapter - the number you are about to "
            "read is unhinted (`B19`)",
            terms=len(hint.vocabulary),
            engine="thonburian_hf",
        )

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
        # SAY SO rather than dropping it silently (`B19`). The port advertises the
        # vocabulary as a "domain nudge that measurably helps on insurance jargon",
        # `FasterWhisperEngine` honours it through `initial_prompt`, and this adapter does
        # not - it needs `processor.get_prompt_ids()` fed as `prompt_ids`, which the
        # pipeline API does not take directly. Found by measuring: hint and no-hint gave
        # CER identical to three decimals, which is not what "off-domain vocabulary does
        # not help" looks like. It is what "the argument is discarded" looks like.
        self._warn_about_unused_vocabulary(hint)

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
