"""Prefixed, sortable, human-readable identifiers.

`call_01JQK7M2R4X8ZB3N` in a log line tells you what it is at a glance; a bare UUID
does not. Everything about one call is keyed by `call_session_id`, so these ids show
up in every log line, span, event and table — legibility is worth the small effort.

Time-ordered (ULID-ish: 50-bit ms timestamp + randomness, Crockford base32) so ids
sort chronologically, which helps both index locality and eyeballing a log.

The generator is swappable so scenario replays and tests can produce identical ids
(`install(DeterministicIds())`), which is what makes golden-output comparison possible.
"""

from __future__ import annotations

import secrets
import time
from typing import Final, Protocol

_ALPHABET: Final = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"  # Crockford base32 (no I, L, O, U)


def _b32(value: int, length: int) -> str:
    out: list[str] = []
    for _ in range(length):
        out.append(_ALPHABET[value & 0x1F])
        value >>= 5
    return "".join(reversed(out))


class IdGenerator(Protocol):
    def __call__(self, prefix: str, *, now_ms: int | None = None) -> str: ...


def random_ids(prefix: str, *, now_ms: int | None = None) -> str:
    """The production generator: time-sortable and unguessable enough for an id."""
    ms = int(time.time() * 1000) if now_ms is None else now_ms
    return f"{prefix}_{_b32(ms, 10)}{_b32(secrets.randbits(30), 6)}"


class DeterministicIds:
    """Sequential ids (`call_000001`) for reproducible runs. Tests/scenarios only."""

    def __init__(self) -> None:
        self._counters: dict[str, int] = {}

    def __call__(self, prefix: str, *, now_ms: int | None = None) -> str:
        n = self._counters.get(prefix, 0) + 1
        self._counters[prefix] = n
        return f"{prefix}_{n:06d}"


_generator: IdGenerator = random_ids


def install(generator: IdGenerator) -> None:
    """Swap the id generator process-wide. Tests and the scenario runner only."""
    global _generator
    _generator = generator


def reset() -> None:
    install(random_ids)


def generate(prefix: str, *, now_ms: int | None = None) -> str:
    return _generator(prefix, now_ms=now_ms)


# --- the prefixes in use, in one place, so nobody invents `sess_` next to `call_` ---


def call_session_id() -> str:
    return generate("call")


def intent_id() -> str:
    return generate("intent")


def snapshot_id() -> str:
    return generate("snap")


def intake_id() -> str:
    return generate("intake")


def turn_id() -> str:
    return generate("turn")


def brief_id() -> str:
    return generate("brief")


def decision_id() -> str:
    return generate("match")


def assignment_id() -> str:
    return generate("asgn")


def event_id() -> str:
    return generate("evt")


def trace_id() -> str:
    return generate("trace")


def correlation_token() -> str:
    """Opaque, unguessable token binding a placed call to its intent (`D4`).

    A bearer credential: never logged in full, stored only as a hash.
    """
    return secrets.token_urlsafe(32)


def capture_id() -> str:
    return generate("cap")


def attestation_id() -> str:
    return generate("attest")


def agent_session_id() -> str:
    return generate("asess")
