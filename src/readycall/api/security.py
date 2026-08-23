"""Working out which customer a request belongs to — **server-side, always** (`D4`).

This module exists to make one rule structurally true rather than merely intended: **the
client never asserts who it is.** A request body may say which *plan* was tapped and which
*screen* it was tapped from; it may not say `customer_id`. If it could, anyone with curl
could mint a correlation token for a stranger and walk their identity up to `L3_VERIFIED`
on the next call — the whole assurance ladder would rest on a value the attacker supplied.

So identity comes from the session, and the session is resolved through a `Protocol`:

* in production the adapter validates the bank's own session token / OIDC cookie;
* in the demo it maps a cookie to a persona the user picked (`DemoSessionStore`).

The endpoint is identical in both cases, which is the point of the seam (`D3`) — the
customer simulator exercises the *same* code path the real Krungsri app would.
"""

from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from readycall.clock import Clock
from readycall.logging import get_logger

log = get_logger(__name__)


class AuthenticationRequired(Exception):
    """No valid session. Raised by the resolver, turned into a 401 by the router."""


@dataclass(frozen=True, slots=True)
class Principal:
    """Who the caller of an API request is, as far as the *server* is concerned."""

    customer_id: str
    session_id: str
    #: How the session was established, for the audit trail. Demo sessions say so.
    method: str = "session"


@runtime_checkable
class SessionResolver(Protocol):
    """Turns an opaque session token into a customer id, or refuses."""

    @property
    def name(self) -> str: ...

    async def resolve(self, session_token: str | None) -> Principal: ...


class DemoSessionStore:
    """DEMO: a persona picker standing in for the bank's login (`D47`).

    Real authentication is out of scope for this project — we are a context layer on top
    of Krungsri's systems, not an identity provider, and the competition brief puts core
    changes out of scope. What matters is that the *shape* is right: a token is issued
    server-side, the mapping to a customer lives on the server, and the endpoint that
    consumes it cannot tell a demo session from a real one.

    Tokens are compared with `hmac.compare_digest` and looked up as opaque strings. That
    is not because a hackathon demo is under attack — it is because this class is the
    thing a real adapter gets modelled on, and a lookup that leaks timing or accepts a
    prefix is a bad model to copy.
    """

    name = "demo"

    def __init__(self, *, clock: Clock, ttl_s: float = 3600.0) -> None:
        self._clock = clock
        self._ttl_s = ttl_s
        self._sessions: dict[str, tuple[str, float]] = {}

    def issue(self, customer_id: str) -> str:
        """Start a session for a persona. Returns the token to set as a cookie."""
        token = secrets.token_urlsafe(24)
        expires_at = self._clock.now().timestamp() + self._ttl_s
        self._sessions[token] = (customer_id, expires_at)
        log.info("demo session issued", customer_id=customer_id, expires_in_s=self._ttl_s)
        return token

    def revoke(self, session_token: str | None) -> None:
        if session_token:
            self._sessions.pop(session_token, None)

    async def resolve(self, session_token: str | None) -> Principal:
        if not session_token:
            raise AuthenticationRequired("no session cookie")

        # Constant-time comparison against known tokens rather than a dict hit, so this
        # stays a faithful model of a real credential check.
        match: str | None = None
        for known in self._sessions:
            if hmac.compare_digest(known, session_token):
                match = known
                break
        if match is None:
            raise AuthenticationRequired("unknown session")

        customer_id, expires_at = self._sessions[match]
        if self._clock.now().timestamp() >= expires_at:
            del self._sessions[match]
            raise AuthenticationRequired("session expired")

        return Principal(customer_id=customer_id, session_id=match, method="demo_persona")


__all__ = [
    "AuthenticationRequired",
    "DemoSessionStore",
    "Principal",
    "SessionResolver",
]
