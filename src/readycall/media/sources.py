"""Audio sources that are not a telephone.

`NEXT_SESSION`'s plan for this step says it plainly: build the media path **against a WAV
file rather than a phone, so endpointing can be tuned without telephony**. Asterisk arrives
at P5; the VAD constants have to be tunable now, and a file is a better tuning rig anyway
because it is the same audio every run.

`WavFileSource` reads with the standard library's `wave` module — no soundfile, no numpy —
so it works on a base install and in CI. It deliberately feeds the gateway **the same way a
phone would**: in small packets, in the file's own format, letting the gateway do the
resampling. Decoding the file straight to 16 kHz here would skip the exact code path that
carries the call on the day.
"""

from __future__ import annotations

import asyncio
import wave
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

from readycall.errors import ConfigError
from readycall.media.audio import AudioFormat, Encoding

#: 20 ms, which is what SIP/RTP actually sends and therefore what the gateway should be
#: tested against — not a round number of samples chosen for our own convenience.
DEFAULT_PACKET_MS = 20.0


class WavFileSource:
    """Replays a WAV file as if it were arriving from a phone line."""

    def __init__(self, path: Path | str, *, packet_ms: float = DEFAULT_PACKET_MS) -> None:
        self._path = Path(path)
        if not self._path.exists():
            raise ConfigError(f"audio file not found: {self._path}")
        self._packet_ms = packet_ms
        with wave.open(str(self._path), "rb") as w:
            width = w.getsampwidth()
            if width != 2:
                raise ConfigError(
                    f"{self._path.name}: only 16-bit PCM WAV is supported, got "
                    f"{width * 8}-bit. Convert it rather than guessing: "
                    f"ffmpeg -i in.wav -acodec pcm_s16le -ac 1 out.wav"
                )
            self._fmt = AudioFormat(
                encoding=Encoding.PCM16,
                sample_rate=w.getframerate(),
                channels=w.getnchannels(),
            )
            self._total_frames = w.getnframes()

    @property
    def fmt(self) -> AudioFormat:
        return self._fmt

    @property
    def duration_s(self) -> float:
        return self._total_frames / self._fmt.sample_rate

    def packets(self) -> Iterator[bytes]:
        """Raw packets in the file's own format, exactly as telephony would deliver them."""
        per_packet = max(1, int(self._fmt.sample_rate * self._packet_ms / 1000.0))
        with wave.open(str(self._path), "rb") as w:
            while True:
                chunk = w.readframes(per_packet)
                if not chunk:
                    return
                yield chunk

    async def stream(self, *, realtime: bool = False) -> AsyncIterator[bytes]:
        """Async packets. `realtime=True` paces them at wall-clock speed.

        Pacing is off by default because a test that sleeps for the length of its audio is
        a test nobody runs. It is worth switching **on** when measuring latency, though:
        feeding a whole call in a tight loop measures throughput, and the number the
        latency budget actually cares about is utterance-end to turn (`ARCHITECTURE` §15).
        """
        for packet in self.packets():
            yield packet
            if realtime:
                await asyncio.sleep(self._packet_ms / 1000.0)


def write_wav(path: Path | str, samples: list[float], *, sample_rate: int = 16000) -> Path:
    """Write float samples as 16-bit PCM. For fixtures and for the bake-off's inputs."""
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(out), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sample_rate)
        w.writeframes(
            b"".join(
                int(max(-1.0, min(1.0, s)) * 32767).to_bytes(2, "little", signed=True)
                for s in samples
            )
        )
    return out


__all__ = ["DEFAULT_PACKET_MS", "WavFileSource", "write_wav"]
