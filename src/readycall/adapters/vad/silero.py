"""SileroVad — the production detector (`D9`).

**Loaded from the installed package, never from `torch.hub`.** The reference project called
`torch.hub.load(...)` at runtime, which reaches out to GitHub. `D9` changed that
deliberately and the reason is worth restating: a network fetch in the middle of a live
call is unacceptable, and on a hackathon stage with venue wifi it is a demo that dies in
front of judges. `silero-vad` is a declared dependency and the weights ship inside it.

**It runs on the CPU on purpose** (`D95`). The model is about a megabyte and costs well
under a millisecond per 32 ms frame, while the GPU on the demo laptop has ~3.2 GiB free and
Whisper wants most of it. Spending scarce VRAM on the cheap model to save nothing is the
wrong trade twice over.

**The import is deferred to the constructor** so that this module can be imported on an
install with no `ml` extra — which is every CI run. Nothing above the port may need to know
whether torch exists.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.vad import VadInfo

log = get_logger(__name__)

#: Silero v5 takes exactly this many samples at 16 kHz (32 ms). It does not merely prefer
#: it: hand it a different length and it returns confident nonsense rather than raising.
SILERO_FRAME_SAMPLES = 512


class SileroVad:
    def __init__(self, *, frame_samples: int = SILERO_FRAME_SAMPLES) -> None:
        try:
            import torch
            from silero_vad import load_silero_vad
        except ImportError as exc:  # pragma: no cover - depends on the optional extra
            raise ConfigError(
                "SileroVad needs the `ml` extra: uv sync --extra ml. "
                "Use STT_ENGINE=scripted with the energy VAD to run without it."
            ) from exc

        if frame_samples != SILERO_FRAME_SAMPLES:
            raise ConfigError(
                f"Silero v5 requires exactly {SILERO_FRAME_SAMPLES} samples per frame; "
                f"got {frame_samples}. A different size does not fail, it returns "
                "confident nonsense - so it is refused here instead."
            )
        self._torch: Any = torch
        self._model: Any = load_silero_vad(onnx=False)
        self._frame_samples = frame_samples
        log.info("silero vad loaded", frame_samples=frame_samples, device="cpu")

    @property
    def info(self) -> VadInfo:
        return VadInfo(name="silero", version="v5", device="cpu")

    @property
    def frame_samples(self) -> int:
        return self._frame_samples

    def reset(self) -> None:
        """Drop the RNN hidden state.

        Not housekeeping. Silero carries state across frames, so the previous caller's
        trailing audio would otherwise colour the first frames of the next call — and the
        symptom is a clipped or missing first word, which looks like an STT fault.
        """
        self._model.reset_states()

    def speech_probability(self, samples: Sequence[float]) -> float:
        if len(samples) != self._frame_samples:
            raise ValueError(
                f"silero needs exactly {self._frame_samples} samples, got {len(samples)}"
            )
        tensor = self._torch.tensor(list(samples), dtype=self._torch.float32)
        with self._torch.no_grad():
            return float(self._model(tensor, 16000).item())


__all__ = ["SILERO_FRAME_SAMPLES", "SileroVad"]
