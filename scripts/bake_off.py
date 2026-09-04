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
import statistics
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
HINT = SttHint(language="th", vocabulary=THAI_INSURANCE_TERMS)


def build_engine(name: str) -> SttEngine:
    """Engines by short name, so the table's rows are reproducible from a command line.

    `name` may carry an explicit checkpoint after a colon:

        thonburian                          the Thai medium checkpoint, HF pipeline
        thonburian:biodatlab/whisper-th-large-v3-combined
        faster_whisper:models/whisper-th-medium-combined-ct2
        faster_whisper:tiny                 a generic size - no conversion needed
        typhoon                             NeMo FastConformer (needs the `asr` extra)
    """
    engine_name, _, model = name.partition(":")

    if engine_name == "scripted":
        return ScriptedSttEngine(
            [ScriptedTurn(text="(scripted)", t_start_ms=0, t_end_ms=1000)] * 500
        )
    if engine_name == "faster_whisper":
        from readycall.adapters.stt.faster_whisper import DEFAULT_MODEL, FasterWhisperEngine

        return FasterWhisperEngine(model=model or DEFAULT_MODEL)
    if engine_name == "thonburian":
        from readycall.adapters.stt.thonburian_hf import DEFAULT_MODEL, ThonburianHfEngine

        return ThonburianHfEngine(model=model or DEFAULT_MODEL)
    if engine_name == "typhoon":
        from readycall.adapters.stt.typhoon_asr import DEFAULT_MODEL, TyphoonAsrEngine

        return TyphoonAsrEngine(model=model or DEFAULT_MODEL)
    raise SystemExit(
        f"unknown engine: {name!r}. Known: scripted, thonburian, faster_whisper, typhoon "
        "(each optionally followed by ':<checkpoint>')"
    )


def _levenshtein(ref: list[str], hyp: list[str]) -> int:
    if not ref:
        return len(hyp)
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, start=1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, start=1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def character_error_rate(reference: str, hypothesis: str) -> float:
    """CER — **the metric for Thai**, and the one this table ranks on (`B18`).

    Whitespace is stripped from both sides before comparing, because **Thai does not use
    it**: our transcriptions come back as one continuous string, and the reference is
    segment-joined with spaces we inserted ourselves. Comparing those as *word* tokens is
    comparing one arbitrary segmentation against another.

    That is not a theoretical objection. The first run against real Thai audio reported a
    word error rate of **1.000, 1.118, 1.071, 0.941** — over 100% on two of four files,
    which is only possible when essentially nothing lines up. The model was fine; the
    ruler was wrong, and it was wrong in exactly the way this file's own docstring had
    warned about two days earlier.
    """
    ref = [c for c in reference if not c.isspace()]
    hyp = [c for c in hypothesis if not c.isspace()]
    if not ref:
        return 0.0 if not hyp else 1.0
    return _levenshtein(ref, hyp) / len(ref)


def _strip_latin_runs(text: str) -> str:
    """Remove Latin-script runs (and the digits/punctuation inside them).

    `Q28`: the dataset's human transcripts write brand and place names in Latin —
    `True move`, `Mezzox Drip Cafe`, `Frosen Khaoyai`, `Router`, `L O S` — while the model
    correctly writes them in Thai (`ทูมู`, `เมโซเอ็กซ์ดิสกาแฟ`). Every character differs, so a
    RIGHT answer is charged as a total miss.
    """
    out: list[str] = []
    for ch in text:
        if "a" <= ch.lower() <= "z":
            continue
        out.append(ch)
    return "".join(out)


def thai_only_cer(reference: str, hypothesis: str) -> float:
    """CER with Latin-script spans removed from BOTH sides (`Q28`).

    **This is a diagnostic, not a second ranking metric, and the distinction matters.**
    Reported beside the headline CER so the gap between them says how much of the error is
    the script mismatch rather than the model. A large gap means the headline is pessimistic
    and by roughly how much; a small gap means the headline is what it looks like.

    It is deliberately NOT what engines are ranked on. Ranking on it would mean quietly
    excluding the words the model is most likely to get wrong, which flatters every engine
    equally and hides a real weakness — an insurance line will hear brand names too.
    """
    return character_error_rate(_strip_latin_runs(reference), _strip_latin_runs(hypothesis))


def word_error_rate(reference: str, hypothesis: str) -> float:
    """WER over whitespace tokens. Kept, reported second, and **not** what to judge Thai on.

    It is meaningful for a language that delimits its words, and it is meaningful against a
    *segmented* Thai reference (pythainlp). Against raw Thai it measures the segmentation,
    not the transcription — see `character_error_rate`.
    """
    return _levenshtein(reference.split(), hypothesis.split()) / max(1, len(reference.split()))


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


#: Whisper encodes a fixed 30 s window whatever you hand it (`D99`). One second of
#: speech and twenty-nine seconds of zero padding cost the same as a full window, so the
#: number of CALLS matters far more than their length.
WHISPER_WINDOW_S = 30.0


class TimedEngine:
    """Wraps an engine to measure how long it is actually busy, and how often it is asked.

    Both numbers are invisible from outside. Wall-clock time in a paced run tells you only
    that pacing worked; what decides whether this design keeps up is the fraction of the
    call the GPU spends inside the model, and that has to be timed at the call site.
    """

    def __init__(self, inner: SttEngine) -> None:
        self._inner = inner
        self.busy_s = 0.0
        self.calls = 0

    @property
    def info(self) -> object:
        return self._inner.info

    async def transcribe_utterance(self, frames, *, hint=None):  # type: ignore[no-untyped-def]
        started = time.perf_counter()
        try:
            return await self._inner.transcribe_utterance(frames, hint=hint)
        finally:
            self.busy_s += time.perf_counter() - started
            self.calls += 1

    def stream(self, frames):  # type: ignore[no-untyped-def]
        return self._inner.stream(frames)

    async def warmup(self) -> None:
        await self._inner.warmup()

    async def close(self) -> None:
        await self._inner.close()


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
    cer: float | None = None
    cer_th: float | None = None
    wer: float | None = None
    #: Seconds spent inside the model, and how many times it was called (`WHISPER_WINDOW_S`).
    busy_s: float = 0.0
    model_calls: int = 0

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

    @property
    def busy_fraction(self) -> float:
        """Seconds in the model per second of audio. **The number that decides whether
        this keeps up**, and the one `rtf` was a proxy for. Above 1.00 the transcriber
        falls further behind with every sentence and never recovers, which is the shape
        behind the 23-59 s latencies in the paced run. Unlike `rtf` it is meaningful
        whether or not the feed was paced."""
        return self.busy_s / self.audio_s if self.audio_s else 0.0

    @property
    def pad_multiple(self) -> float:
        """How many seconds of audio Whisper actually encoded per second of real call.

        Every call to the model encodes a full 30 s window even for a one-second
        utterance, so this is `30 x calls / audio seconds`. It is the size of the prize
        for batching several utterances into one window before dispatching.

        **Meaningless for a non-Whisper engine.** A transducer such as Typhoon (`D99`) has
        no fixed window at all — it processes what it is given — so this column is
        computing a Whisper fact about a model that does not have it. Read it only on the
        Whisper rows; the honest number for a transducer is 1.0 and the column does not
        know that."""
        return WHISPER_WINDOW_S * self.model_calls / self.audio_s if self.audio_s else 0.0


async def transcribe_file(
    engine: TimedEngine,
    vad: VoiceActivityDetector,
    path: Path,
    *,
    engine_name: str,
    baseline_vram: float | None = None,
    realtime: bool = True,
    hint: SttHint | None = None,
) -> Run:
    source = WavFileSource(path)
    run = Run(engine=engine_name, audio=path.name, audio_s=source.duration_s)
    before_busy, before_calls = engine.busy_s, engine.calls
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
        stt=engine,  # type: ignore[arg-type]
        clock=SystemClock(),
        on_turn=sink,
        hint=hint,
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
    run.busy_s = engine.busy_s - before_busy
    run.model_calls = engine.calls - before_calls
    run.text = " ".join(parts)

    reference = path.with_suffix(".txt")
    if reference.exists():
        truth = reference.read_text(encoding="utf-8").strip()
        run.cer = character_error_rate(truth, run.text)
        run.cer_th = thai_only_cer(truth, run.text)
        run.wer = word_error_rate(truth, run.text)
    return run


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--engines", nargs="+", default=["scripted"])
    parser.add_argument("--audio", nargs="+", default=[])
    parser.add_argument("--list", action="store_true", help="show the known engine names")
    parser.add_argument(
        "--no-hint",
        action="store_true",
        help="do not send config/stt_vocabulary.yaml to the engine. Worth measuring rather "
        "than assuming: the hint helps in-domain and `B14` showed it can be handed straight "
        "back as invented text, so its cost off-domain is a real question (`Q27`)",
    )
    parser.add_argument(
        "--vad",
        default="energy",
        choices=["energy", "silero"],
        help="which detector finds the sentences. `energy` needs nothing; `silero` is the "
        "real one (`D9`) and is what a real measurement should use - the detector decides "
        "what the model is even asked to transcribe, so it is half of any CER number",
    )
    parser.add_argument("--out", default="", help="write the table to a UTF-8 file")
    parser.add_argument(
        "--dump",
        default="",
        help="write REFERENCE vs HYPOTHESIS for every run to a UTF-8 file. A CER is a "
        "summary of a difference, and twice now this project has ranked models on a "
        "number nobody had looked behind (`B18`, `B19`). Thai on the Windows console "
        "kills the process (`B1`), so this goes to a file - never to stdout",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="do not pace the audio. Measures THROUGHPUT (rtf); the latency column is "
        "then meaningless and is printed as n/a. NOTE (`B20`, `D100`): an unpaced feed "
        "ingests the whole file before the model has finished the first utterance, so the "
        "stream holds the entire call in its buffer. Past 120 s of audio that reaches "
        "`_MAX_BACKLOG_SAMPLES` and the oldest segments are abandoned with a warning - so "
        "--fast is only safe for ACCURACY on files shorter than that. Every latency number "
        "needs a paced run regardless",
    )
    args = parser.parse_args()

    enable_utf8()
    if args.list:
        print("engines:")
        print("  scripted                     no model, no GPU - proves the harness itself")
        print("  thonburian                   biodatlab Thai medium, fp16. THE BASELINE (`D9`)")
        print("  thonburian:<hf-id>           e.g. biodatlab/whisper-th-large-v3-combined")
        print("  faster_whisper:<dir>         a converted CT2 build (scripts/convert_ct2.py)")
        print("  faster_whisper:tiny          ~75 MB generic Whisper - a cheap plumbing check,")
        print("                               NOT a Thai result and not a bake-off row")
        print("  typhoon                      scb10x/typhoon-asr-realtime, NeMo (`D30`)")
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
        engine = TimedEngine(build_engine(name))
        vad: VoiceActivityDetector = EnergyVad()
        if args.vad == "silero":
            from readycall.adapters.vad.silero import SileroVad

            vad = SileroVad()
        print(f"\n=== {name}  (vad: {vad.info.name}) ===")
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
                hint=None if args.no_hint else HINT,
            )
            runs.append(run)
            print(
                f"  {path.name:28} {run.turns:3} turns  "
                + (
                    f"p95 {run.p95_ms:7.0f}ms  rtf     n/a"
                    if run.paced
                    else f"p95     n/a  rtf {run.realtime_factor:5.2f}"
                )
                + (f"  cer {run.cer:.3f}" if run.cer is not None else "  cer   n/a")
            )
        await engine.close()

    lines = [
        "",
        "=" * 110,
        "BAKE-OFF  (`D30`) - p95 is utterance-end to turn; rtf < 1.0 means faster than real time",
        "=" * 92,
        f"{'engine':<24}{'audio':<24}{'turns':>6}{'p95 ms':>9}{'rtf':>6}"
        f"{'busy':>6}{'pad':>6}{'vram':>6}{'CER':>7}{'CERth':>7}{'WER':>7}",
        "-" * 110,
    ]
    for r in runs:
        lines.append(
            # The TAIL of the filename, not the head: several calls in this dataset share
            # their first 18 characters (same customer id, different call), so truncating
            # from the left produced rows that could not be told apart - and a join on
            # that label silently collapsed 9 of 20 rows during an analysis.
            f"{r.engine:<24}{r.audio[-22:]:<24}{r.turns:>6}"
            f"{(f'{r.p95_ms:.0f}' if r.paced else 'n/a'):>9}"
            f"{('n/a' if r.paced else f'{r.realtime_factor:.2f}'):>6}"
            f"{r.busy_fraction:>6.2f}{r.pad_multiple:>6.1f}"
            f"{(f'{r.vram_mb:.0f}' if r.vram_mb is not None else '-'):>6}"
            f"{(f'{r.cer:.3f}' if r.cer is not None else '-'):>7}"
            f"{(f'{r.cer_th:.3f}' if r.cer_th is not None else '-'):>7}"
            f"{(f'{r.wer:.3f}' if r.wer is not None else '-'):>7}"
        )
    # ---- per-engine aggregate ------------------------------------------------------
    #
    # Reported because reading a column of 20 numbers by eye is how a wrong summary gets
    # quoted, and because **the median is not stable at this sample size**. Two runs of an
    # identical configuration moved the median CER from 0.087 to 0.124 while the mean went
    # 0.128 to 0.130 — int8 inference is not bit-reproducible, and with 20 spread-out
    # samples a couple of them crossing the middle drags the median a long way. Rank on the
    # MEAN; the median is printed beside it so a large gap between them warns that one call
    # is doing the talking.
    by_engine: dict[str, list[Run]] = {}
    for r in runs:
        by_engine.setdefault(r.engine, []).append(r)

    lines += [
        "-" * 110,
        "",
        "PER-ENGINE AGGREGATE  (rank on CER mean - see the note below)",
        f"{'engine':<40}{'n':>4}{'CER mean':>10}{'CER med':>9}{'CER worst':>11}"
        f"{'busy med':>10}{'busy worst':>12}",
        "-" * 110,
    ]
    for name, group in by_engine.items():
        cers = [r.cer for r in group if r.cer is not None]
        busies = [r.busy_fraction for r in group]
        lines.append(
            f"{name[:38]:<40}{len(group):>4}"
            + (
                f"{statistics.mean(cers):>10.3f}{statistics.median(cers):>9.3f}{max(cers):>11.3f}"
                if cers
                else f"{'-':>10}{'-':>9}{'-':>11}"
            )
            + f"{statistics.median(busies):>10.2f}{max(busies):>12.2f}"
        )

    lines += [
        "-" * 110,
        "",
        "RANK ON THE CER MEAN, not the median. The median is unstable at 20 samples: two",
        "runs of an IDENTICAL configuration moved it 0.087 -> 0.124 while the mean moved",
        "0.128 -> 0.130. int8 inference is not bit-reproducible, and a couple of calls",
        "crossing the middle drags a median a long way. A big mean-vs-median gap means one",
        "call is dominating - go and read that call in --dump before quoting either number.",
        "",
        "CER is the headline and the ONLY thing engines are ranked on. CERth is the same",
        "score with Latin-script spans removed from both sides - a DIAGNOSTIC, not a second",
        "ranking (`Q28`). The reference writes brand names in Latin (True move, Router) and",
        "the model correctly transliterates them into Thai, so a right answer is charged in",
        "full. The GAP between the two columns is how much of the error is that mismatch.",
        "Ranking on CERth would excuse every engine from the words it is most likely to get",
        "wrong, equally, which hides a real weakness rather than measuring it.",
        "",
        "busy = seconds inside the model per second of audio. THE number that decides whether",
        "this keeps up: above 1.00 the transcriber falls further behind every sentence and",
        "never recovers. Meaningful in BOTH modes, unlike rtf, which is blank in a paced run",
        "because pacing makes wall time equal the audio length by construction.",
        "",
        "pad = seconds Whisper actually ENCODED per second of call. It pads every clip to 30 s,",
        "so it is a WHISPER fact and is meaningless on a transducer row (typhoon): that",
        "architecture has no fixed window, and its honest pad is 1.0 whatever this prints.",
        "so twelve one-second utterances cost twelve full windows. This is the size of the",
        "prize for batching utterances into one window before dispatch.",
        "",
        "The budget (`ARCHITECTURE` S15): utterance end -> turn, p95 < 1.5 s.",
        "Audio is PACED at wall-clock speed by default, because an unpaced feed queues every",
        "utterance at once and reports a backlog instead of a latency. --fast measures",
        "throughput instead, and blanks the latency column rather than printing a wrong one.",
        "RANK ON CER. Thai does not put spaces between words, so WER compares one arbitrary",
        "segmentation against another - it read 0.94-1.12 on a model that was working fine",
        "(`B18`). WER is kept only because it is meaningful against a SEGMENTED reference.",
        "Record the winner and the table in PROJECT_STATE (`D30`).",
    ]
    report = "\n".join(lines)
    print(report)
    if args.out:
        Path(args.out).write_text(report + "\n", encoding="utf-8")
        print(f"\nwritten to {args.out}")
    if args.dump:
        dump: list[str] = [
            "REFERENCE vs HYPOTHESIS, per run.",
            "",
            "The reference joins only the spans a human annotated as speech; the pipeline",
            "transcribes everything the detector finds. Read the pair before believing the",
            "CER above it - the number cannot tell a wrong word from a missing one, and the",
            "fixes are opposite.",
            "",
        ]
        for r in runs:
            ref_path = next((f for f in files if f.name == r.audio), None)
            truth = ""
            if ref_path is not None and ref_path.with_suffix(".txt").exists():
                truth = ref_path.with_suffix(".txt").read_text(encoding="utf-8").strip()
            dump += [
                "=" * 92,
                f"{r.engine}   {r.audio}   {r.turns} turns   "
                + (f"CER {r.cer:.3f}" if r.cer is not None else "CER n/a"),
                "-" * 92,
                f"REF  ({len([c for c in truth if not c.isspace()])} chars)",
                truth or "(no reference)",
                "",
                f"HYP  ({len([c for c in r.text if not c.isspace()])} chars)",
                r.text or "(nothing transcribed)",
                "",
            ]
        Path(args.dump).write_text("\n".join(dump) + "\n", encoding="utf-8")
        print(f"transcripts written to {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
