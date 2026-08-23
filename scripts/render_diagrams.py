"""Render every `docs/diagrams/src/*.mmd` to an SVG beside it.

    uv run python scripts/render_diagrams.py            # render all
    uv run python scripts/render_diagrams.py state_machine menu_tree
    uv run python scripts/render_diagrams.py --check    # fail if any SVG is missing or stale

Needs the mermaid CLI. Either put `mmdc` on PATH::

    npm install -g @mermaid-js/mermaid-cli

or point at one you already have::

    MMDC=/path/to/mmdc uv run python scripts/render_diagrams.py

The renderer drives a headless browser through puppeteer. If it cannot find one, set
`PUPPETEER_EXECUTABLE_PATH` to an installed Chrome or Edge — that avoids downloading a
second copy of Chromium onto a laptop that already has one.

Regenerate the *derived* diagrams first with `scripts/gen_diagrams.py`; this script only
renders whatever `.mmd` files are present.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "diagrams" / "src"
OUT = ROOT / "docs" / "diagrams"

#: Records the hash of each `.mmd` at the moment its SVG was rendered.
#:
#: `--check` used to compare mtimes, which cried wolf: re-running `gen_diagrams.py` rewrites
#: every source file, so all 45 looked stale even when not one byte had changed. A check that
#: reports false alarms gets ignored, which is worse than no check. Hashes only flag diagrams
#: whose *content* has actually moved.
MANIFEST = OUT / ".render-manifest.json"


def source_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_manifest() -> dict[str, str]:
    if not MANIFEST.exists():
        return {}
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def save_manifest(entries: dict[str, str]) -> None:
    MANIFEST.write_text(json.dumps(entries, indent=2, sort_keys=True) + "\n", encoding="utf-8")


#: Shared look, so 40-odd diagrams read as one set rather than forty stylings.
MERMAID_CONFIG = {
    "theme": "base",
    "themeVariables": {
        "fontFamily": "Segoe UI, Noto Sans Thai, Tahoma, sans-serif",
        "fontSize": "15px",
        "primaryColor": "#eef4ff",
        "primaryTextColor": "#0b2545",
        "primaryBorderColor": "#5b8def",
        "lineColor": "#7a8699",
        "secondaryColor": "#fff4e6",
        "tertiaryColor": "#f7f9fc",
    },
    "flowchart": {"curve": "basis", "htmlLabels": True, "padding": 14},
    "sequence": {"useMaxWidth": True, "wrap": True, "width": 190},
    "gantt": {"useMaxWidth": True, "barHeight": 22, "fontSize": 13},
    "journey": {"useMaxWidth": True},
}

_CHROME_CANDIDATES = (
    r"C:/Program Files/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    r"C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    r"C:/Program Files/Microsoft/Edge/Application/msedge.exe",
    "/usr/bin/chromium",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_mmdc() -> str:
    """Locate the mermaid CLI.

    On Windows the extensionless `mmdc` npm drops in `node_modules/.bin` is a shell
    script, and handing it to `CreateProcess` fails with WinError 193. The sibling
    `mmdc.cmd` is the launcher that actually runs, so prefer it.
    """
    explicit = os.environ.get("MMDC")
    if explicit:
        if sys.platform == "win32" and not Path(explicit).suffix:
            windows_launcher = Path(explicit).with_suffix(".cmd")
            if windows_launcher.exists():
                return str(windows_launcher)
        return explicit
    found = shutil.which("mmdc")
    if found:
        return found
    raise SystemExit(
        "mermaid CLI not found.\n"
        "  npm install -g @mermaid-js/mermaid-cli\n"
        "or set MMDC=/path/to/mmdc"
    )


def find_browser() -> str | None:
    explicit = os.environ.get("PUPPETEER_EXECUTABLE_PATH")
    if explicit and Path(explicit).exists():
        return explicit
    for candidate in _CHROME_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    return None  # let puppeteer use its own bundled download


def render(mmdc: str, source: Path, config_path: Path, puppeteer_path: Path | None) -> bool:
    target = OUT / f"{source.stem}.svg"
    cmd = [mmdc, "-i", str(source), "-o", str(target), "-c", str(config_path), "-b", "transparent"]
    if puppeteer_path is not None:
        cmd += ["-p", str(puppeteer_path)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists():
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        print(f"  FAIL  {source.name}")
        for line in tail[:6]:
            print(f"        {line}")
        return False
    size = target.stat().st_size
    if size < 400:
        # mermaid writes a tiny SVG containing the parse error rather than exiting non-zero.
        print(
            f"  FAIL  {source.name} -> suspiciously small SVG ({size} bytes), likely a parse error"
        )
        return False
    print(f"  ok    {source.stem}.svg  ({size // 1024}kb)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("names", nargs="*", help="diagram stems to render (default: all)")
    parser.add_argument("--check", action="store_true", help="only report missing/stale SVGs")
    args = parser.parse_args()

    sources = sorted(SRC.glob("*.mmd"))
    if args.names:
        wanted = set(args.names)
        sources = [s for s in sources if s.stem in wanted]
        missing = wanted - {s.stem for s in sources}
        if missing:
            raise SystemExit(f"no such diagram(s): {', '.join(sorted(missing))}")
    if not sources:
        raise SystemExit(f"no .mmd files in {SRC}")

    if args.check:
        manifest = load_manifest()
        stale = []
        for source in sources:
            svg = OUT / f"{source.stem}.svg"
            if not svg.exists():
                stale.append(f"{source.stem}: never rendered")
            elif source.stem not in manifest:
                stale.append(f"{source.stem}: rendered before hashes were tracked - re-render")
            elif manifest[source.stem] != source_hash(source):
                stale.append(f"{source.stem}: source changed since the SVG was rendered")
        for line in stale:
            print(f"  STALE {line}")
        print(f"\n{len(sources) - len(stale)}/{len(sources)} up to date")
        return 1 if stale else 0

    mmdc = find_mmdc()
    browser = find_browser()
    OUT.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        config_path = Path(tmp) / "mermaid.json"
        config_path.write_text(json.dumps(MERMAID_CONFIG), encoding="utf-8")

        puppeteer_path: Path | None = None
        if browser:
            puppeteer_path = Path(tmp) / "puppeteer.json"
            puppeteer_path.write_text(
                json.dumps({"executablePath": browser, "args": ["--no-sandbox"]}),
                encoding="utf-8",
            )
            print(f"browser: {browser}")

        print(f"rendering {len(sources)} diagrams -> {OUT.relative_to(ROOT)}\n")
        manifest = load_manifest()
        failures = []
        for source in sources:
            if render(mmdc, source, config_path, puppeteer_path):
                manifest[source.stem] = source_hash(source)
            else:
                failures.append(source.name)
        save_manifest(manifest)

    print(f"\n{len(sources) - len(failures)}/{len(sources)} rendered")
    if failures:
        print("failed: " + ", ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
