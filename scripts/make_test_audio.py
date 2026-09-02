"""Generate the synthetic WAV the bake-off's smoke run uses.

`.gitignore` excludes `*.wav`, correctly — `CLAUDE.md` says never commit audio — so a fresh
clone has no test audio at all and the README's bake-off example would point at nothing.
Generating it is the honest fix: no bytes in the repo, and one command to get them back.

    uv run python scripts/make_test_audio.py

⚠️ **This is not speech and must never be used to judge a model** (`B14`). It is three
harmonic bursts separated by near-silence — enough to prove the pipeline moves audio from a
file through the gateway, the detector, the endpointer and an engine, and nothing more.
Whisper fed tones does not behave the way Whisper fed Thai behaves; it hallucinates, it
echoes its own prompt, and it is slow doing both. A real WER or latency number needs real
Thai telephone speech with a reference transcript beside it (`D30`).
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.console import enable_utf8  # noqa: E402
from readycall.media.sources import write_wav  # noqa: E402

RATE = 16000


def burst(ms: float, f0: float, amplitude: float = 0.25) -> list[float]:
    """A few harmonics with a soft envelope — loud and structured, like voiced speech.

    The envelope matters: a square-edged tone makes the detector fire on the click rather
    than on the content, which would make the endpointing tests pass for the wrong reason.
    """
    n = int(RATE * ms / 1000.0)
    out: list[float] = []
    edge = max(1, int(0.02 * RATE))
    for i in range(n):
        env = min(1.0, i / edge) * min(1.0, (n - i) / edge)
        value = sum(
            amplitude * (0.6**k) * math.sin(2 * math.pi * f0 * (k + 1) * i / RATE) for k in range(5)
        )
        out.append(value * env)
    return out


def quiet(ms: float) -> list[float]:
    """Never digital zero: a real line has a noise floor, and a detector tuned against
    perfect silence falls over on the first actual phone call."""
    n = int(RATE * ms / 1000.0)
    return [0.0009 * math.sin(2 * math.pi * 50 * i / RATE) for i in range(n)]


def main() -> int:
    enable_utf8()
    samples = (
        quiet(300)
        + burst(900, 180)
        + quiet(500)
        + burst(1100, 210)
        + quiet(600)
        + burst(700, 160)
        + quiet(400)
    )
    out = write_wav(ROOT / "tests" / "audio" / "three_utterances.wav", samples)
    print(f"wrote {out.relative_to(ROOT)}  ({len(samples) / RATE:.1f}s, 3 utterances)")
    print("\nNOT SPEECH. Proves the pipeline moves, says nothing about a model (`B14`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
