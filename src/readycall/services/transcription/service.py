"""Wiring the audio path to the intake that has been waiting for it since `D88`.

`services/intake/service.py` has had three entry points since P3 step 4a — `on_turn`,
`on_silence`, `on_max_duration` — and the note beside them said *"nothing feeds this yet;
the transcriber is the next slice"*. This is that slice. It owns one `MediaGateway` and
one `TranscriptionStream` per recording call, and it is the thing that finally calls them.

**The two timeouts are driven here, and only from the sweep** (`B7`). A recording ends when
the caller falls silent for `INTAKE_SILENCE_TIMEOUT_S` or hits `INTAKE_MAX_DURATION_S`, and
both happen *because time passed* — there is no request in flight to notice them. `B7` cost
a session to learn that a service whose docstring says "runs on a timer" is asserting that
somebody else does something, and nothing checks the claim. `sweep_once()` calls
`check_timeouts()`, and its test moves nothing but the clock.

**Silence here is not the endpointer's silence.** The endpointer closes a *phrase* after
100 ms (`D9`); this is the caller having stopped talking altogether, which is six seconds
and belongs to `hold.py`. Conflating the two would end the recording at the first breath.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from readycall.clock import Clock
from readycall.config import Settings
from readycall.domain.enums import SpeakerRole
from readycall.domain.models import TranscriptTurn
from readycall.logging import get_logger
from readycall.media.audio import AudioFormat
from readycall.media.gateway import MediaGateway
from readycall.ports.stt import ReplayableSttEngine, SttEngine, SttHint
from readycall.ports.vad import VoiceActivityDetector
from readycall.services.intake.service import IntakeService
from readycall.services.transcription.stream import TranscriptionStream

log = get_logger(__name__)


@dataclass
class _Live:
    stream: TranscriptionStream
    #: `monotonic_ms` of the last turn, and of the moment recording opened. Monotonic
    #: rather than wall-clock for the same reason the hold uses it (`B8`): a clock
    #: correction mid-call must not make a recording look six seconds old.
    opened_ms: float
    last_turn_ms: float
    turns: int = 0
    speaker_role: SpeakerRole = SpeakerRole.CUSTOMER
    fired: set[str] = field(default_factory=set)


class TranscriptionService:
    def __init__(
        self,
        *,
        intake: IntakeService,
        vad_factory: object,
        stt: SttEngine,
        clock: Clock,
        settings: Settings,
        gateway: MediaGateway | None = None,
        hint: SttHint | None = None,
    ) -> None:
        self._intake = intake
        # A factory rather than one instance: detectors are STATEFUL across frames, so two
        # concurrent calls sharing one would interleave their hidden states and each would
        # be endpointed against the other's audio.
        self._vad_factory = vad_factory
        self._stt = stt
        self._clock = clock
        self._settings = settings
        self.gateway = gateway or MediaGateway()
        self._hint = hint
        self._live: dict[str, _Live] = {}

    async def open(
        self,
        call_session_id: str,
        *,
        fmt: AudioFormat | None = None,
        speaker_role: SpeakerRole = SpeakerRole.CUSTOMER,
    ) -> None:
        """The caller pressed 1 and a recording is now running."""
        if call_session_id in self._live:
            return
        # A scripted engine carries a cursor through its lines, so a new recording has to
        # start at the top of the script (`D107`). Real engines are stateless per utterance
        # and do not implement this — which is why it is a capability check rather than a
        # method on the port. Found on a running server: the FIRST demo call transcribed
        # and every one after it showed an empty panel, because one engine instance per
        # process is correct for a model and wrong for a script.
        if isinstance(self._stt, ReplayableSttEngine):
            self._stt.reset()
        self.gateway.open_leg(call_session_id, speaker_role=speaker_role, fmt=fmt)

        async def sink(turn: TranscriptTurn) -> None:
            live = self._live.get(call_session_id)
            if live is not None:
                live.last_turn_ms = self._clock.monotonic_ms()
                live.turns += 1
            await self._intake.on_turn(call_session_id, turn)

        vad: VoiceActivityDetector = self._vad_factory()  # type: ignore[operator]
        stream = TranscriptionStream(
            call_session_id=call_session_id,
            vad=vad,
            stt=self._stt,
            clock=self._clock,
            on_turn=sink,
            speaker_role=speaker_role,
            hint=self._hint,
        )
        await stream.start()
        self.gateway.subscribe(call_session_id, stream.feed, speaker_role=speaker_role)
        now = self._clock.monotonic_ms()
        self._live[call_session_id] = _Live(
            stream=stream, opened_ms=now, last_turn_ms=now, speaker_role=speaker_role
        )
        log.info(
            "transcription opened",
            call_session_id=call_session_id,
            engine=self._stt.info.name,
            vad=vad.info.name,
        )

    async def push(self, call_session_id: str, payload: bytes) -> None:
        """One packet off the wire. P5's telephony adapter is what will call this."""
        live = self._live.get(call_session_id)
        if live is None:
            return
        await self.gateway.push(call_session_id, payload, speaker_role=live.speaker_role)

    async def close(self, call_session_id: str) -> None:
        """Flush and stop. Safe twice — the accept and the hang-up race (`D21`)."""
        live = self._live.pop(call_session_id, None)
        if live is None:
            return
        await live.stream.finish()
        await self.gateway.close_leg(call_session_id, speaker_role=live.speaker_role)
        log.info("transcription closed", call_session_id=call_session_id, turns=live.turns)

    async def check_timeouts(self) -> list[str]:
        """Silence and max duration, driven by the sweep because nothing else can (`B7`).

        Returns the calls it acted on. The intake owns what *happens* — one re-prompt and
        then a close, per `hold.py` — so this only reports that the time has passed.
        """
        acted: list[str] = []
        now = self._clock.monotonic_ms()
        for call_session_id, live in list(self._live.items()):
            if now - live.opened_ms >= self._settings.intake_max_duration_s * 1000.0:
                await self._intake.on_max_duration(call_session_id)
                await self.close(call_session_id)
                acted.append(call_session_id)
                continue
            quiet_ms = now - live.last_turn_ms
            if quiet_ms >= self._settings.intake_silence_timeout_s * 1000.0:
                # `hold.py` counts the silences and decides whether this is a re-prompt or
                # the end; firing again every tick would burn its allowance in one second.
                marker = f"silence:{int(live.last_turn_ms)}"
                if marker in live.fired:
                    continue
                live.fired.add(marker)
                report = await self._intake.on_silence(call_session_id)
                acted.append(call_session_id)
                if report is not None and not report.recording:
                    await self.close(call_session_id)
        return acted

    def live_call_ids(self) -> tuple[str, ...]:
        return tuple(self._live)


__all__ = ["TranscriptionService"]
