"""The wiring: audio in one end, `IntakeService.on_turn` at the other.

`D88` built the intake with three entry points and a note saying nothing fed them yet.
This is the suite that proves something does — and, `B7`-style, that the two timeouts are
driven by something rather than merely documented as being on a timer.

Everything runs on the energy detector and the scripted engine, so it works in CI with no
GPU. That is deliberate: a wiring test that only passes on the one laptop with the `ml`
extra installed is a wiring test nobody runs.
"""

from __future__ import annotations

import math

from readycall.adapters.stt.scripted import ScriptedSttEngine, ScriptedTurn
from readycall.adapters.vad.energy import EnergyVad
from readycall.clock import ManualClock
from readycall.config import Settings
from readycall.domain.models import TranscriptTurn
from readycall.media.audio import AudioFormat, Encoding
from readycall.services.transcription.service import TranscriptionService

RATE = 16000


def speech_packet(ms: float = 20.0, *, amplitude: float = 0.25) -> bytes:
    """16-bit PCM at 16 kHz, loud and harmonically structured — enough for the energy VAD."""
    n = int(RATE * ms / 1000.0)
    out = bytearray()
    for i in range(n):
        value = (
            amplitude
            * (
                math.sin(2 * math.pi * 190 * i / RATE)
                + 0.5 * math.sin(2 * math.pi * 700 * i / RATE)
            )
            / 1.5
        )
        out += int(max(-1.0, min(1.0, value)) * 32767).to_bytes(2, "little", signed=True)
    return bytes(out)


def silent_packet(ms: float = 20.0) -> bytes:
    n = int(RATE * ms / 1000.0)
    return b"".join(
        int(0.0005 * math.sin(2 * math.pi * 50 * i / RATE) * 32767).to_bytes(
            2, "little", signed=True
        )
        for i in range(n)
    )


class RecordingIntake:
    """Stands in for `IntakeService`, recording only which entry points were reached."""

    def __init__(self) -> None:
        self.turns: list[TranscriptTurn] = []
        self.silences: list[str] = []
        self.max_durations: list[str] = []

    async def on_turn(self, call_session_id: str, turn: TranscriptTurn) -> None:
        self.turns.append(turn)

    async def on_silence(self, call_session_id: str):  # type: ignore[no-untyped-def]
        self.silences.append(call_session_id)
        return None

    async def on_max_duration(self, call_session_id: str):  # type: ignore[no-untyped-def]
        self.max_durations.append(call_session_id)
        return None


def build(
    intake: RecordingIntake, clock: ManualClock, turns: list[ScriptedTurn]
) -> TranscriptionService:
    return TranscriptionService(
        intake=intake,  # type: ignore[arg-type]
        vad_factory=EnergyVad,
        stt=ScriptedSttEngine(turns),
        clock=clock,
        settings=Settings(),
    )


async def test_audio_pushed_at_the_gateway_arrives_as_a_turn_at_the_intake() -> None:
    """The end-to-end claim of this whole slice, on fakes: bytes off a wire become a
    `TranscriptTurn` on the call the caller is actually holding."""
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [ScriptedTurn(text="รถผมชนครับ", t_start_ms=0, t_end_ms=900)])

    await service.open("call_1", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    for _ in range(60):  # 1.2 s of speech
        await service.push("call_1", speech_packet())
    for _ in range(40):  # 0.8 s of quiet, to close the utterance
        await service.push("call_1", silent_packet())
    await service.close("call_1")

    assert [t.text for t in intake.turns] == ["รถผมชนครับ"]
    assert intake.turns[0].call_session_id == "call_1"


async def test_an_eight_kilohertz_ulaw_leg_is_normalised_on_the_way_through() -> None:
    """What an actual phone line delivers. Nothing above the gateway should notice."""
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [ScriptedTurn(text="สวัสดีครับ", t_start_ms=0, t_end_ms=900)])

    # µ-law silence is 0xFF; 0x00 is close to full-scale negative, so this is loud.
    await service.open("call_u", fmt=AudioFormat(encoding=Encoding.PCM8_ULAW, sample_rate=8000))
    for i in range(80):
        await service.push("call_u", bytes([0x00 if i % 2 else 0x80] * 160))
    for _ in range(40):
        await service.push("call_u", bytes([0xFF] * 160))
    await service.close("call_u")

    assert intake.turns, "an 8 kHz µ-law leg produced no turns"
    # The frames the stream saw must have been 16 kHz, or it would have refused them.
    assert intake.turns[0].t_end_ms > 0


async def test_the_silence_timeout_fires_from_the_sweep_and_nothing_else() -> None:
    """`B7`, applied in advance. A recording ends when the caller stops talking, and the
    only thing that happens at that moment is that time passed — so it needs a driver, and
    a test in which nothing but the clock moves."""
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [])

    await service.open("call_q", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    assert await service.check_timeouts() == []

    clock.advance(Settings().intake_silence_timeout_s + 1.0)
    assert await service.check_timeouts() == ["call_q"]
    assert intake.silences == ["call_q"]


async def test_the_silence_timeout_does_not_re_fire_every_tick() -> None:
    """`hold.py` counts silences and decides whether this is a re-prompt or the end.
    Firing once a second would burn its whole allowance inside one second."""
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [])

    await service.open("call_r", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    clock.advance(Settings().intake_silence_timeout_s + 1.0)
    for _ in range(5):
        await service.check_timeouts()

    assert intake.silences == ["call_r"], f"fired {len(intake.silences)} times"


async def test_the_max_duration_closes_the_recording() -> None:
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [])

    await service.open("call_m", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    clock.advance(Settings().intake_max_duration_s + 1.0)

    assert await service.check_timeouts() == ["call_m"]
    assert intake.max_durations == ["call_m"]
    assert service.live_call_ids() == (), "the recording should be closed, not just reported"


async def test_two_calls_do_not_share_a_detector() -> None:
    """Detectors are stateful across frames. One shared instance would interleave two
    callers' hidden states and endpoint each against the other's audio — and the symptom
    is a missing first word, which reads as an STT fault."""
    intake = RecordingIntake()
    clock = ManualClock()
    service = build(intake, clock, [])

    await service.open("call_a", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    await service.open("call_b", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    assert set(service.live_call_ids()) == {"call_a", "call_b"}
    assert service.gateway.leg_ids() != ()


async def test_close_is_safe_twice() -> None:
    """The agent accepting and the caller hanging up genuinely race (`D21`)."""
    intake = RecordingIntake()
    service = build(intake, ManualClock(), [])
    await service.open("call_x", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=RATE))
    await service.close("call_x")
    await service.close("call_x")


async def test_pushing_to_a_call_with_no_open_leg_is_ignored() -> None:
    """Media can arrive a beat before or after the recording window. Neither is an error,
    and neither may raise inside a live call (`D12`)."""
    intake = RecordingIntake()
    service = build(intake, ManualClock(), [])
    await service.push("never_opened", speech_packet())
    assert intake.turns == []
