"""TelephonyProvider port.

The hardest dependency and the one most likely to change, so it gets the strictest
seam (`INTEGRATIONS.md` §1). Asterisk/ARI in production; `simulated` for every test
and dev run, so no other phase is ever blocked on SIP, NAT or certificates.

Two things worth noticing in the shape of this interface:

* `bridge()` exists but `dial_agent()` does not. The customer's channel is already
  connected and parked in a holding bridge; accepting an offer *bridges* two live
  channels (`D33`). There is no dial-out, hence no dial-out delay.
* `start_media_fork()` takes a leg, because we fork each side separately and get exact
  speaker labels for free instead of running diarisation (`D26`).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable


class CallLeg(StrEnum):
    CUSTOMER = "customer"
    AGENT = "agent"


class TelephonyEventKind(StrEnum):
    INCOMING = "incoming"
    ANSWERED = "answered"
    DTMF = "dtmf"
    HANGUP = "hangup"
    BRIDGED = "bridged"
    MEDIA_STARTED = "media_started"
    MEDIA_FAILED = "media_failed"
    PLAYBACK_FINISHED = "playback_finished"


@dataclass(frozen=True, slots=True)
class DialTarget:
    """Where to place or expect a call."""

    number: str | None = None
    sip_uri: str | None = None
    correlation_token: str | None = None


@dataclass(frozen=True, slots=True)
class TelephonyEvent:
    kind: TelephonyEventKind
    telephony_call_id: str
    at_ms: float
    leg: CallLeg = CallLeg.CUSTOMER
    digit: str | None = None
    caller_number: str | None = None
    dialled_number: str | None = None
    correlation_token: str | None = None
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MediaFork:
    stream_id: str
    telephony_call_id: str
    leg: CallLeg
    sample_rate: int = 16000


@runtime_checkable
class TelephonyProvider(Protocol):
    @property
    def name(self) -> str: ...

    async def answer(self, telephony_call_id: str) -> None: ...

    async def play(self, telephony_call_id: str, audio_ref: str) -> None:
        """Play a **pre-rendered** prompt clip. Never synthesises at call time (`D24`)."""
        ...

    async def stop_playback(self, telephony_call_id: str) -> None:
        """Barge-in: a DTMF press interrupts the prompt, or menus feel glacial."""
        ...

    async def hold(self, telephony_call_id: str, *, music: bool = True) -> None:
        """Park the channel in a holding bridge. It stays connected while queued."""
        ...

    async def start_media_fork(
        self, telephony_call_id: str, *, leg: CallLeg = CallLeg.CUSTOMER
    ) -> MediaFork: ...

    async def stop_media_fork(self, stream_id: str) -> None: ...

    async def bridge(self, telephony_call_id: str, agent_endpoint: str) -> None:
        """Connect the parked customer channel to the agent's browser SIP endpoint."""
        ...

    async def hangup(self, telephony_call_id: str, reason: str) -> None: ...

    async def originate(self, target: DialTarget) -> str:
        """Outbound (callbacks). Returns the telephony call id."""
        ...

    def events(self) -> AsyncIterator[TelephonyEvent]: ...

    async def close(self) -> None: ...
