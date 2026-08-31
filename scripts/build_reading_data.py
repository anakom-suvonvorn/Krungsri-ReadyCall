"""Inject the real menu and prompt data into `docs/reading/the_line.html`.

    uv run python scripts/build_reading_data.py
    uv run python scripts/build_reading_data.py --check

`the_line.html` is an interactive twin of `diagrams/12_the_menu.md`: it lets you press a
keypad and watch the walk. That is only worth anything if the menu it walks is **the real
one** — a page that shows a Thai label nobody would ever hear is worse than no page, because
it is convincing.

So the data is not typed into the HTML. It is extracted from the loaded `DomainPack` and
`PromptPack` and written into a single `<script id="ivr-data">` block, and
`tests/unit/test_voice_prompts.py` fails if the committed page is stale — the same mechanism
the generated diagrams use, for the same reason.

The *walk* in that page is still a hand-built twin of `services/ivr/machine.py`. Nothing can
check that automatically, so it says so in its own footer.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from readycall.console import enable_utf8  # noqa: E402
from readycall.domain.enums import ProductLine  # noqa: E402
from readycall.domainpack import DomainPack  # noqa: E402
from readycall.voiceprompts import PromptPack, PromptRole  # noqa: E402

PAGE = ROOT / "docs" / "reading" / "the_line.html"
BLOCK = re.compile(r'(<script id="ivr-data" type="application/json">)(.*?)(</script>)', re.DOTALL)
PROMOTABLE = (ProductLine.MOTOR, ProductLine.HEALTH, ProductLine.TRAVEL, ProductLine.LIFE)


def extract(pack: DomainPack, prompts: PromptPack) -> dict[str, object]:
    """Everything the page needs, and nothing it does not."""
    return {
        "settings": {
            "repeat_key": pack.menu_settings.repeat_key,
            "runaway_press_guard": pack.menu_settings.runaway_press_guard,
            "max_silences": pack.menu_settings.max_silences,
            "invalid_prompt": pack.menu_settings.invalid_prompt,
            "timeout_s": pack.menu_settings.timeout_s,
        },
        "menus": {
            menu_id: {
                "prompt": menu.prompt,
                "options": [
                    {
                        "key": option.key,
                        "label": option.label_th,
                        "line": option.product_line.value if option.product_line else None,
                        "intent": option.intent,
                        "next": option.next_menu,
                    }
                    for option in menu.options
                ],
            }
            for menu_id, menu in pack.menus.items()
        },
        "dids": {
            number: {
                # The label in `dids.yaml` is a sentence; the page has room for the first
                # clause only, and that clause is the part that names the number.
                "label": did.label.split(" - ")[0],
                "line": did.product_line.value,
                "greeting": did.greeting_prompt,
                "skip": did.skip_product_menu,
                "queue": did.default_queue,
                "assumed": did.assumed_intent,
            }
            for number, did in pack.dids.items()
        },
        "prompts": {pid: spec.text_th for pid, spec in prompts.prompts.items()},
        "roles": {role.value: prompts.id_for(role) for role in PromptRole},
        "queues": {code: pack.queue_for_intent(code) for code in pack.intents},
        "catch_all": {line.value: pack.catch_all_for(line).code for line in PROMOTABLE},
    }


def payload(pack: DomainPack, prompts: PromptPack) -> str:
    return json.dumps(extract(pack, prompts), ensure_ascii=False, separators=(",", ":"))


def current(html: str) -> str | None:
    match = BLOCK.search(html)
    return match.group(2) if match else None


def inject(html: str, data: str) -> str:
    match = BLOCK.search(html)
    if match is None:
        raise SystemExit(f'{PAGE} has no <script id="ivr-data"> block')
    return html[: match.start(2)] + data + html[match.end(2) :]


def main() -> int:
    enable_utf8()  # `B1`: Thai on a cp1252 console kills the process.
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the page is stale")
    args = parser.parse_args()

    pack = DomainPack.load(ROOT / "config")
    prompts = PromptPack.load(ROOT / "config" / "voice_prompts.yaml")
    prompts.validate_against(pack)

    html = PAGE.read_text(encoding="utf-8")
    fresh = payload(pack, prompts)

    if args.check:
        if current(html) != fresh:
            print(f"{PAGE.relative_to(ROOT)} is stale")
            print("run: uv run python scripts/build_reading_data.py")
            return 1
        print(f"{PAGE.relative_to(ROOT)} is up to date ({len(fresh)} chars of data)")
        return 0

    PAGE.write_text(inject(html, fresh), encoding="utf-8", newline="\n")
    print(f"wrote {len(fresh)} chars of menu + prompt data into {PAGE.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
