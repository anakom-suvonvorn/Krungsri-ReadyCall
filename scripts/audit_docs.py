"""Check every number the docs claim against what the code actually is.

    uv run python scripts/audit_docs.py

Documentation rots in a specific, boring way: a count that was true when it was written and
is quietly wrong six commits later. `tests/unit/test_diagrams.py` already stops the
*generated* diagrams drifting; this does the same job for prose, which cannot be generated.

It reports rather than fails, because not every mismatch is a bug:

* `docs/explanations/*.md` are **dated snapshots** the user asked to keep verbatim. A P0
  walkthrough saying "84 tests pass" is correct *as of when it was written*; the fix for
  those is an entry in their "changes since" section, never an edit to the body.
* `BUG_HISTORY.md` verification notes are the same — they record what was true at the fix.
* Anything describing *current* state (`NEXT_SESSION`, `PROJECT_STATE`, the diagram pages)
  should match, and a mismatch there is real.

Run it after any phase lands, and before a `/compact` — a stale number is worse than a
missing one, because the next session will believe it.
"""

from __future__ import annotations

import pathlib
import re
import subprocess
import sys

sys.path.insert(0, "src")
from readycall.console import enable_utf8

enable_utf8()

from readycall.domain import events as ev  # noqa: E402
from readycall.domain.enums import CallState  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402

DOCS = pathlib.Path("docs")


def _count(pattern: str, path: pathlib.Path) -> int:
    return len(re.findall(pattern, path.read_text(encoding="utf-8"), re.M))


ROOT = pathlib.Path(".")

# --- ground truth ----------------------------------------------------------------------
pack = DomainPack.load("config")
truth = {
    "call_states": len(list(CallState)),
    "event_types": len(ev.EVENT_TYPES),
    "intents": len(pack.intents),
    "skills": len(pack.skills),
    "queues": len(pack.queues),
    "dids": len(pack.dids),
    "menus": len(pack.menus),
    "decisions": _count(r"^## D\d+", DOCS / "DECISIONS.md"),
    "bugs": _count(r"^## B\d+", DOCS / "BUG_HISTORY.md"),
    "svgs": len(list((DOCS / "diagrams").glob("*.svg"))),
    "mmds": len(list((DOCS / "diagrams" / "src").glob("*.mmd"))),
    "pages": len(list((DOCS / "diagrams").glob("*.md"))),
    "ports": len([p for p in (ROOT / "src/readycall/ports").glob("*.py") if p.stem != "__init__"]),
}
out = subprocess.run(
    ["uv", "run", "pytest", "-q", "--collect-only"], capture_output=True, text=True, shell=True
)
m = re.search(r"(\d+) tests? collected", out.stdout)
truth["tests"] = int(m.group(1)) if m else -1

print("GROUND TRUTH")
for k, v in truth.items():
    print(f"  {k:16} {v}")

# --- scan docs for numeric claims -------------------------------------------------------
patterns = [
    (r"(\d+)\s+call states", "call_states"),
    (r"(\d+)\s+event types", "event_types"),
    (r"(\d+)\s+intents", "intents"),
    (r"(\d+)\s+tests?\b", "tests"),
    (r"\*\*(\d+)\s+tests?\*\*", "tests"),
    (r"(\d+)\s+diagrams", "svgs"),
    (r"(\d+)\s+rendered", "svgs"),
    (r"(\d+)/(\d+)\s+up to date", None),
]

print("\nNUMERIC CLAIMS THAT DISAGREE WITH THE CODE")
problems = []
targets = [*DOCS.rglob("*.md"), pathlib.Path("../CLAUDE.md")]
for path in targets:
    if not path.exists():
        continue
    text = path.read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), 1):
        for pat, key in patterns:
            if key is None:
                continue
            for match in re.finditer(pat, line):
                claimed = int(match.group(1))
                if claimed != truth[key]:
                    problems.append(f"{path}:{lineno}  claims {claimed} {key}, actual {truth[key]}")
for p in sorted(set(problems)):
    print("  " + p)
if not problems:
    print("  none")

# --- references to things that no longer exist -------------------------------------------
print("\nREFERENCES TO REMOVED THINGS")
gone = {
    r"CallState\.RATING": "the RATING state was deleted (D46)",
    # Lowercase too: a stale *pasted transcript* in PROJECT_STATE survived the D46 audit
    # because it said `wrap_up -> rating`, not `CallState.RATING`. Copied-in tool output
    # is exactly the kind of doc that rots invisibly.
    r"-> rating\b|rating -> ": "no call transitions through rating any more (D46)",
    r"DemoProject/": "DemoProject was abandoned (D34)",
    r"time\.monotonic\(\)": "replaced by perf_counter (B3) - ok inside B3/clock docstrings",
    r"\bno_candidates\b": "split into no_qualified_agent / all_qualified_busy (D50, B4)",
}
found = []
for path in targets:
    if not path.exists():
        continue
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        for pat, why in gone.items():
            if re.search(pat, line):
                found.append(f"{path}:{lineno}  {why}\n      {line.strip()[:100]}")
print("\n".join("  " + f for f in found) if found else "  none")

# --- last-updated dates -------------------------------------------------------------------
print("\nLAST UPDATED DATES")
for path in sorted(DOCS.glob("*.md")):
    # Not anchored to a leading `_`: DATA_MODEL and INTEGRATIONS carry a status first
    # ("_Status: **design only**. Last updated: ..._"), and matching only the bare form
    # reported them as MISSING when the date was right there. An audit that cries wolf
    # gets ignored, which is worse than not having one.
    m = re.search(r"Last updated: ([\d-]+)\.", path.read_text(encoding="utf-8"))
    print(f"  {path.name:24} {m.group(1) if m else 'MISSING'}")

# --- open questions still marked open -----------------------------------------------------
print("\nOPEN QUESTIONS IN NEXT_SESSION")
ns = (DOCS / "NEXT_SESSION.md").read_text(encoding="utf-8")
for m in re.finditer(r"^\| (Q\d+) \| (.{0,70})", ns, re.M):
    print(f"  {m.group(1)}: {m.group(2)}")
