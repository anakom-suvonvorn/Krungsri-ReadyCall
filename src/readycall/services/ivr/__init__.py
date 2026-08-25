"""The keypad menu — the layer that actually routes the call (`D37`).

Split three ways on purpose:

* `machine.py`  — the walk, with no I/O. Every rule lives here and every rule is a
  plain method call, so a timeout is testable without a fake phone line (`B7`).
* `presentation.py` — turning a menu into the words *this* caller hears, in their
  order, plus the mapping back from what they pressed (`D80`, `D81`).
* `personalise.py`  — which options come first, and the evidence for each (`D37`).

`service.py` is the thin async driver that plays the lines and moves the call.
"""

from readycall.services.ivr.machine import (
    IvrMachine,
    IvrOutcome,
    IvrOutcomeKind,
    IvrRun,
    IvrStep,
)
from readycall.services.ivr.presentation import MenuPresentation, present

__all__ = [
    "IvrMachine",
    "IvrOutcome",
    "IvrOutcomeKind",
    "IvrRun",
    "IvrStep",
    "MenuPresentation",
    "present",
]
