"""SimulatedTelephonyProvider — a whole phone network in a dict.

This is the adapter that removes the biggest schedule risk in the project. Telephony
is the hardest dependency to stand up (SIP, NAT, certificates, codecs), so every other
phase is built against this instead and nothing waits for Asterisk (`PLAN.md` §0).

It models the parts the rest of the system actually depends on:

* a channel that **stays connected while queued**, parked in a holding bridge — which
  is why accepting an offer is a bridge rather than a dial-out (`D33`);
* **per-leg media forks**, so speaker labels come from the topology rather than a
  diarisation model (`D26`);
* DTMF, playback and barge-in, so the IVR can be exercised for real (`D24`).

Everything it does is recorded, so a test can assert *which* prompt was played and
*when* the fork started.
"""

from __future__ import annotations

from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum

from readycall import ids
from readycall.clock import Clock, SystemClock
from readycall.errors import PermanentError
from readycall.logging import get_logger
from readycall.ports.telephony import (
    CallLeg,
    DialTarget,
    MediaFork,
    TelephonyEvent,
    TelephonyEventKind,
)

log = get_logger(__name__)


class ChannelState(StrEnum):
    RINGING = "ringing"
    ANSWERED = "answered"
    HELD = "held"
    BRIDGED = "bridged"
    HUNGUP = "hungup"


@dataclass
class SimulatedChannel:
    telephony_call_id: str
    caller_number: str | None
    dialled_number: str | None
    correlation_token: str | None
    state: ChannelState = ChannelState.RINGING
    played: list[str] = field(default_factory=list)
    playing: str | None = None
    digits: list[str] = field(default_factory=list)
    forks: dict[str, MediaFork] = field(default_factory=dict)
    bridged_to: str | None = None
    hangup_reason: str | None = None


class SimulatedTelephonyProvider:
    """In-process telephony. No sockets, no audio hardware, fully deterministic."""

    def __init__(self, *, clock: Clock | None = None, fail_media_fork: bool = False) -> None:
        self._clock = clock or SystemClock()
        self._channels: dict[str, SimulatedChannel] = {}
        self._events: deque[TelephonyEvent] = deque()
        # Lets a test exercise the "media fork fails, call proceeds anyway" rung.
        self._fail_media_fork = fail_media_fork

    @property
    def name(self) -> str:
        return "simulated"

    def channel(self, telephony_call_id: str) -> SimulatedChannel:
        try:
            return self._channels[telephony_call_id]
        except KeyError as exc:
            raise PermanentError(f"no such channel: {telephony_call_id}") from exc

    # --- test-side injection ---------------------------------------------------------

    def inject_incoming(
        self,
        *,
        caller_number: str | None = None,
        dialled_number: str | None = None,
        correlation_token: str | None = None,
    ) -> str:
        """Simulate a call arriving. Returns the telephony call id."""
        telephony_call_id = ids.generate("tel")
        self._channels[telephony_call_id] = SimulatedChannel(
            telephony_call_id=telephony_call_id,
            caller_number=caller_number,
            dialled_number=dialled_number,
            correlation_token=correlation_token,
        )
        self._emit(
            TelephonyEventKind.INCOMING,
            telephony_call_id,
            caller_number=caller_number,
            dialled_number=dialled_number,
            correlation_token=correlation_token,
        )
        return telephony_call_id

    def inject_dtmf(self, telephony_call_id: str, digit: str) -> None:
        """A keypress. Interrupts any playback in progress (barge-in)."""
        channel = self.channel(telephony_call_id)
        channel.digits.append(digit)
        if channel.playing is not None:
            channel.playing = None
        self._emit(TelephonyEventKind.DTMF, telephony_call_id, digit=digit)

    def inject_hangup(self, telephony_call_id: str, reason: str = "caller_hung_up") -> None:
        channel = self.channel(telephony_call_id)
        channel.state = ChannelState.HUNGUP
        channel.hangup_reason = reason
        self._emit(TelephonyEventKind.HANGUP, telephony_call_id, detail={"reason": reason})

    def _emit(
        self,
        kind: TelephonyEventKind,
        telephony_call_id: str,
        *,
        leg: CallLeg = CallLeg.CUSTOMER,
        digit: str | None = None,
        caller_number: str | None = None,
        dialled_number: str | None = None,
        correlation_token: str | None = None,
        detail: dict[str, str] | None = None,
    ) -> None:
        self._events.append(
            TelephonyEvent(
                kind=kind,
                telephony_call_id=telephony_call_id,
                at_ms=self._clock.monotonic_ms(),
                leg=leg,
                digit=digit,
                caller_number=caller_number,
                dialled_number=dialled_number,
                correlation_token=correlation_token,
                detail=dict(detail or {}),
            )
        )

    # --- the port --------------------------------------------------------------------

    async def answer(self, telephony_call_id: str) -> None:
        channel = self.channel(telephony_call_id)
        channel.state = ChannelState.ANSWERED
        self._emit(TelephonyEventKind.ANSWERED, telephony_call_id)

    async def play(self, telephony_call_id: str, audio_ref: str) -> None:
        channel = self.channel(telephony_call_id)
        channel.played.append(audio_ref)
        channel.playing = audio_ref
        self._emit(
            TelephonyEventKind.PLAYBACK_FINISHED, telephony_call_id, detail={"ref": audio_ref}
        )

    async def stop_playback(self, telephony_call_id: str) -> None:
        self.channel(telephony_call_id).playing = None

    async def hold(self, telephony_call_id: str, *, music: bool = True) -> None:
        """Park in a holding bridge. Still connected — that is the whole point (`D33`)."""
        channel = self.channel(telephony_call_id)
        channel.state = ChannelState.HELD

    async def start_media_fork(
        self, telephony_call_id: str, *, leg: CallLeg = CallLeg.CUSTOMER
    ) -> MediaFork:
        channel = self.channel(telephony_call_id)
        if self._fail_media_fork:
            self._emit(TelephonyEventKind.MEDIA_FAILED, telephony_call_id, leg=leg)
            raise PermanentError("simulated media fork failure")
        fork = MediaFork(
            stream_id=ids.generate("media"),
            telephony_call_id=telephony_call_id,
            leg=leg,
        )
        channel.forks[fork.stream_id] = fork
        self._emit(TelephonyEventKind.MEDIA_STARTED, telephony_call_id, leg=leg)
        return fork

    async def stop_media_fork(self, stream_id: str) -> None:
        for channel in self._channels.values():
            if stream_id in channel.forks:
                del channel.forks[stream_id]
                return

    async def bridge(self, telephony_call_id: str, agent_endpoint: str) -> None:
        channel = self.channel(telephony_call_id)
        if channel.state is ChannelState.HUNGUP:
            raise PermanentError("cannot bridge a hung-up channel")
        channel.state = ChannelState.BRIDGED
        channel.bridged_to = agent_endpoint
        self._emit(
            TelephonyEventKind.BRIDGED, telephony_call_id, detail={"endpoint": agent_endpoint}
        )

    async def hangup(self, telephony_call_id: str, reason: str) -> None:
        channel = self.channel(telephony_call_id)
        channel.state = ChannelState.HUNGUP
        channel.hangup_reason = reason
        self._emit(TelephonyEventKind.HANGUP, telephony_call_id, detail={"reason": reason})

    async def originate(self, target: DialTarget) -> str:
        """Outbound, for callbacks (`D25`)."""
        return self.inject_incoming(
            caller_number=None,
            dialled_number=target.number,
            correlation_token=target.correlation_token,
        )

    async def events(self) -> AsyncIterator[TelephonyEvent]:
        while self._events:
            yield self._events.popleft()

    def pending_events(self) -> list[TelephonyEvent]:
        """Synchronous drain, for tests."""
        out = list(self._events)
        self._events.clear()
        return out

    async def close(self) -> None:
        self._channels.clear()
        self._events.clear()
