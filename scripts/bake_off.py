"""Measure the STT engines against each other on the same audio (`D30`).

    uv run python scripts/bake_off.py --list
    uv run python scripts/bake_off.py --engines scripted --audio tests/audio/*.wav
    uv run python scripts/bake_off.py --engines faster_whisper_tiny --audio my.wav

`D30` is explicit that the engine is chosen on **measurements, not reputation**, and this
is the thing that produces them: WER against a reference transcript, p95 utterance latency,
and peak VRAM — for every configured engine, over identical audio, through the real
`TranscriptionStream` so the endpointing and the padding are the ones that ship.

**Latency is measured from utterance END, not from the start of the file.** That is the
number `ARCHITECTURE` §15 budgets (p95 < 1.5 s) and the one the caller experiences; total
wall time for a file measures throughput, which nobody is waiting on.

**WER needs a reference, and without one this reports latency and VRAM only.** Printing a
number computed against nothing would be exactly the kind of confident, plausible, wrong
result this project keeps writing bug entries about. Put a `.txt` beside each `.wav` with
the true transcript in it and the WER column fills in.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from bisect import bisect_left
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn  # noqa: E402
from readycall.adapters.vad.energy import EnergyVad  # noqa: E402
from readycall.clock import SystemClock  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.domain.models import TranscriptTurn  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402
from readycall.media.audio import normalise  # noqa: E402
from readycall.media.sources import WavFileSource  # noqa: E402
from readycall.ports.stt import SttEngine, SttHint  # noqa: E402
from readycall.ports.vad import VoiceActivityDetector  # noqa: E402
from readycall.services.transcription.stream import TranscriptionStream  # noqa: E402

#: Read from `config/`, never spelled here (`D28`) - and see `B14` for why the contents
#: of this list are load-bearing in a way a word list normally is not.
THAI_INSURANCE_TERMS = DomainPack.load(ROOT / "config").stt_vocabulary


def build_engine(name: str) -> SttEngine:
    """Engines by short name, so the table's rows are reproducible from the command line."""
    if name == "scripted":
        return ScriptedSttEngine(
            [ScriptedTurn(text="(scripted)", t_start_ms=0, t_end_ms=1000)] * 200
        )
    if name.startswith("faster_whisper"):
        from readycall.adapters.stt.faster_whisper import DEFAULT_MODEL, FasterWhisperEngine

        # faster_whisper_tiny / faster_whisper_small / faster_whisper (the Thai medium)
        suffix = name[len("faster_whisper") :].lstrip("_")
        model = suffix if suffix else DEFAULT_MODEL
        return FasterWhisperEngine(model=model)
    if name.startswith("thonburian"):
        from readycall.adapters.stt.thonburian_hf import DEFAULT_MODEL, ThonburianHfEngine

        suffix = name[len("thonburian") :].lstrip("_")
        return ThonburianHfEngine(model=suffix or DEFAULT_MODEL)
    raise SystemExit(f"unknown engine: {name}")


def word_error_rate(reference: str, hypothesis: str) -> float:
    """Standard Levenshtein WER over whitespace tokens.

    ⚠️ **Thai does not put spaces between words**, so on unsegmented Thai this is really a
    *phrase* error rate and will read pessimistically high. It is still perfectly usable
    for RANKING engines against each other on identical text, which is what `D30` asks
    for. Do not quote it as an absolute WER without segmenting first (pythainlp), and
    label it as CER if that is what is wanted.
    """
    ref, hyp = reference.split(), hypothesis.split()
    if not ref:
        return 0.0 if not hyp else 1.0
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1] / len(ref)


def vram_in_use_mb() -> float | None:
    """Device memory in use, asked of the DRIVER rather than of torch's allocator.

    The first version used `torch.cuda.max_memory_allocated()` and reported a confident
    **0 MB** for every engine. It is not wrong so much as blind: CTranslate2 allocates
    through its own CUDA allocator, so torch's counter never sees a byte of
    faster-whisper's model. `mem_get_info()` queries the driver and therefore sees
    everything on the card — including, honestly, other processes, which is why the table
    reports a delta against a baseline taken before the model loads.
    """
    try:
        import torch

        if not torch.cuda.is_available():
            return None
        free, total = torch.cuda.mem_get_info()
        return (total - free) / 1024**2
    except Exception:
        return None


@dataclass
class Run:
    engine: str
    audio: str
    text: str = ""
    latencies_ms: list[float] = field(default_factory=list)
    turns: int = 0
    audio_s: float = 0.0
    wall_s: float = 0.0
    vram_mb: float | None = None
    wer: float | None = None

    @property
    def p95_ms(self) -> float:
        if not self.latencies_ms:
            return 0.0
        ordered = sorted(self.latencies_ms)
        return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]

    #: True when the feed was paced at wall-clock speed. Decides which of the two numbers
    #: below is meaningful — and the other is printed as `n/a` rather than as a number
    #: somebody could quote.
    paced: bool = True

    @property
    def realtime_factor(self) -> float:
        return self.wall_s / self.audio_s if self.audio_s else 0.0


async def transcribe_file(
    engine: SttEngine,
    vad: VoiceActivityDetector,
    path: Path,
    *,
    engine_name: str,
    baseline_vram: float | None = None,
    realtime: bool = True,
) -> Run:
    source = WavFileSource(path)
    run = Run(engine=engine_name, audio=path.name, audio_s=source.duration_s)
    parts: list[str] = []
    # (audio position in ms, wall clock when we had fed that far). A turn ending at
    # `t_end_ms` is dated back to the moment the caller had actually finished saying it,
    # which is what the latency budget is about.
    #
    # The first version keyed a dict on the exact packet-boundary position and looked it
    # up by `turn.t_end_ms`. It never matched once - the endpointer adds `pad_after_ms`,
    # so a turn's end is 60 ms past any boundary - and every latency silently went
    # unrecorded, printing a clean p95 of 0 ms for every engine. A round zero across
    # unrelated things is `B3` exactly: the instrument, not the code.
    fed: list[tuple[int, float]] = []

    async def sink(turn: TranscriptTurn) -> None:
        arrived = time.perf_counter()
        parts.append(turn.text)
        run.turns += 1
        index = bisect_left(fed, (turn.t_end_ms, 0.0))
        if index < len(fed):
            run.latencies_ms.append((arrived - fed[index][1]) * 1000.0)
        peak = vram_in_use_mb()
        if peak is not None:
            run.vram_mb = max(run.vram_mb or 0.0, peak - (baseline_vram or 0.0))

    stream = TranscriptionStream(
        call_session_id=f"bake_{path.stem}",
        vad=vad,
        stt=engine,
        clock=SystemClock(),
        on_turn=sink,
        hint=SttHint(language="th", vocabulary=THAI_INSURANCE_TERMS),
    )
    await stream.start()

    started = time.perf_counter()
    position_ms = 0
    for packet in source.packets():
        frame = normalise(packet, source.fmt, t_start_ms=position_ms)
        position_ms += int(len(frame.samples) / 16000 * 1000)
        if realtime:
            # PACE THE FEED, or the latency column is a lie. Pushing a whole file in a
            # tight loop queues every utterance at once, so the third one waits behind
            # two inferences and the number reported is a *backlog*, not a latency. On a
            # real call utterance one is transcribed while utterance two is still being
            # spoken. Measured on `faster_whisper_tiny`: 11270 ms unpaced against 553 ms
            # paced, for identical audio and identical work.
            behind = position_ms / 1000.0 - (time.perf_counter() - started)
            if behind > 0:
                await asyncio.sleep(behind)
        fed.append((position_ms, time.perf_counter()))
        await stream.feed(frame)
    await stream.finish()
    run.wall_s = time.perf_counter() - started
    run.paced = realtime
    run.text = " ".join(parts)

    reference = path.with_suffix(".txt")
    if reference.exists():
        run.wer = word_error_rate(reference.read_text(encoding="utf-8").strip(), run.text)
    return run


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engines", nargs="+", default=["scripted"])
    parser.add_argument("--audio", nargs="+", default=[])
    parser.add_argument("--list", action="store_true", help="show the known engine names")
    parser.add_argument("--out", default="", help="write the table to a UTF-8 file")
    parser.add_argument(
        "--fast",
        action="store_true",
        help="do not pace the audio. Measures THROUGHPUT (rtf); the latency column is "
        "then meaningless and is printed as n/a",
    )
    args = parser.parse_args()

    enable_utf8()
    if args.list:
        print("engines:")
        print("  scripted                  no model, no GPU - proves the harness itself")
        print("  faster_whisper_tiny       ~75 MB, CTranslate2. The cheap real check")
        print("  faster_whisper_small      ~460 MB")
        print("  faster_whisper            biodatlab Thai medium, CT2 - the candidate")
        print("  thonburian                biodatlab Thai medium, HF fp16 - the baseline")
        print()
        print("Put a .txt beside each .wav with the true transcript to get a WER column.")
        return 0

    files = [Path(p) for pattern in args.audio for p in sorted(Path().glob(pattern))] or [
        Path(p) for p in args.audio
    ]
    files = [f for f in files if f.exists()]
    if not files:
        print("no audio files found - pass --audio path/to/*.wav")
        return 2

    runs: list[Run] = []
    for name in args.engines:
        engine = build_engine(name)
        vad = EnergyVad()
        print(f"\n=== {name} ===")
        # Sampled before the weights land, so the column is this engine's cost and not
        # the desktop compositor's 0.8 GiB (`D95`).
        baseline = vram_in_use_mb()
        await engine.warmup()
        for path in files:
            run = await transcribe_file(
                engine,
                vad,
                path,
                engine_name=name,
                baseline_vram=baseline,
                realtime=not args.fast,
            )
            runs.append(run)
            print(
                f"  {path.name:28} {run.turns:3} turns  "
                + (
                    f"p95 {run.p95_ms:7.0f}ms  rtf     n/a"
                    if run.paced
                    else f"p95     n/a  rtf {run.realtime_factor:5.2f}"
                )
                + (f"  wer {run.wer:.3f}" if run.wer is not None else "  wer   n/a")
            )
        await engine.close()

    lines = [
        "",
        "=" * 92,
        "BAKE-OFF  (`D30`) - p95 is utterance-end to turn; rtf < 1.0 means faster than real time",
        "=" * 92,
        f"{'engine':<26}{'audio':<24}{'turns':>6}{'p95 ms':>9}{'rtf':>7}{'vram MB':>10}{'WER':>8}",
        "-" * 92,
    ]
    for r in runs:
        lines.append(
            f"{r.engine:<26}{r.audio:<24}{r.turns:>6}"
            f"{(f'{r.p95_ms:.0f}' if r.paced else 'n/a'):>9}"
            f"{('n/a' if r.paced else f'{r.realtime_factor:.2f}'):>7}"
            f"{(f'{r.vram_mb:.0f}' if r.vram_mb is not None else '-'):>10}"
            f"{(f'{r.wer:.3f}' if r.wer is not None else '-'):>8}"
        )
    lines += [
        "-" * 92,
        "",
        "The budget (`ARCHITECTURE` S15): utterance end -> turn, p95 < 1.5 s.",
        "Audio is PACED at wall-clock speed by default, because an unpaced feed queues every",
        "utterance at once and reports a backlog instead of a latency. --fast measures",
        "throughput instead, and blanks the latency column rather than printing a wrong one.",
        "WER over whitespace tokens is a PHRASE error rate on unsegmented Thai - fine for",
        "ranking engines against each other, not quotable as an absolute number.",
        "Record the winner and the table in PROJECT_STATE (`D30`).",
    ]
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\nwritten to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
