"""A caching, circuit-breaking wrapper around any `CoreDataProvider`.

A decorator, not a provider of its own: it wraps whatever the env var selected, so the
same behaviour applies to fixtures, Postgres, or a hackathon-day HTTP endpoint that turns
out to be slow and flaky.

Three behaviours, each earning its place:

* **TTL cache** — the same call reads the same customer several times (identity, context,
  brief). Doing it once keeps the tap-to-snapshot budget realistic.
* **Stale-while-error** — if the upstream fails but we hold an expired entry, serve the
  stale value and *mark it stale*. A slightly old policy list with a visible staleness
  badge beats an empty screen (`ARCHITECTURE.md` §16).
* **Circuit breaker** — after repeated failures, stop hammering a dying service for a
  cooldown. This is what keeps the *call* fast when the bank's data is down: we fail in
  microseconds and degrade, rather than waiting out a timeout on every lookup.

The provider is read-only, so caching is safe by construction: there is no write path that
could invalidate an entry behind our back (`D5`).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypeVar

from readycall.clock import Clock, SystemClock
from readycall.domain.models import (
    Claim,
    Customer,
    Holding,
    Interaction,
    LifeEvent,
    Policy,
    Product,
)
from readycall.logging import get_logger
from readycall.ports.core_data import CoreDataProvider

log = get_logger(__name__)

T = TypeVar("T")


@dataclass(slots=True)
class _Entry:
    value: Any
    stored_at: float


class CircuitOpen(Exception):
    """The breaker is open; do not attempt the call."""


class CachingCoreDataProvider:
    """Wraps a `CoreDataProvider` with a TTL cache and a circuit breaker."""

    def __init__(
        self,
        inner: CoreDataProvider,
        *,
        clock: Clock | None = None,
        ttl_s: float = 60.0,
        failure_threshold: int = 3,
        cooldown_s: float = 30.0,
    ) -> None:
        self._inner = inner
        self._clock = clock or SystemClock()
        self._ttl_s = ttl_s
        self._failure_threshold = failure_threshold
        self._cooldown_s = cooldown_s

        self._cache: dict[tuple[str, tuple[Any, ...]], _Entry] = {}
        self._failures = 0
        self._opened_at: float | None = None
        #: True when the most recent read came from an expired entry after an upstream
        #: failure. The context assembler turns this into a staleness badge.
        self.served_stale = False

    @property
    def name(self) -> str:
        return f"caching({self._inner.name})"

    @property
    def circuit_is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if self._clock.monotonic_ms() - self._opened_at >= self._cooldown_s * 1000:
            # Cooldown elapsed: let one call through and see what happens.
            self._opened_at = None
            self._failures = 0
            return False
        return True

    # --- the machinery ----------------------------------------------------------------

    async def _call(
        self,
        method: str,
        args: tuple[Any, ...],
        default: T,
        fn: Callable[[], Awaitable[T]],
    ) -> T:
        """Cache-or-fetch. `method` + `args` form the cache key; `fn` does the real call."""
        key = (method, args)
        now = self._clock.monotonic_ms()
        entry = self._cache.get(key)

        if entry is not None and now - entry.stored_at < self._ttl_s * 1000:
            self.served_stale = False
            return entry.value  # type: ignore[no-any-return]

        if self.circuit_is_open:
            return self._fall_back(key, entry, default, reason="circuit_open")

        try:
            value = await fn()
        except Exception as exc:
            self._failures += 1
            if self._failures >= self._failure_threshold and self._opened_at is None:
                self._opened_at = now
                log.warning(
                    "core data circuit opened",
                    provider=self._inner.name,
                    failures=self._failures,
                    cooldown_s=self._cooldown_s,
                )
            log.warning("core data call failed", method=method, error=repr(exc))
            return self._fall_back(key, entry, default, reason="upstream_error")

        self._failures = 0
        self._cache[key] = _Entry(value=value, stored_at=now)
        self.served_stale = False
        return value

    def _fall_back(
        self, key: tuple[str, tuple[Any, ...]], entry: _Entry | None, default: T, *, reason: str
    ) -> T:
        if entry is not None:
            # Stale beats empty — but the caller must be able to say so on screen.
            self.served_stale = True
            log.info("serving stale core data", method=key[0], reason=reason)
            return entry.value  # type: ignore[no-any-return]
        self.served_stale = False
        return default

    # --- the port ---------------------------------------------------------------------

    async def get_customer(self, customer_id: str) -> Customer | None:
        return await self._call(
            "get_customer", (customer_id,), None, lambda: self._inner.get_customer(customer_id)
        )

    async def find_customer_by_phone(self, phone_e164: str) -> Customer | None:
        return await self._call(
            "find_customer_by_phone",
            (phone_e164,),
            None,
            lambda: self._inner.find_customer_by_phone(phone_e164),
        )

    async def list_policies(self, customer_id: str, *, active_only: bool = True) -> list[Policy]:
        # `active_only` is part of the key: the two calls return different lists.
        return await self._call(
            "list_policies",
            (customer_id, active_only),
            [],
            lambda: self._inner.list_policies(customer_id, active_only=active_only),
        )

    async def get_policy(self, policy_no: str) -> Policy | None:
        return await self._call(
            "get_policy", (policy_no,), None, lambda: self._inner.get_policy(policy_no)
        )

    async def list_claims(self, policy_no: str) -> list[Claim]:
        return await self._call(
            "list_claims", (policy_no,), [], lambda: self._inner.list_claims(policy_no)
        )

    async def list_interactions(self, customer_id: str, *, limit: int = 20) -> list[Interaction]:
        return await self._call(
            "list_interactions",
            (customer_id, limit),
            [],
            lambda: self._inner.list_interactions(customer_id, limit=limit),
        )

    async def list_holdings(self, customer_id: str) -> list[Holding]:
        return await self._call(
            "list_holdings", (customer_id,), [], lambda: self._inner.list_holdings(customer_id)
        )

    async def list_life_events(self, customer_id: str) -> list[LifeEvent]:
        return await self._call(
            "list_life_events",
            (customer_id,),
            [],
            lambda: self._inner.list_life_events(customer_id),
        )

    async def get_product(self, product_code: str) -> Product | None:
        return await self._call(
            "get_product", (product_code,), None, lambda: self._inner.get_product(product_code)
        )

    async def health_check(self) -> bool:
        if self.circuit_is_open:
            return False
        try:
            return await self._inner.health_check()
        except Exception:
            return False

    def cache_size(self) -> int:
        return len(self._cache)

    def invalidate(self) -> None:
        self._cache.clear()
