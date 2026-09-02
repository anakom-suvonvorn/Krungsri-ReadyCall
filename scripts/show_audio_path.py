"""Walk one utterance through the whole audio path and print what each stage did.

    uv run python scripts/show_audio_path.py

The matching equivalent is `run_matching.py`: no telephony, no GPU, no database, and every
number printed is computed by the real code rather than described. It exists because the
audio path is the one slice of this system with **no screen and no sound** — the IVR has a
keypad page, the workstation has a browser tab, and this has neither, so without a tool
like this the only way to know it works is to read it.

Five stages, in the order a caller's sentence meets them:

    bytes off the wire  ->  normalise  ->  detect voice  ->  endpoint  ->  transcribe

Runs on the energy detector and the scripted engine by default, so it needs nothing
installed. `--silero` and `--engine` swap in the real ones if the `ml` extra is present.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn  # noqa: E402
from readycall.adapters.vad.energy import EnergyVad  # noqa: E402
from readycall.clock import SystemClock  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.domain.models import TranscriptTurn  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402
from readycall.media.audio import AudioFormat, Encoding, decode, normalise, rms  # noqa: E402
from readycall.media.sources import WavFileSource  # noqa: E402
from readycall.ports.stt import SttHint  # noqa: E402
from readycall.ports.vad import VoiceActivityDetector  # noqa: E402
from readycall.services.transcription.endpointer import EndpointSettings  # noqa: E402
from readycall.services.transcription.stream import (  # noqa: E402
    TranscriptionStream,
    echoes_the_prompt,
    looks_like_a_loop,
)

BAR = "=" * 78


def rule(title: str) -> None:
    print(f"\n{BAR}\n{title}\n{BAR}")


def show_normalisation() -> None:
    rule("STAGE 1  normalise - the only place that knows a phone is not 16 kHz mono")
    print(
        "Everything above this stage is promised 16 kHz mono float32, always. A phone\n"
        "line delivers none of that, so it is converted once, here, at the edge.\n"
    )
    print(f"  {'what arrived':<34}{'bytes':>7}{'->':^6}{'samples':>9}{'rate':>8}")
    print("  " + "-" * 62)
    cases = [
        (
            "8 kHz mono mu-law (US, Japan)",
            AudioFormat(Encoding.PCM8_ULAW, 8000, 1),
            bytes([0x7F] * 160),
        ),
        (
            "8 kHz mono A-law (THAILAND)",
            AudioFormat(Encoding.PCM8_ALAW, 8000, 1),
            bytes([0x2A] * 160),
        ),
        ("8 kHz STEREO mu-law", AudioFormat(Encoding.PCM8_ULAW, 8000, 2), bytes([0x7F] * 320)),
        (
            "16 kHz mono 16-bit PCM",
            AudioFormat(Encoding.PCM16, 16000, 1),
            bytes([0x00, 0x40] * 320),
        ),
    ]
    for label, fmt, payload in cases:
        frame = normalise(payload, fmt)
        print(
            f"  {label:<34}{len(payload):>7}{'->':^6}{len(frame.samples):>9}{frame.sample_rate:>8}"
        )
    print(
        "\n  Every row is 20 ms of audio and every row comes out as 320 samples at 16 kHz.\n"
        "  Nothing above this line ever has to ask which one it got."
    )

    print("\n  Why BOTH companding laws exist here, shown rather than argued:")
    idle = bytes([0xD5] * 8)
    right = decode(idle, AudioFormat(Encoding.PCM8_ALAW))
    wrong = decode(idle, AudioFormat(Encoding.PCM8_ULAW))
    print(f"    A-law idle bytes read correctly as A-law  -> level {rms(right):.4f}  (silence)")
    print(f"    the same bytes read as mu-law by mistake  -> level {rms(wrong):.4f}  (LOUD)")
    print(
        "    It does not crash. It produces loud plausible noise, and the first thing\n"
        "    anybody blames is the microphone or the model. Thailand is A-law."
    )


def show_detection(vad: VoiceActivityDetector, path: Path) -> None:
    rule("STAGE 2 + 3  detect voice, then decide where the sentence starts and ends")
    settings = EndpointSettings()
    print(
        "The detector answers one narrow question per 32 ms frame: is anyone speaking?\n"
        "It returns a PROBABILITY, not a verdict. Deciding where an utterance begins and\n"
        "ends is a separate job with its own rules, and those rules are inherited from the\n"
        "team's earlier Thai project rather than invented here:\n"
    )
    print(f"    speech threshold      {settings.threshold}     (Silero's own default is 0.5)")
    print(f"    minimum speech        {settings.min_speech_ms:.0f} ms   below this it is a cough")
    print(f"    minimum silence       {settings.min_silence_ms:.0f} ms   Thai pauses mid-clause")
    print(f"    pad BEFORE the start  {settings.pad_before_ms:.0f} ms   <- the important one")
    print(f"    pad AFTER the end     {settings.pad_after_ms:.0f} ms")
    print(
        "\n  The leading pad is the one worth understanding. Whisper clips the first\n"
        "  syllable without it, and in Thai the first syllable often carries the tone that\n"
        "  decides which word it is. So the segment starts 120 ms BEFORE the detector\n"
        f"  noticed anybody - {int(settings.pad_before_ms / 1000 * 16000)} samples of"
        " audio it would otherwise have thrown away.\n"
    )

    source = WavFileSource(path)
    print(f"  Reading {path.name} ({source.duration_s:.1f}s, {source.fmt.sample_rate} Hz)\n")
    print(f"  {'time':>8}  {'level':>7}  {'speech?':>8}   what the detector saw")
    print("  " + "-" * 62)

    position_ms = 0
    shown = 0
    last_state = None
    for packet in source.packets():
        frame = normalise(packet, source.fmt, t_start_ms=position_ms)
        position_ms += 20
        level = rms(frame.samples)
        # One probability per VAD frame; the packet is 320 samples and the VAD wants 512,
        # so this is only for display - the real path reframes properly.
        probability = vad.speech_probability(
            list(frame.samples)[: vad.frame_samples]
            + [0.0] * max(0, vad.frame_samples - len(frame.samples))
        )
        state = probability >= EndpointSettings().threshold
        if state != last_state and shown < 14:
            mark = "SPEECH" if state else "silence"
            print(
                f"  {position_ms / 1000:>7.2f}s  {level:>7.4f}  {probability:>8.2f}   "
                f"{'--> ' + mark if state else '<-- back to ' + mark}"
            )
            shown += 1
        last_state = state
    print("\n  Those transitions are what the endpointer turns into whole sentences.")


async def show_transcription(path: Path, engine_name: str, vad: VoiceActivityDetector) -> None:
    rule("STAGE 4 + 5  transcribe, and refuse anything that looks invented")
    pack = DomainPack.load(ROOT / "config")
    hint = SttHint(language="th", vocabulary=pack.stt_vocabulary)
    turns: list[TranscriptTurn] = []

    async def sink(turn: TranscriptTurn) -> None:
        turns.append(turn)

    if engine_name == "scripted":
        engine = ScriptedSttEngine(
            [
                ScriptedTurn(text="สวัสดีครับ ผมขอแจ้งอุบัติเหตุรถยนต์", t_start_ms=0, t_end_ms=900),
                ScriptedTurn(text="รถผมชนกับมอเตอร์ไซค์ที่แยกอโศก", t_start_ms=1400, t_end_ms=2500),
                ScriptedTurn(text="ไม่มีใครบาดเจ็บครับ แต่รถขับต่อไม่ได้", t_start_ms=3100, t_end_ms=3800),
            ]
        )
    else:  # pragma: no cover - needs the ml extra
        from readycall.adapters.stt.faster_whisper import FasterWhisperEngine

        engine = FasterWhisperEngine(model=engine_name)  # type: ignore[assignment]
    await engine.warmup()

    stream = TranscriptionStream(
        call_session_id="call_demo",
        vad=vad,
        stt=engine,
        clock=SystemClock(),
        on_turn=sink,
        hint=hint,
    )
    await stream.start()
    source = WavFileSource(path)
    position_ms = 0
    for packet in source.packets():
        frame = normalise(packet, source.fmt, t_start_ms=position_ms)
        position_ms += 20
        await stream.feed(frame)
    await stream.finish()
    await engine.close()

    print(f"  engine: {engine.info.name} ({engine.info.model} on {engine.info.device})\n")
    if not turns:
        print("  no turns - the audio contained nothing the detector called speech")
    for turn in turns:
        print(
            f"  #{turn.seq}  {turn.t_start_ms / 1000:>6.2f}s - {turn.t_end_ms / 1000:>5.2f}s"
            f"  {'final' if turn.is_final else 'CUT OFF':>7}   {turn.text}"
        )

    print(
        "\n  Those go straight to `IntakeService.on_turn`, which is the method the intake\n"
        "  has had since the offer was built and nothing was calling."
    )

    rule("THE THREE GUARDS  (why they exist: a real GPU run, not a theory)")
    print(
        "Fed one second of DIGITAL SILENCE, Whisper on this laptop took 8578 ms and\n"
        "returned invented Thai. Fed real speech energy it took 155 ms. So a detector\n"
        "false positive is not a junk line on a screen, it is a 55x delay that blocks\n"
        "every real sentence queued behind it.\n\n"
        "Worse: fed our own vocabulary hint, it handed those very words back as if the\n"
        "caller had said them - and those are exactly the words that make a brief look\n"
        "credible. Three guards catch it. Here they are, judging real strings:\n"
    )
    samples = [
        ("what the GPU returned on silence", "สินไหม? กรมธรรม์, ผู้เอาประกัน, ผู้เอาประกัน"),
        ("a repetition loop", "ครับ ครับ ครับ ครับ"),
        ("a genuine caller sentence", "ผมขอสอบถามเรื่องสินไหม ของกรมธรรม์ ที่ทำไว้ครับ"),
    ]
    print(f"  {'':<34}{'loop?':>8}{'echo?':>8}   verdict")
    print("  " + "-" * 68)
    for label, text in samples:
        loop = looks_like_a_loop(text)
        echo = echoes_the_prompt(text, hint)
        verdict = "REFUSED" if (loop or echo) else "reaches the agent"
        print(f"  {label:<34}{loop!s:>8}{echo!s:>8}   {verdict}")
    print(
        f"\n  ({len(pack.stt_vocabulary)} vocabulary terms, read from config/stt_vocabulary.yaml)"
    )
    print(
        "  The third row is the one that matters most: a guard that ate real sentences\n"
        "  containing insurance words would delete exactly the calls it exists to help."
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--audio",
        default=str(ROOT / "tests" / "audio" / "three_utterances.wav"),
        help="a 16-bit WAV. Generate the default with scripts/make_test_audio.py",
    )
    parser.add_argument("--silero", action="store_true", help="use the real detector (ml extra)")
    parser.add_argument(
        "--engine", default="scripted", help="scripted (default), or a faster-whisper model name"
    )
    args = parser.parse_args()

    enable_utf8()
    path = Path(args.audio)
    if not path.exists():
        print(f"no audio at {path}")
        print("generate it with:  uv run python scripts/make_test_audio.py")
        return 2

    vad: VoiceActivityDetector = EnergyVad()
    if args.silero:  # pragma: no cover - needs the ml extra
        from readycall.adapters.vad.silero import SileroVad

        vad = SileroVad()

    print(BAR)
    print("THE AUDIO PATH, one stage at a time")
    print(f"  audio    {path.name}")
    print(f"  detector {vad.info.name}")
    print(f"  engine   {args.engine}")
    print(BAR)
    print(
        "\nNOTE: the default audio is three synthetic tone bursts, NOT speech. It proves\n"
        "the pipeline moves and says nothing about how well a model hears Thai. Point\n"
        "--audio at a real recording for that."
    )

    show_normalisation()
    show_detection(vad, path)
    vad.reset()
    await show_transcription(path, args.engine, vad)
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
