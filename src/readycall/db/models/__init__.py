"""Every table we write. Importing this package is what registers them on `Base.metadata`,
which is what Alembic autogenerate walks — a model in a file nobody imports is a migration
that never gets written.

**What is deliberately absent is as much of the design as what is here** (`D78`):

* there is no `agent_presence` table — current presence is the newest `agent_state_log` row
  per agent (`D76`);
* there is no `waiting_pool` table — the pool is `call_sessions` in `queued`/`matched`;
* there is no `identity_for_call` table — the live resolution is a column on the session.

Each of those is *derived* from something already stored. A second place recording the same
fact is a second place that can disagree, and the derived one is always the copy that ends
up wrong.
"""

from readycall.db.models.agents import AgentStateLogRow, AssignmentRow
from readycall.db.models.calls import (
    CallSessionRow,
    CallStateTransitionRow,
    CallWrapupRow,
    ContextSnapshotRow,
)
from readycall.db.models.identity import AttestationRow, KeypadCaptureRow
from readycall.db.models.matching import MatchingDecisionRow
from readycall.db.models.media import AudioRecordingRow, TranscriptTurnRow

__all__ = [
    "AgentStateLogRow",
    "AssignmentRow",
    "AttestationRow",
    "AudioRecordingRow",
    "CallSessionRow",
    "CallStateTransitionRow",
    "CallWrapupRow",
    "ContextSnapshotRow",
    "KeypadCaptureRow",
    "MatchingDecisionRow",
    "TranscriptTurnRow",
]
