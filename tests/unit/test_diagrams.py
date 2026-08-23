"""The generated diagrams must match the system they claim to describe.

The whole argument for generating diagrams from source (`docs/diagrams/README.md`) is that
they cannot quietly drift the way a hand-drawn one does. That argument is only true if
something checks — otherwise the committed `.mmd` files are just as stale as any diagram,
with a `%% GENERATED` banner making a false promise.

So: regenerate into a temp directory and compare. Add a menu option, a call state, an
adapter or a domain model field without re-running the generator, and this fails.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
COMMITTED = ROOT / "docs" / "diagrams" / "src"
SVG_DIR = ROOT / "docs" / "diagrams"


def _load_generator():
    """Import `scripts/gen_diagrams.py`, which is a script rather than a package module."""
    spec = importlib.util.spec_from_file_location(
        "gen_diagrams", ROOT / "scripts" / "gen_diagrams.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_diagrams"] = module
    spec.loader.exec_module(module)
    return module


def test_generated_diagrams_are_up_to_date(tmp_path: Path) -> None:
    generator = _load_generator()
    original_out = generator.OUT
    generator.OUT = tmp_path
    try:
        generator.main()
    finally:
        generator.OUT = original_out

    regenerated = {p.name: p.read_text(encoding="utf-8") for p in tmp_path.glob("*.mmd")}
    assert regenerated, "the generator produced nothing"

    stale: list[str] = []
    for name, expected in sorted(regenerated.items()):
        committed = COMMITTED / name
        if not committed.exists():
            stale.append(f"{name}: generated but never committed")
        elif committed.read_text(encoding="utf-8") != expected:
            stale.append(f"{name}: committed copy differs from what the code produces now")

    assert not stale, (
        "generated diagrams are out of date - run `uv run python scripts/gen_diagrams.py` "
        "then `uv run python scripts/render_diagrams.py`:\n  " + "\n  ".join(stale)
    )


def test_every_source_has_a_rendered_svg() -> None:
    """A committed `.mmd` with no `.svg` beside it is invisible to anyone reading the docs."""
    missing = [
        source.stem
        for source in sorted(COMMITTED.glob("*.mmd"))
        if not (SVG_DIR / f"{source.stem}.svg").exists()
    ]
    assert not missing, "no rendered SVG for: " + ", ".join(missing)


@pytest.mark.parametrize("page", sorted((ROOT / "docs" / "diagrams").glob("*.md")))
def test_diagram_pages_only_link_images_that_exist(page: Path) -> None:
    """A broken image link silently shows nothing, which is worse than a missing page."""
    import re

    broken = [
        ref
        for ref in re.findall(r"!\[[^\]]*\]\(([^)]+)\)", page.read_text(encoding="utf-8"))
        if not (page.parent / ref).exists()
    ]
    assert not broken, f"{page.name} links missing images: {', '.join(broken)}"
