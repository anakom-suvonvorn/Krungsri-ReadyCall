"""Creating the `CallIntent` that a tap on *Contact* produces.

This is the service the HTTP route delegates to, so the route stays a validate-and-call
shell. It owns three things worth stating out loud:

* **The token is a bearer credential.** It is generated here, returned to the caller
  exactly once, and only its SHA-256 hash is ever stored. Anyone who can read our
  database still cannot place a call as that customer.
* **The customer id is a parameter, not a field the caller sent.** The route obtains it
  from the session and passes it in (`D4`). This function has no way to read a request.
* **Prefetch is not its job.** Creating the intent publishes `IntentCreated` and returns.
  Context assembly happens on the event, off the request path (`D6`), so the app gets its
  dial target immediately rather than waiting on six reads to the bank core.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from readycall import ids
from readycall.clock import Clock
from readycall.domain import events as ev
from readycall.domain.models import CallIntent
from readycall.logging import get_logger
from readycall.ports.event_bus import EventBus
from readycall.services.identity.resolver import hash_token
from readycall.services.identity.store import CallIntentStore

log = get_logger(__name__)


class IntentService:
    def __init__(
        self,
        *,
        store: CallIntentStore,
        bus: EventBus,
        clock: Clock,
        ttl_s: float = 900.0,
        dial_target: str = "+6621234000",
    ) -> None:
        self._store = store
        self._bus = bus
        self._clock = clock
        self._ttl_s = ttl_s
        self._dial_target = dial_target

    @property
    def dial_target(self) -> str:
        return self._dial_target

    async def create(
        self,
        *,
        customer_id: str,
        product_code: str | None = None,
        plan_id: str | None = None,
        entry_screen: str | None = None,
        app_intent: str | None = None,
        app_context: dict[str, Any] | None = None,
    ) -> tuple[CallIntent, str]:
        """Returns the stored intent and the plaintext token, which is never persisted."""
        now = self._clock.now()
        token = ids.correlation_token()

        context: dict[str, Any] = dict(app_context or {})
        if app_intent:
            # `D41`: the screen they tapped from answers the second menu question too.
            context["app_intent"] = app_intent

        intent = CallIntent(
            intent_id=ids.intent_id(),
            customer_id=customer_id,
            product_code=product_code,
            plan_id=plan_id,
            entry_screen=entry_screen,
            app_context=context,
            correlation_token_hash=hash_token(token),
            created_at=now,
            expires_at=now + timedelta(seconds=self._ttl_s),
        )
        await self._store.save(intent)

        # Fire-and-return. The assembler runs on this event, not in the request (`D6`).
        await self._bus.publish(
            ev.IntentCreated(
                call_session_id=intent.intent_id,
                occurred_at=now,
                intent_id=intent.intent_id,
                customer_id=customer_id,
                product_code=product_code,
            )
        )
        log.info(
            "call intent created",
            intent_id=intent.intent_id,
            customer_id=customer_id,
            product_code=product_code,
            app_intent=app_intent,
            ttl_s=self._ttl_s,
        )
        return intent, token

    async def get(self, intent_id: str) -> CallIntent | None:
        return await self._store.get(intent_id)


__all__ = ["IntentService"]
