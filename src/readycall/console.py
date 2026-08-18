"""Make stdout/stderr safe for Thai on Windows.

The Windows console defaults to cp1252, so printing a Thai transcript raises
`UnicodeEncodeError` and takes the process with it (`B1`). This is a Thai product:
every entrypoint that can print domain text calls `enable_utf8()` first.

Not optional politeness — the scenario runner prints transcripts, and a crash there
would be the first thing anyone runs.
"""

from __future__ import annotations

import contextlib
import sys


def enable_utf8() -> None:
    """Reconfigure the standard streams to UTF-8 with replacement on failure.

    `errors="replace"` rather than `strict`: a mangled glyph in a log line is a
    cosmetic problem, a killed process mid-call is not.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            # A redirected or already-closed stream cannot be reconfigured; that is
            # not worth failing a process over.
            with contextlib.suppress(ValueError, OSError):
                reconfigure(encoding="utf-8", errors="replace")
