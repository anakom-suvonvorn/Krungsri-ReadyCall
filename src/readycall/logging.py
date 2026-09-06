"""Structured logging, with `call_session_id` bound to everything.

The single most useful debugging property this system can have is that every log
line emitted while handling a call carries that call's id (`CLAUDE.md`). So the id
lives in a contextvar bound once by the orchestrator, not passed by hand into every
log call and inevitably forgotten in the one place that matters.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import structlog

_SENSITIVE_KEYS = frozenset(
    {
        "correlation_token",
        "api_key",
        "anthropic_api_key",
        "llm_api_key",
        # Declared in `Settings` whether or not an adapter reads them yet: the point of
        # naming a secret is that it is redacted the day somebody sets it, not the day
        # something starts using it.
        "gemini_api_key",
        "huggingface_token",
        "hf_token",
        "asterisk_ari_password",
        "twilio_auth_token",
        "vault_token",
        "recording_master_key",
        "blob_secret_key",
        "secret",
        "token",
        "password",
        "authorization",
        "citizen_id",
        "national_id",
        "card_number",
    }
)


def _redact(_logger: Any, _name: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """Never log a bearer token or a national id, even by accident (`D14`).

    Cheap insurance: one processor beats trusting every future call site.
    """
    for key in list(event_dict):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = "<redacted>"
    return event_dict


def configure(*, log_format: str = "console", level: str = "INFO") -> None:
    """Configure structlog once, at process start."""
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _redact,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=False)
    )

    structlog.configure(
        processors=[*shared, renderer],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]


@contextmanager
def call_context(
    call_session_id: str,
    *,
    trace_id: str | None = None,
    **extra: Any,
) -> Iterator[None]:
    """Bind a call's identity to every log line emitted inside this block."""
    bindings: dict[str, Any] = {"call_session_id": call_session_id}
    if trace_id:
        bindings["trace_id"] = trace_id
    bindings.update(extra)
    tokens = structlog.contextvars.bind_contextvars(**bindings)
    try:
        yield
    finally:
        structlog.contextvars.reset_contextvars(**tokens)


def clear_context() -> None:
    structlog.contextvars.clear_contextvars()
