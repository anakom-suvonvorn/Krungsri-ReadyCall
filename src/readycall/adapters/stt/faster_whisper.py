"""FasterWhisperEngine — CTranslate2, and the one most likely to fit this GPU (`D30`).

`PROJECT_STATE` §7 predicted this before any of it was installed: *"Whisper pads every
chunk to 30 s, so `faster-whisper`/CTranslate2 with `int8_float16` is probably required to
hit the latency budget."* With the real figure now measured — **4.00 GiB total and about
3.2 GiB free** (`D95`) — that reads less like a preference and more like the only option
for a medium checkpoint.

`int8_float16` is the default here for that reason: weights quantised to int8, compute in
fp16. On an Ampere card it is roughly a quarter of the memory of fp32 and faster, and the
accuracy cost on speech is small — but "small" is a claim, which is what `scripts/bake_off.py`
exists to replace with a number on our own audio.

**The Windows cuDNN problem, handled here rather than in a README nobody reads.**
CTranslate2 loads cuDNN by name at import time. torch ships its own copy inside
`torch/lib`, but that directory is not on the DLL search path, so on Windows this fails
with `Could not locate cudnn_ops64_9.dll` — a message that sounds like a broken CUDA
install and is not. `_add_torch_dll_directory()` points the loader at the copy that is
already on disk.
"""

from __future__ import annotations

import asyncio
import os
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult

log = get_logger(__name__)

#: `D9`'s model family. A CTranslate2 build of the same checkpoint, or any Whisper size
#: name that faster-whisper can fetch (`tiny`, `small`, `medium`, `large-v3`).
DEFAULT_MODEL = "biodatlab/whisper-th-medium-combined-ct2"


def _warmup_tone(seconds: float = 1.0) -> list[float]:
    """A tone, NOT silence.

    The obvious warmup input is a second of zeros, and it is the worst possible choice:
    Whisper hallucinates on silence and takes **8.6 s** to do it on this GPU, against
    155 ms for a segment with energy in it (`B14`). A warmup meant to take the first-use
    cost off the call path was itself paying a pathological one.
    """
    import math

    return [0.2 * math.sin(2 * math.pi * 220 * i / 16000) for i in range(int(16000 * seconds))]


def _add_torch_dll_directory() -> None:
    """Let CTranslate2 find the cuDNN that torch already ships (Windows only)."""
    if sys.platform != "win32":  # pragma: no cover - only Windows needs this
        return
    try:
        import torch

        lib = os.path.join(os.path.dirname(torch.__file__), "lib")
        if os.path.isdir(lib):
            os.add_dll_directory(lib)
            log.info("added torch lib to the DLL search path", path=lib)
    except Exception:  # pragma: no cover - best effort; the real error surfaces below
        log.info("could not add torch lib to the DLL search path")


class FasterWhisperEngine:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        compute_type: str = "int8_float16",
        beam_size: int = 5,
        language: str = "th",
    ) -> None:
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._beam_size = beam_size
        self._language = language
        self._model: Any = None
        self._np: Any = None

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(
            name="faster_whisper",
            version=self._compute_type,
            device=self._device,
            model=self._model_name,
            extra={"beam_size": str(self._beam_size), "language": self._language},
        )

    async def warmup(self) -> None:
        """Load the model and run one throwaway inference.

        Both halves matter. Loading is 5-20 s and can never sit in a call path (`ports/stt.py`),
        and the *first* inference is slower than every later one because CUDA kernels are
        compiled on first use — so a warmup that only loads still leaves the first real
        caller paying for it.
        """
        if self._model is not None:  # pragma: no cover - the worker warms once
            return
        _add_torch_dll_directory()
        try:
            import numpy
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ConfigError(
                "FasterWhisperEngine needs the `ml` extra: uv sync --extra ml"
            ) from exc
        self._np = numpy

        def load() -> Any:
            return WhisperModel(
                self._model_name, device=self._device, compute_type=self._compute_type
            )

        # Off the event loop: this is seconds of blocking work, and on the API process it
        # would stall every other call in flight.
        self._model = await asyncio.to_thread(load)
        log.info(
            "faster-whisper loaded",
            model=self._model_name,
            device=self._device,
            compute_type=self._compute_type,
        )
        await self.transcribe_utterance([AudioFrame(samples=_warmup_tone(), t_start_ms=0)])
        log.info("faster-whisper warm")

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        if self._model is None:
            await self.warmup()
        samples: list[float] = []
        for frame in frames:
            samples.extend(frame.samples)
        if not samples:
            return SttResult(text="", confidence=0.0, engine="faster_whisper")

        t_start = frames[0].t_start_ms
        t_end = t_start + int(len(samples) / 16000 * 1000)
        audio = self._np.asarray(samples, dtype=self._np.float32)

        def run() -> tuple[str, float | None]:
            segments, _info = self._model.transcribe(
                audio,
                language=(hint.language if hint else self._language),
                beam_size=self._beam_size,
                # The segment is ALREADY endpointed by our own VAD (`D9`), and letting
                # Whisper endpoint it again would re-cut utterances against different
                # constants than the ones the padding was tuned for.
                vad_filter=False,
                # Domain nudges measurably help on insurance jargon (IPD/OPD, ค่าห้อง).
                initial_prompt=", ".join(hint.vocabulary) if hint and hint.vocabulary else None,
                condition_on_previous_text=False,
            )
            texts: list[str] = []
            logprobs: list[float] = []
            for seg in segments:
                texts.append(seg.text)
                logprobs.append(seg.avg_logprob)
            mean = sum(logprobs) / len(logprobs) if logprobs else None
            # avg_logprob is roughly -1..0; map it onto 0..1 so the number on the agent's
            # screen means the same thing whichever engine produced it. It is a rough
            # proxy and `D13` forbids showing an uncalibrated number as a percentage.
            confidence = None if mean is None else max(0.0, min(1.0, 1.0 + mean))
            return "".join(texts).strip(), confidence

        text, confidence = await asyncio.to_thread(run)
        return SttResult(
            text=text,
            confidence=confidence,
            t_start_ms=t_start,
            t_end_ms=t_end,
            engine="faster_whisper",
            engine_version=f"{self._model_name}:{self._compute_type}",
            is_final=True,
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        """Not implemented, deliberately: endpointing is ours (`D9`).

        Whisper is not a streaming model — it consumes a padded 30 s window — so a
        "streaming" wrapper here would just be our own endpointer with a worse interface.
        `TranscriptionStream` already owns that job.
        """
        raise NotImplementedError(
            "Whisper is not streaming; use TranscriptionStream with transcribe_utterance"
        )
        yield  # type: ignore[unreachable]  # pragma: no cover - makes this a generator

    async def close(self) -> None:
        self._model = None


__all__ = ["DEFAULT_MODEL", "FasterWhisperEngine"]
