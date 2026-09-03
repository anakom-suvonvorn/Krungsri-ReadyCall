"""Score the ENDPOINTER against hand-annotated speech spans (`D9`, `D96`, `D97`).

    uv run python scripts/score_endpointer.py --vad silero
    uv run python scripts/score_endpointer.py --vad energy --out endpointer.txt

**Why this exists, and why CER cannot replace it.** `bake_off.py` measures the *engine*:
text out against text expected. But the detector decides what the engine is ever asked to
transcribe, so a bad CER has two possible authors and that table cannot tell them apart. A
sentence the endpointer never emitted reads, in CER, exactly like a sentence the model got
wrong — and the fixes are opposite. This scores the half `bake_off.py` is blind to, with
**no model loaded and no GPU touched**.

The ground truth is `segments.tsv` from `scripts/prepare_dataset.py`: every annotated span
of the call, including the ones marked `noise`. That file has existed since `D97` and
nothing has ever read it.

**The three numbers, and what each one predicts.**

- **coverage** — what fraction of annotated speech *seconds* landed inside some detected
  segment. This is the one that predicts **deletions** in CER: audio outside every segment
  is audio the model is never shown, so its words cannot appear in the output at any
  accuracy. A coverage of 0.6 puts a floor of roughly 0.4 under the CER no engine change
  can lift.
- **span recall** — what fraction of annotated *spans* were caught at all (over half the
  span inside a detected segment). Coverage can look healthy while short spans vanish
  wholesale, and a lost span is a lost sentence rather than a lost syllable.
- **false-alarm seconds** — detected audio outside every annotated span. On this dataset
  the un-annotated remainder is near-digital-silence, which is the single worst input
  Whisper can be handed (`B14`: 8.6 s and invented Thai for one second of it). So a false
  alarm here is not a cosmetic imprecision; it is the exact condition the three guards in
  `stream.py` exist to catch, and this says how often the detector creates it.

**Padding is included in the detected spans on purpose.** `pad_before_ms` is 120 ms because
in Thai the first syllable often carries the tone (`D9`), and that padded audio is genuinely
what the model receives — scoring the unpadded span would flatter the detector on precisely
the boundary the padding was added to fix.

The tolerance is one-sided and stated rather than tuned: a detected segment counts as
covering annotated audio wherever the two overlap, and every annotated second outside all
detected segments is a miss. There is no grace window, because a grace window is a knob
that can be turned until the answer is the one you wanted.
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.console import enable_utf8  # noqa: E402
from readycall.media.audio import normalise  # noqa: E402
from readycall.media.sources import WavFileSource  # noqa: E402
from readycall.ports.vad import VoiceActivityDetector  # noqa: E402
from readycall.services.transcription.endpointer import Endpointer, EndpointSettings  # noqa: E402

DEFAULT_CALLS = ROOT / "tests/audio/thai_calls"


@dataclass(frozen=True, slots=True)
class Span:
    start_s: float
    end_s: float

    @property
    def duration_s(self) -> float:
        return max(0.0, self.end_s - self.start_s)

    def overlap_s(self, other: Span) -> float:
        return max(0.0, min(self.end_s, other.end_s) - max(self.start_s, other.start_s))


@dataclass
class CallScore:
    stem: str
    duration_s: float
    annotated: list[Span] = field(default_factory=list)
    detected: list[Span] = field(default_factory=list)

    @property
    def annotated_s(self) -> float:
        return sum(s.duration_s for s in self.annotated)

    @property
    def detected_s(self) -> float:
        return sum(s.duration_s for s in self.detected)

    @property
    def covered_s(self) -> float:
        """Annotated speech seconds that fall inside at least one detected segment."""
        return sum(sum(a.overlap_s(d) for d in self.detected) for a in self.annotated)

    @property
    def coverage(self) -> float:
        return self.covered_s / self.annotated_s if self.annotated_s else 0.0

    @property
    def spans_caught(self) -> int:
        """Annotated spans more than half of which was detected."""
        return sum(
            1
            for a in self.annotated
            if a.duration_s and sum(a.overlap_s(d) for d in self.detected) / a.duration_s >= 0.5
        )

    @property
    def span_recall(self) -> float:
        return self.spans_caught / len(self.annotated) if self.annotated else 0.0

    @property
    def false_alarm_s(self) -> float:
        """Detected seconds outside every annotated span."""
        return max(0.0, self.detected_s - self.covered_s)


def load_annotations(manifest: Path) -> dict[str, list[Span]]:
    """Speech spans only. The `noise` rows are ground truth for the opposite question and
    are handled by `false_alarm_s`, which needs no labels — anything outside every
    annotated span is un-annotated audio, and on this dataset that is silence (`D97`)."""
    spans: dict[str, list[Span]] = {}
    with manifest.open(encoding="utf-8") as fh:
        for row in csv.DictReader(fh, delimiter="\t"):
            if row["kind"] != "speech":
                continue
            spans.setdefault(row["call"], []).append(
                Span(float(row["start_s"]), float(row["end_s"]))
            )
    return spans


def frames(samples: Sequence[float], size: int) -> Iterator[Sequence[float]]:
    """Fixed-size frames. The trailing partial frame is dropped rather than zero-padded:
    Silero declares its frame size for a reason (`ports/vad.py`) and a short frame is a
    frame it was never designed to score."""
    for start in range(0, len(samples) - size + 1, size):
        yield samples[start : start + size]


def probabilities(path: Path, vad: VoiceActivityDetector) -> list[float]:
    """One speech probability per frame, over the SHIPPING path: the same source, the same
    `normalise`, the same frame size the detector declares. Nothing here re-implements the
    media layer, which is the point — a scorer that approximates the detector measures the
    scorer.

    Separated from endpointing so a threshold sweep costs one model pass rather than one
    per threshold. That is not only speed: it also guarantees every row of a sweep is
    reading **identical** probabilities, so a difference between rows is the endpointer's
    and cannot be the detector's.
    """
    source = WavFileSource(path)
    vad.reset()
    samples: list[float] = []
    for packet in source.packets():
        samples.extend(normalise(packet, source.fmt).samples)
    return [vad.speech_probability(frame) for frame in frames(samples, vad.frame_samples)]


def endpoint(probs: Sequence[float], settings: EndpointSettings, frame_samples: int) -> list[Span]:
    """Replay the endpointer over cached probabilities. No model, no audio (`D96`)."""
    endpointer = Endpointer(settings=settings, frame_samples=frame_samples)
    out: list[Span] = []
    for p in probs:
        segment = endpointer.push(p)
        if segment is not None:
            out.append(Span(segment.t_start_ms / 1000.0, segment.t_end_ms / 1000.0))
    tail = endpointer.flush()
    if tail is not None:
        out.append(Span(tail.t_start_ms / 1000.0, tail.t_end_ms / 1000.0))
    return out


def build_vad(name: str) -> VoiceActivityDetector:
    if name == "energy":
        from readycall.adapters.vad.energy import EnergyVad

        return EnergyVad()
    if name == "silero":
        from readycall.adapters.vad.silero import SileroVad

        return SileroVad()
    raise SystemExit(f"unknown vad {name!r} - expected energy or silero")


def sweep(
    args: argparse.Namespace,
    vad: VoiceActivityDetector,
    annotations: dict[str, list[Span]],
    base: EndpointSettings,
) -> int:
    """One detector pass, many thresholds — so every row reads identical probabilities.

    The trade this table exposes is the whole question: a lower threshold shows the model
    more of what the caller said, and eventually starts handing it audio nobody spoke in,
    which is the input `B14` measured at 8.6 seconds and invented Thai.
    """
    cached: list[tuple[str, list[float], list[Span]]] = []
    for stem in sorted(annotations):
        wav = args.calls / f"{stem}.wav"
        if wav.exists():
            cached.append((stem, probabilities(wav, vad), annotations[stem]))
    if not cached:
        raise SystemExit("no wav files to score")

    lines = [
        f"threshold sweep   vad={vad.info.name} {vad.info.version}   "
        f"min_speech={base.min_speech_ms:.0f}ms  min_silence={base.min_silence_ms:.0f}ms  "
        f"({len(cached)} calls, one detector pass)",
        "",
        f"{'threshold':>10}{'segments':>10}{'det_s':>8}{'coverage':>10}"
        f"{'recall':>8}{'false_s':>9}{'missed_s':>10}",
    ]
    for t in args.sweep:
        settings = EndpointSettings(
            threshold=t,
            min_speech_ms=base.min_speech_ms,
            min_silence_ms=base.min_silence_ms,
        )
        scores = [
            CallScore(
                stem=stem,
                duration_s=0.0,
                annotated=spans,
                detected=endpoint(probs, settings, vad.frame_samples),
            )
            for stem, probs, spans in cached
        ]
        ann = sum(s.annotated_s for s in scores)
        cov = sum(s.covered_s for s in scores)
        det = sum(s.detected_s for s in scores)
        spans_total = sum(len(s.annotated) for s in scores)
        caught = sum(s.spans_caught for s in scores)
        lines.append(
            f"{t:>10.2f}{sum(len(s.detected) for s in scores):>10}{det:>8.1f}"
            f"{(cov / ann if ann else 0):>10.3f}"
            f"{(caught / spans_total if spans_total else 0):>8.3f}"
            f"{max(0.0, det - cov):>9.1f}{ann - cov:>10.1f}"
        )

    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


def main() -> int:
    enable_utf8()  # `B1`: this file prints only ASCII, but call stems come from disk.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vad", default="silero", choices=("energy", "silero"))
    parser.add_argument("--calls", type=Path, default=DEFAULT_CALLS)
    parser.add_argument("--out", type=Path, default=None, help="write the table here too")
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help="override the D9 speech threshold (0.65)",
    )
    parser.add_argument(
        "--min-silence-ms",
        type=float,
        default=None,
        help="override the D9 minimum silence (100 ms) that ends a phrase",
    )
    parser.add_argument(
        "--sweep",
        nargs="+",
        type=float,
        default=None,
        metavar="T",
        help="score these thresholds against ONE pass of the detector, totals only",
    )
    args = parser.parse_args()

    manifest = args.calls / "segments.tsv"
    if not manifest.exists():
        raise SystemExit(
            f"no annotations at {manifest}\nRun: uv run python scripts/prepare_dataset.py --n 12"
        )

    settings = EndpointSettings(
        **{
            k: v
            for k, v in (
                ("threshold", args.threshold),
                ("min_silence_ms", args.min_silence_ms),
            )
            if v is not None
        }
    )

    vad = build_vad(args.vad)
    annotations = load_annotations(manifest)

    if args.sweep:
        return sweep(args, vad, annotations, settings)

    lines: list[str] = []
    lines.append(
        f"endpointer vs annotation   vad={vad.info.name} {vad.info.version} "
        f"({vad.info.device})   threshold={settings.threshold}   "
        f"min_speech={settings.min_speech_ms:.0f}ms  min_silence={settings.min_silence_ms:.0f}ms"
    )
    lines.append("")
    lines.append(
        f"{'call':<26}{'spans':>6}{'found':>6}{'ann_s':>8}{'det_s':>8}"
        f"{'coverage':>10}{'recall':>8}{'false_s':>9}"
    )

    scores: list[CallScore] = []
    for stem in sorted(annotations):
        wav = args.calls / f"{stem}.wav"
        if not wav.exists():
            continue
        source = WavFileSource(wav)
        score = CallScore(
            stem=stem,
            duration_s=source.duration_s,
            annotated=annotations[stem],
            detected=endpoint(probabilities(wav, vad), settings, vad.frame_samples),
        )
        scores.append(score)
        lines.append(
            f"{stem[-26:]:<26}{len(score.annotated):>6}{len(score.detected):>6}"
            f"{score.annotated_s:>8.1f}{score.detected_s:>8.1f}"
            f"{score.coverage:>10.3f}{score.span_recall:>8.3f}{score.false_alarm_s:>9.1f}"
        )

    if not scores:
        raise SystemExit(f"no wav files beside {manifest}")

    ann = sum(s.annotated_s for s in scores)
    cov = sum(s.covered_s for s in scores)
    det = sum(s.detected_s for s in scores)
    spans = sum(len(s.annotated) for s in scores)
    caught = sum(s.spans_caught for s in scores)
    lines.append("")
    lines.append(
        f"{'TOTAL':<26}{spans:>6}{sum(len(s.detected) for s in scores):>6}"
        f"{ann:>8.1f}{det:>8.1f}{(cov / ann if ann else 0):>10.3f}"
        f"{(caught / spans if spans else 0):>8.3f}{max(0.0, det - cov):>9.1f}"
    )
    lines.append("")
    lines.append(
        f"{ann - cov:.1f}s of annotated speech ({100 * (1 - cov / ann if ann else 0):.0f}%) "
        "was never shown to the model."
    )
    lines.append(
        "That is a floor under the CER that no engine change can lift - see `NEXT_SESSION` step 2."
    )

    text = "\n".join(lines)
    print(text)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
