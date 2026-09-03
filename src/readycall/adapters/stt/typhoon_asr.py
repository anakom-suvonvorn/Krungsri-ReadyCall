"""TyphoonAsrEngine — the Thai alternative `D30` promised to measure against Thonburian.

**It is not a Whisper model, and that is the whole point of having it.**
`scb10x/typhoon-asr-realtime` is an NVIDIA **NeMo FastConformer transducer**, fine-tuned
from `nvidia/stt_en_fastconformer_transducer_large`. Architecturally it has almost nothing
in common with Whisper, and three consequences follow — every one of them relevant to the
failures this project has already hit:

* **it does not pad to 30 seconds.** Whisper transcribes a fixed window whatever you give
  it, which is why a two-second utterance costs the same as a twenty-second one here. A
  transducer processes what it is given. If the latency budget (`ARCHITECTURE` §15) turns
  out to be the problem on this GPU, this is the structural reason it might not be.
* **it is a streaming architecture.** "realtime" in the name is not marketing: transducers
  emit as they go. `SttEngine.stream()` could be genuinely implemented for this one, where
  for Whisper it can only ever be our own endpointer wearing a costume.
* **it does not hallucinate on silence the way Whisper does** (`B14`). Whisper is a
  sequence-to-sequence model with a language-model decoder that will happily generate text
  from nothing; a transducer has no such freedom. Whether that actually holds on our audio
  is a measurement, not a promise — and it is one of the more interesting things the
  bake-off can settle.

**It needs `nemo_toolkit[asr]`, which is a large install and is NOT in the `ml` extra**
(`D99`). That was left as its own decision because it is another multi-gigabyte dependency
with a heavy transitive tree, and this project's standing rule is that an unused dependency
in the lockfile is a cost with no payer. Declaring it is one line in `pyproject.toml`:

    asr = ["nemo_toolkit[asr]>=2.0"]

then `uv sync --extra ml --extra asr`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult

log = get_logger(__name__)

DEFAULT_MODEL = "scb10x/typhoon-asr-realtime"


class TyphoonAsrEngine:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        language: str = "th",
    ) -> None:
        self._model_name = model
        self._device = device
        self._language = language
        self._model: Any = None
        self._np: Any = None

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(
            name="typhoon_asr",
            version="nemo-fastconformer",
            device=self._device,
            model=self._model_name,
            extra={"language": self._language, "architecture": "transducer"},
        )

    async def warmup(self) -> None:
        if self._model is not None:  # pragma: no cover - the worker warms once
            return
        try:
            import numpy
            import torch
            from nemo.collections.asr.models import ASRModel
        except ImportError as exc:
            raise ConfigError(
                "TyphoonAsrEngine needs NeMo, which is deliberately not in the `ml` extra "
                "(`D99` — it is a large install with a heavy transitive tree). Add it:\n"
                '  asr = ["nemo_toolkit[asr]>=2.0"]   in pyproject.toml\n'
                "  uv sync --extra ml --extra asr"
            ) from exc
        self._np = numpy

        def load() -> Any:
            model = ASRModel.from_pretrained(model_name=self._model_name)
            model = model.to(self._device)
            model.eval()
            return model

        self._model = await asyncio.to_thread(load)
        log.info("typhoon asr loaded", model=self._model_name, device=self._device)
        # A tone rather than silence, for `B14`'s reason. It should matter less here than
        # it does for Whisper — that is a claim the bake-off can check rather than repeat.
        import math

        await self.transcribe_utterance(
            [
                AudioFrame(
                    samples=[0.2 * math.sin(2 * math.pi * 220 * i / 16000) for i in range(16000)],
                    t_start_ms=0,
                )
            ]
        )
        log.info("typhoon asr warm")
        _ = torch

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
            return SttResult(text="", confidence=0.0, engine="typhoon_asr")

        t_start = frames[0].t_start_ms
        t_end = t_start + int(len(samples) / 16000 * 1000)
        audio = self._np.asarray(samples, dtype=self._np.float32)

        def run() -> str:
            out = self._model.transcribe([audio], batch_size=1, verbose=False)
            # NeMo's return shape has moved between versions: a list of strings in some,
            # a list of Hypothesis objects in others, and occasionally a (best, all) tuple.
            # Unwrapped defensively rather than pinned to one version, because a silent
            # `str(Hypothesis(...))` would put an object repr on the agent's screen.
            first: Any = out
            while isinstance(first, (list, tuple)) and first:
                first = first[0]
            return str(getattr(first, "text", first)).strip()

        text = await asyncio.to_thread(run)
        return SttResult(
            text=text,
            # A transducer does expose per-token scores, but turning them into one number
            # comparable with Whisper's would be inventing a scale. `D13`: a number on the
            # agent's screen has to mean something.
            confidence=None,
            t_start_ms=t_start,
            t_end_ms=t_end,
            engine="typhoon_asr",
            engine_version=self._model_name,
            is_final=True,
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        """Genuinely implementable for a transducer, unlike for Whisper — but not built.

        `TranscriptionStream` owns endpointing for every engine (`D9`), so a streaming path
        here would be a *second* way for audio to become turns, with its own timing and its
        own bugs. If Typhoon wins the bake-off on latency, this is the follow-up that could
        make it win by more; until then one path is worth more than two.
        """
        raise NotImplementedError(
            "streaming is possible for a transducer but not built - see the docstring"
        )
        yield  # type: ignore[unreachable]  # pragma: no cover - makes this a generator

    async def close(self) -> None:
        self._model = None


__all__ = ["DEFAULT_MODEL", "TyphoonAsrEngine"]
