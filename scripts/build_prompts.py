"""Render every spoken line to a cached clip, and record what was rendered.

    uv run python scripts/build_prompts.py            # render what changed
    uv run python scripts/build_prompts.py --check    # fail if the manifest is stale
    uv run python scripts/build_prompts.py --list     # show the work list, render nothing

This is the build half of `D24`. Nothing here runs during a call: the IVR plays a file,
and this is what puts the file there. Three properties are the whole point —

* **Editing Thai in YAML changes what the caller hears after one re-render.** No studio,
  no vendor round trip at call time, and it works with no internet on the day.
* **Unchanged lines are skipped**, because the cache key is `hash(text, voice, engine)`.
  Change a comma and exactly one clip is re-rendered.
* **The same rendered text is one clip, wherever it came from.** "ติดตามสถานะเคลม" is key
  `3` in both the motor and the health menu, so it is rendered once and played by both.
  That dedupe is not an optimisation detail — it is why dynamic lines are cacheable at
  all, and it is what makes the queue-position line warm up in minutes.

**What the work list contains, and why it is derived rather than listed.** Static prompts
are obvious. Menu option lines are *not* in `voice_prompts.yaml` at all: a menu is a
lead-in plus one rendered line per option (`D80`), and the labels live in `menus.yaml`,
so the build reads them from the domain pack. Writing them out here would create a second
copy of every menu label, which is the drift this whole phase exists to prevent.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.adapters.tts import build_tts  # noqa: E402
from readycall.config import Settings, load_settings  # noqa: E402
from readycall.console import enable_utf8  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402
from readycall.ports.tts import TtsEngine, VoiceSpec  # noqa: E402
from readycall.voiceprompts import PromptPack, PromptRole, SpokenLine  # noqa: E402

MANIFEST_VERSION = 1


def work_list(prompts: PromptPack, pack: DomainPack) -> list[SpokenLine]:
    """Every distinct line the system can be asked to play, deduped by rendered text."""
    lines: list[SpokenLine] = []

    for prompt_id, spec in prompts.prompts.items():
        if not spec.is_dynamic:
            lines.append(prompts.render(prompt_id))
            continue
        for entry in spec.warm:
            lines.append(prompts.render(prompt_id, **dict(entry)))

    # Menu lines, derived from the menus themselves (`D80`).
    for menu in pack.menus.values():
        for option in menu.options:
            lines.append(prompts.say(PromptRole.MENU_OPTION, key=option.key, label=option.label_th))
    settings = pack.menu_settings
    lines.append(prompts.say(PromptRole.MENU_RESERVED_HINT, repeat_key=settings.repeat_key))

    unique: dict[tuple[str, str], SpokenLine] = {}
    for line in lines:
        # Keyed by (text, voice) rather than by prompt id: two prompts that say the same
        # words in the same voice are one clip, and that is the cache working.
        unique.setdefault((line.text, line.voice), line)
    return sorted(unique.values(), key=lambda line: (line.prompt_id, line.text))


def manifest_path(settings: Settings) -> Path:
    return ROOT / settings.prompts_dir / "voice" / "manifest.json"


def load_manifest(path: Path) -> dict[str, dict[str, object]]:
    """Existing clips, keyed by clip key. Missing or unreadable means "render it all"."""
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    clips = raw.get("clips", []) if isinstance(raw, dict) else []
    return {str(clip["clip_key"]): clip for clip in clips if isinstance(clip, dict)}


def drift(
    lines: list[SpokenLine], existing: dict[str, dict[str, object]], engine_name: str
) -> tuple[list[str], list[str]]:
    """(clip keys wanted but not built, clip keys built but no longer wanted).

    Shared by `--check` and by the test that runs it, so the two cannot disagree about
    what "stale" means — a check whose test re-implements it is two checks (`D72`).
    """
    wanted = {line.clip_key(engine_name) for line in lines}
    return sorted(wanted - set(existing)), sorted(set(existing) - wanted)


async def render(
    lines: list[SpokenLine],
    *,
    engine: TtsEngine,
    engine_name: str,
    existing: dict[str, dict[str, object]],
    force: bool,
) -> tuple[list[dict[str, object]], int]:
    """Render what is missing, reuse what is not. Returns (clips, rendered_count)."""
    clips: list[dict[str, object]] = []
    rendered = 0
    for line in lines:
        key = line.clip_key(engine_name)
        cached = None if force else existing.get(key)
        if cached is not None:
            clips.append(cached)
            continue
        audio = await engine.synthesize(line.text, VoiceSpec(voice=line.voice))
        rendered += 1
        clips.append(
            {
                "clip_key": key,
                "prompt_id": line.prompt_id,
                "text": line.text,
                "voice": line.voice,
                "slots": dict(line.slots),
                "duration_ms": round(audio.duration_ms, 1),
                "sample_rate": audio.sample_rate,
                "storage_ref": audio.storage_ref,
            }
        )
    clips.sort(key=lambda clip: (str(clip["prompt_id"]), str(clip["clip_key"])))
    return clips, rendered


def write_manifest(path: Path, *, engine_name: str, prompts: PromptPack, clips: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(
        {
            "version": MANIFEST_VERSION,
            "engine": engine_name,
            "default_voice": prompts.default_voice,
            "language": prompts.language,
            "clips": clips,
        },
        ensure_ascii=False,
        indent=2,
        sort_keys=False,
    )
    # Explicit LF: this file is committed, and a CRLF rewrite on Windows would show up
    # as the whole pack changing every time somebody runs the build.
    path.write_text(body + "\n", encoding="utf-8", newline="\n")


async def main() -> int:
    enable_utf8()  # Thai on a cp1252 console kills the process (`B1`).
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if anything is stale")
    parser.add_argument("--force", action="store_true", help="re-render everything")
    parser.add_argument("--list", action="store_true", help="print the work list only")
    parser.add_argument("--config", default=None, help="config directory")
    args = parser.parse_args()

    settings = load_settings(**({"config_dir": Path(args.config)} if args.config else {}))
    pack = DomainPack.load(ROOT / settings.config_dir)
    prompts = PromptPack.load(ROOT / settings.config_dir / "voice_prompts.yaml")
    prompts.validate_against(pack)

    lines = work_list(prompts, pack)
    if args.list:
        for line in lines:
            marker = "~" if line.is_dynamic else " "
            print(f"{marker} {line.prompt_id:<24} {line.text}")
        print(f"\n{len(lines)} distinct clips")
        return 0

    engine = build_tts(settings)
    engine_name = engine.name
    path = manifest_path(settings)
    existing = {} if args.force else load_manifest(path)

    if args.check:
        stale, orphaned = drift(lines, existing, engine_name)
        if stale or orphaned:
            print(f"prompt pack is stale ({path.relative_to(ROOT)}):")
            for key in stale:
                clip = next(line for line in lines if line.clip_key(engine_name) == key)
                print(f"  missing  {clip.prompt_id:<24} {clip.text}")
            for key in orphaned:
                print(f"  orphaned {existing[key].get('prompt_id')!s:<24} {existing[key]['text']}")
            print("\nrun: uv run python scripts/build_prompts.py")
            return 1
        print(f"prompt pack is up to date: {len(lines)} clips, engine={engine_name}")
        return 0

    clips, rendered = await render(
        lines, engine=engine, engine_name=engine_name, existing=existing, force=args.force
    )
    write_manifest(path, engine_name=engine_name, prompts=prompts, clips=clips)
    await engine.close()

    reused = len(clips) - rendered
    print(
        f"{len(clips)} clips, engine={engine_name}: {rendered} rendered, {reused} reused "
        f"-> {path.relative_to(ROOT)}"
    )
    if engine_name == "null":
        print(
            "\nNOTE: the null engine records lines and synthesises no audio, so this is a\n"
            "manifest rather than a playable pack. A real voice is a TTS_ENGINE change and\n"
            "a re-run - chosen on a listening test of these actual lines, not on a spec\n"
            "sheet (INTEGRATIONS.md 4)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
