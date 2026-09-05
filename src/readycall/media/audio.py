"""Turning whatever telephony hands us into the one format everything above expects.

`ports/stt.py` states the contract in a sentence: **16 kHz mono float32, always. The media
gateway normalises before anyone sees it.** This module is that normalisation, and it is
the only place in the system that knows a phone call is not already in that shape.

**Why it is pure Python with no numpy.** CI runs `uv sync --frozen` — no extras — so
everything here has to work on an install with no torch, no numpy and no soundfile.
Making the audio path depend on the `ml` extra would mean the media layer is only
exercised on a machine that happens to have a GPU, which is `B7`'s shape exactly: a path
nobody runs. The arithmetic is per-frame and tiny (a 20 ms frame at 8 kHz is 160 samples),
so the cost of doing it in Python is irrelevant next to that.

**Both G.711 companding laws are supported on purpose.** North America and Japan use
µ-law; most of the rest of the world, Thailand included, uses **A-law**. Decoding one as
the other does not fail — it produces loud, distorted, entirely plausible-looking audio,
and the first thing anybody would blame is the microphone or the model. It is a one-table
difference and getting it wrong is expensive to diagnose, so both are here and the format
is explicit rather than assumed.
"""

from __future__ import annotations

import io
import struct
import wave
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from readycall.errors import ConfigError
from readycall.ports.stt import AudioFrame

#: What every stage above the gateway is promised (`D9`).
TARGET_SAMPLE_RATE = 16000


class Encoding(StrEnum):
    """How the bytes on the wire represent a sample."""

    PCM16 = "pcm16"  # signed 16-bit little-endian — WAV, WebRTC, AudioSocket
    PCM8_ULAW = "ulaw"  # G.711 µ-law — North America, Japan
    PCM8_ALAW = "alaw"  # G.711 A-law — Europe, and Thailand
    FLOAT32 = "float32"  # already normalised; the app's mic over a websocket


@dataclass(frozen=True, slots=True)
class AudioFormat:
    """The shape of an incoming stream. Named, never guessed."""

    encoding: Encoding = Encoding.PCM16
    sample_rate: int = 8000
    channels: int = 1

    def __post_init__(self) -> None:
        if self.sample_rate <= 0:
            raise ConfigError(f"sample_rate must be positive, got {self.sample_rate}")
        if self.channels not in (1, 2):
            raise ConfigError(f"channels must be 1 or 2, got {self.channels}")

    @property
    def bytes_per_sample(self) -> int:
        return (
            1
            if self.encoding in (Encoding.PCM8_ULAW, Encoding.PCM8_ALAW)
            else (4 if self.encoding is Encoding.FLOAT32 else 2)
        )

    @property
    def bytes_per_frame(self) -> int:
        """One sample across all channels."""
        return self.bytes_per_sample * self.channels


def _ulaw_table() -> tuple[int, ...]:
    """G.711 µ-law -> signed 16-bit, all 256 codes precomputed once."""
    table = []
    for code in range(256):
        u = ~code & 0xFF
        sign = u & 0x80
        exponent = (u >> 4) & 0x07
        mantissa = u & 0x0F
        magnitude = (((mantissa << 3) + 0x84) << exponent) - 0x84
        table.append(-magnitude if sign else magnitude)
    return tuple(table)


def _alaw_table() -> tuple[int, ...]:
    """G.711 A-law -> signed 16-bit. The even-bit inversion (`^ 0x55`) is part of the
    standard, not a trick: it keeps the idle code away from a long run of zeros."""
    table = []
    for code in range(256):
        a = code ^ 0x55
        sign = a & 0x80
        exponent = (a >> 4) & 0x07
        mantissa = a & 0x0F
        # The exponent-0 segment is linear; every other segment is a shifted chord.
        magnitude = (
            (mantissa << 4) + 8 if exponent == 0 else ((mantissa << 4) + 0x108) << (exponent - 1)
        )
        table.append(-magnitude if sign else magnitude)
    return tuple(table)


ULAW = _ulaw_table()
ALAW = _alaw_table()

#: Full scale for signed 16-bit. Dividing by 32768 rather than 32767 keeps the mapping
#: symmetric about zero, which is what every model's preprocessing assumes.
_INT16_FULL_SCALE = 32768.0


def decode(payload: bytes, fmt: AudioFormat) -> list[float]:
    """Bytes on the wire -> interleaved float32 samples in [-1.0, 1.0]."""
    if not payload:
        return []
    usable = len(payload) - (len(payload) % fmt.bytes_per_frame)
    if usable != len(payload):
        # A partial trailing sample means the caller framed the stream wrongly. Dropping
        # it silently would smear every following sample by a fraction of a period, which
        # sounds like noise and reads like a broken model.
        raise ValueError(
            f"{len(payload)} bytes is not a whole number of "
            f"{fmt.bytes_per_frame}-byte frames ({fmt.encoding}, {fmt.channels}ch)"
        )
    if fmt.encoding is Encoding.PCM8_ULAW:
        return [ULAW[b] / _INT16_FULL_SCALE for b in payload]
    if fmt.encoding is Encoding.PCM8_ALAW:
        return [ALAW[b] / _INT16_FULL_SCALE for b in payload]
    count = usable // fmt.bytes_per_sample
    if fmt.encoding is Encoding.FLOAT32:
        return list(struct.unpack(f"<{count}f", payload))
    return [s / _INT16_FULL_SCALE for s in struct.unpack(f"<{count}h", payload)]


def to_mono(samples: Sequence[float], channels: int) -> list[float]:
    """Average the channels.

    Averaging rather than taking channel 0: on a conference leg the two channels can carry
    different speakers, and picking one would silently delete a person from the transcript.
    """
    if channels == 1:
        return list(samples)
    return [sum(samples[i : i + channels]) / channels for i in range(0, len(samples), channels)]


def resample(samples: Sequence[float], *, src_rate: int, dst_rate: int) -> list[float]:
    """Linear interpolation to `dst_rate`.

    Adequate here, and worth saying why rather than leaving it to look like a shortcut.
    Telephony is band-limited to ~3.4 kHz, so upsampling 8 kHz -> 16 kHz invents no
    detail whichever method is used — the information is simply not in the signal. A
    polyphase filter would suppress the imaging above 4 kHz slightly better; that is a
    real but small quality difference, and it costs a numpy dependency in the one module
    that must not have one (see the module docstring).

    **The genuine quality ceiling is upstream of this function, and is worth knowing
    before anybody blames the model:** Whisper is trained on 16 kHz wideband speech, and a
    phone call has had everything above 3.4 kHz removed before it reaches us. Thai has
    contrasts up there. Nothing here can put them back, which is exactly why `D30`'s
    bake-off is measured on real telephone audio rather than on clean recordings.
    """
    if src_rate == dst_rate or not samples:
        return list(samples)
    ratio = src_rate / dst_rate
    out_len = int(len(samples) * dst_rate / src_rate)
    out: list[float] = []
    last = len(samples) - 1
    for n in range(out_len):
        pos = n * ratio
        i = int(pos)
        if i >= last:
            out.append(samples[last])
            continue
        frac = pos - i
        out.append(samples[i] * (1.0 - frac) + samples[i + 1] * frac)
    return out


def normalise(payload: bytes, fmt: AudioFormat, *, t_start_ms: int = 0) -> AudioFrame:
    """The whole job in one call: bytes in any supported shape -> one 16 kHz mono frame."""
    mono = to_mono(decode(payload, fmt), fmt.channels)
    return AudioFrame(
        samples=resample(mono, src_rate=fmt.sample_rate, dst_rate=TARGET_SAMPLE_RATE),
        t_start_ms=t_start_ms,
        sample_rate=TARGET_SAMPLE_RATE,
    )


def encode_wav(samples: Sequence[float], *, sample_rate: int = TARGET_SAMPLE_RATE) -> bytes:
    """Float samples -> a complete 16-bit PCM WAV, in memory (`D110`).

    The inverse of `decode`, and the format a recording is stored in: 16-bit PCM is what
    every player on earth opens, it halves the size of float32, and the loss is below the
    noise floor of a telephone line. No `soundfile`, no numpy - this runs on the CI box
    with no `ml` extra, same as the rest of this module.

    In memory rather than to a path because the bytes go straight into the encrypting
    blob store: a recording that touches the filesystem on its way to being encrypted has
    been on the filesystem in the clear, which is the thing `D9` refused.
    """
    pcm = b"".join(
        int(max(-1.0, min(1.0, s)) * 32767).to_bytes(2, "little", signed=True) for s in samples
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(pcm)
    return buffer.getvalue()


def rms(samples: Sequence[float]) -> float:
    """Root-mean-square level. Used by the energy VAD and by the level meter (`D32`)."""
    if not samples:
        return 0.0
    mean_square = sum(s * s for s in samples) / len(samples)
    return float(mean_square**0.5)


__all__ = [
    "TARGET_SAMPLE_RATE",
    "AudioFormat",
    "Encoding",
    "decode",
    "encode_wav",
    "normalise",
    "resample",
    "rms",
    "to_mono",
]
