"""Exception hierarchy.

Split along the line that actually matters at runtime: a `TransientError` is worth
retrying, a `PermanentError` is not, and a `DegradedError` means "this stage failed
but the call must continue" (`D12`). Adapters are expected to translate vendor
exceptions into these so that services never catch a vendor type.
"""

from __future__ import annotations


class ReadyCallError(Exception):
    """Base for every error this system raises deliberately."""


class ConfigError(ReadyCallError):
    """Configuration is missing, contradictory, or refers to something unknown.

    Raised at startup, never mid-call. Startup validation failing loudly beats a
    surprise at 2am (see `validate_startup`).
    """


class TransientError(ReadyCallError):
    """A failure that may well succeed on retry (network blip, timeout, 5xx)."""


class PermanentError(ReadyCallError):
    """A failure that will not succeed on retry (bad request, unknown id, 4xx)."""


class DegradedError(ReadyCallError):
    """A stage could not produce its output, and the caller should degrade.

    This is not an emergency: every AI/enrichment stage has a documented lesser
    output (`ARCHITECTURE.md` §16). Raising this is how a stage says "use it".
    """

    def __init__(self, message: str, *, stage: str, fallback: str) -> None:
        super().__init__(message)
        self.stage = stage
        self.fallback = fallback


class IllegalTransition(ReadyCallError):
    """An attempt to move a call to a state it cannot legally reach.

    Always a bug in our own code, never caused by input, so it is loud.
    """

    def __init__(self, from_state: object, to_state: object) -> None:
        super().__init__(f"illegal call state transition: {from_state} -> {to_state}")
        self.from_state = from_state
        self.to_state = to_state
