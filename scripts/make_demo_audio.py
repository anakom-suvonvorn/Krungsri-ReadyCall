"""Synthesise the WAV the stage-safe demo plays down the line (`D107`).

**Why this exists at all: a fresh clone has no audio.** `.gitignore` excludes `*.wav`
outright and `tests/audio/thai_calls/` by name, because the real corpus is customer speech
with account numbers in it (`D97`, `D14`). So the demo walkthrough in `README.md` would
point at a file nobody has. This makes one, deterministically, in a second, with no
dependencies beyond the standard library.

**It is not speech and does not pretend to be.** It is a two-harmonic tone shaped by an
envelope — enough energy and structure for `EnergyVad` to endpoint, which is all the
scripted engine needs, since that engine returns a line per utterance regardless of what
the audio contained. What the audio genuinely decides is **how many turns there are and
when each one starts and ends**, and that is exactly what makes the fallback look like a
transcription rather than a slideshow.

**The utterance length is computed from the script, not chosen.** `D98`'s rate guard
refuses a turn carrying more than `MAX_CHARS_PER_SECOND` of text for the seconds of audio
it arrived on — measured from real Thai, and it does not care that this text came from a
file. So the longest line in `config/demo_transcript.yaml` sets the length of every
utterance, with headroom. Get this wrong by hand and every line is silently dropped and the
panel is simply empty, with nothing in the log to explain it.

    uv run python scripts/make_demo_audio.py
    uv run python scripts/make_demo_audio.py --out tests/audio/short.wav --headroom 3
"""

from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from readycall.adapters.stt.scripted import load_scripted_turns  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.media.sources import write_wav  # noqa: E402
from readycall.services.transcription.stream import MAX_CHARS_PER_SECOND  # noqa: E402

RATE = 16000
#: Silence between utterances. Comfortably over `D9`'s 100 ms minimum silence, so the
#: endpointer closes each one cleanly rather than running two together.
GAP_S = 0.6


def utterance(seconds: float, *, amplitude: float = 0.25) -> list[float]:
    """A voiced-sounding tone: a low fundamental plus a formant-ish harmonic.

    The two components matter more than the exact frequencies — a pure sine is quiet in
    the bands an energy detector weighs, and a single click is not held long enough to
    pass `D9`'s 500 ms minimum speech.
    """
    n = int(RATE * seconds)
    out: list[float] = []
    for i in range(n):
        # Fade the first and last 30 ms so the segment does not start with a step, which
        # reads as a transient rather than as speech.
        edge = min(1.0, i / (RATE * 0.03), (n - i) / (RATE * 0.03))
        value = math.sin(2 * math.pi * 190 * i / RATE) + 0.5 * math.sin(
            2 * math.pi * 700 * i / RATE
        )
        out.append(amplitude * edge * value / 1.5)
    return out


def main() -> int:
    enable_utf8()  # Thai on a cp1252 console kills the process (`B1`)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--script",
        type=Path,
        default=REPO_ROOT / "config" / "demo_transcript.yaml",
        help="the lines the scripted engine will speak; sets how long each utterance is",
    )
    parser.add_argument(
        "--out", type=Path, default=REPO_ROOT / "tests" / "audio" / "demo_intake.wav"
    )
    parser.add_argument(
        "--headroom",
        type=float,
        default=2.0,
        help="multiple of the rate guard's minimum. Below 1.0 the lines are refused",
    )
    parser.add_argument("--seconds", type=float, default=0.0, help="override, per utterance")
    args = parser.parse_args()

    turns = load_scripted_turns(args.script)
    if not turns:
        print(f"no turns in {args.script} - nothing to size the audio against")
        return 1

    longest = max(len(t.text) for t in turns)
    # The guard is `chars / seconds <= MAX_CHARS_PER_SECOND`, so this is the floor.
    minimum_s = longest / MAX_CHARS_PER_SECOND
    seconds = args.seconds or round(minimum_s * args.headroom, 2)

    samples: list[float] = []
    for _ in turns:
        samples += utterance(seconds)
        samples += [0.0] * int(RATE * GAP_S)

    write_wav(args.out, samples, sample_rate=RATE)
    total = len(samples) / RATE
    print(f"wrote {args.out}")
    print(f"  {len(turns)} utterances of {seconds:.2f}s + {GAP_S}s silence = {total:.1f}s total")
    print(f"  longest line {longest} chars; the rate guard needs >= {minimum_s:.2f}s for it")
    if seconds < minimum_s:
        print("  WARNING: below the guard's floor - every line will be refused (`D98`)")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
