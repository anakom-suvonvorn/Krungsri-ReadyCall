"""Turn the Thai call-centre dataset into audio/reference pairs the bake-off can measure.

    uv run python scripts/prepare_dataset.py --list-domains
    uv run python scripts/prepare_dataset.py --n 20
    uv run python scripts/prepare_dataset.py --n 40 --domain Insurance --max-seconds 90

**The dataset** is *Thai H2M call-center audio with script* (AI x Block, Kaggle), which the
user downloaded to `../data/` beside `FullProject/`. It is **22 GB and 6378 wav files**:
3189 calls, each with a `_human` side and an `_ai` side. This script never copies the bulk
of it — it selects a handful, writes them somewhere small, and leaves the 22 GB where it is.

**Why the human side only.** `_ai` is a recorded bot prompt: clean, studio-level, and
nothing like what our system will hear. `_human` is a real person on a real phone, which is
the thing worth measuring. Using the `_ai` side would produce a flattering number that
predicts nothing.

**What the reference transcript is, and what gets thrown away.** Their scripts are
per-segment with timestamps:

    Spk2:[16.26 - 18.26] :สิทธิพิเศษ
    Spk2:[19.46 - 21.26] :noise

Everything marked `noise` is dropped — those are the gaps — and the remaining segments are
joined in time order. **The `noise` spans are kept separately** in the manifest, because
they are ground truth for something we otherwise cannot check: whether our own endpointer
agrees about where nobody is speaking (`D9`).

⚠️ **The output is real customer speech and is NEVER committed.** The transcripts contain
names, phone numbers and account details — the sample this was written against has a
customer reciting a nine-digit number. The output directory is gitignored, and that is a
PDPA position (`D14`), not tidiness.
"""

from __future__ import annotations

import argparse
import csv
import random
import re
import shutil
import sys
import wave
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.console import enable_utf8  # noqa: E402

#: Beside `FullProject/`, not inside it — 22 GB does not belong in a git repository.
DEFAULT_DATA = ROOT.parent / "data"
DEFAULT_OUT = ROOT / "tests" / "audio" / "thai_calls"

#: `Spk2:[16.26 - 18.26] :สิทธิพิเศษ`
SEGMENT = re.compile(r"^(\w+):\[\s*([\d.]+)\s*-\s*([\d.]+)\s*\]\s*:(.*)$")

#: What their annotators wrote where there is no speech. Case-insensitive because real
#: annotation files are not consistent about it.
NON_SPEECH = {"noise", "", "-", "n/a", "na", "unk", "unknown", "silence"}


@dataclass
class Segment:
    start_s: float
    end_s: float
    text: str

    @property
    def is_speech(self) -> bool:
        return self.text.strip().lower() not in NON_SPEECH


@dataclass
class Sample:
    stem: str
    wav: Path
    script: Path
    domain: str
    segments: list[Segment] = field(default_factory=list)

    @property
    def speech(self) -> list[Segment]:
        return [s for s in self.segments if s.is_speech]

    @property
    def reference(self) -> str:
        """Segments joined with spaces.

        The spaces are OURS, not Thai — Thai does not use them (`B16`). They are here only
        so the whitespace WER in `bake_off.py` has something to tokenise, and both sides of
        that comparison get the same treatment. A character error rate would not need them;
        see the caveat in `word_error_rate`.
        """
        return " ".join(s.text.strip() for s in self.speech)

    @property
    def duration_s(self) -> float:
        with wave.open(str(self.wav), "rb") as w:
            return w.getnframes() / w.getframerate()

    @property
    def speech_seconds(self) -> float:
        return sum(s.end_s - s.start_s for s in self.speech)


def parse_script(path: Path) -> list[Segment]:
    segments: list[Segment] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        match = SEGMENT.match(line)
        if match is None:
            continue
        _speaker, start, end, text = match.groups()
        segments.append(Segment(float(start), float(end), text.strip()))
    return sorted(segments, key=lambda s: s.start_s)


def load_index(data_dir: Path) -> list[Sample]:
    """Read `metadata.csv` and pair each call's human audio with its human script."""
    meta = data_dir / "metadata.csv"
    if not meta.exists():
        raise SystemExit(f"no metadata.csv in {data_dir}")

    samples: list[Sample] = []
    # utf-8-sig: the file was exported from Excel and carries a BOM, which otherwise ends
    # up glued to the first column name and makes the lookup fail with a confusing message.
    with meta.open(encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            wav_name = (row.get("AudioNameLeft") or "").strip()
            script_name = (row.get("ScriptLeft") or "").strip()
            if not wav_name or not script_name:
                continue
            wav = data_dir / "audiofiles" / wav_name
            script = data_dir / "scriptfiles" / script_name
            if not wav.exists() or not script.exists():
                continue
            samples.append(
                Sample(
                    stem=wav.stem,
                    wav=wav,
                    script=script,
                    domain=(row.get("Domain") or "unknown").strip(),
                )
            )
    return samples


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", default=str(DEFAULT_DATA))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--n", type=int, default=20, help="how many calls to prepare")
    parser.add_argument("--domain", default="", help="only this domain (see --list-domains)")
    parser.add_argument("--seed", type=int, default=42, help="the selection is reproducible")
    parser.add_argument(
        "--max-seconds",
        type=float,
        default=120.0,
        help="skip calls longer than this - a 4-minute call is 8 minutes of GPU time",
    )
    parser.add_argument(
        "--min-speech-seconds",
        type=float,
        default=10.0,
        help="skip calls that are almost entirely noise - they measure nothing",
    )
    parser.add_argument("--list-domains", action="store_true")
    args = parser.parse_args()

    enable_utf8()
    data_dir = Path(args.data)
    samples = load_index(data_dir)
    print(f"{len(samples)} calls indexed from {data_dir}")

    if args.list_domains:
        counts: dict[str, int] = {}
        for s in samples:
            counts[s.domain] = counts.get(s.domain, 0) + 1
        print("\ndomains:")
        for domain, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {domain:<28} {count:>5}")
        print("\nInsurance is the one that matches our own vocabulary, if it is in there.")
        return 0

    if args.domain:
        samples = [s for s in samples if s.domain.lower() == args.domain.lower()]
        print(f"{len(samples)} in domain {args.domain!r}")

    rng = random.Random(args.seed)
    rng.shuffle(samples)

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    chosen: list[Sample] = []
    skipped_long = skipped_quiet = 0

    for sample in samples:
        if len(chosen) >= args.n:
            break
        try:
            sample.segments = parse_script(sample.script)
            if sample.duration_s > args.max_seconds:
                skipped_long += 1
                continue
            if sample.speech_seconds < args.min_speech_seconds:
                skipped_quiet += 1
                continue
        except Exception as exc:  # pragma: no cover - one bad file must not stop the run
            print(f"  skipping {sample.stem}: {type(exc).__name__} {exc}")
            continue
        chosen.append(sample)

    if not chosen:
        print("nothing selected - loosen --max-seconds or --min-speech-seconds")
        return 2

    print(f"\nselected {len(chosen)}  (skipped {skipped_long} too long, {skipped_quiet} too quiet)")
    print(f"writing to {out}\n")
    print(f"  {'call':<44}{'dur':>7}{'speech':>8}{'segs':>6}{'chars':>7}")
    print("  " + "-" * 72)

    total_speech = 0.0
    for sample in chosen:
        shutil.copyfile(sample.wav, out / f"{sample.stem}.wav")
        (out / f"{sample.stem}.txt").write_text(sample.reference, encoding="utf-8")
        total_speech += sample.speech_seconds
        print(
            f"  {sample.stem[:42]:<44}{sample.duration_s:>6.0f}s"
            f"{sample.speech_seconds:>7.0f}s{len(sample.speech):>6}"
            f"{len(sample.reference):>7}"
        )

    # The noise spans, kept because they are ground truth our own endpointer can be
    # scored against - the one thing a WER number cannot tell us.
    manifest = out / "segments.tsv"
    with manifest.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, delimiter="\t")
        writer.writerow(["call", "start_s", "end_s", "kind", "text"])
        for sample in chosen:
            for seg in sample.segments:
                writer.writerow(
                    [
                        sample.stem,
                        f"{seg.start_s:.2f}",
                        f"{seg.end_s:.2f}",
                        "speech" if seg.is_speech else "noise",
                        seg.text if seg.is_speech else "",
                    ]
                )

    print(f"\n  {total_speech:.0f}s of real Thai speech across {len(chosen)} calls")
    print(f"  segment ground truth -> {manifest.name}")
    print("\nMeasure an engine against it:")
    print(f"  uv run python scripts/bake_off.py --engines thonburian --audio {out}/*.wav")
    print("\n⚠️  This directory is gitignored and must stay that way: it is real customer")
    print("    speech, with names and account numbers in the transcripts (`D14`).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
