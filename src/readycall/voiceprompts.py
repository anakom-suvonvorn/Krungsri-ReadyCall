"""Loads `config/voice_prompts.yaml` into typed objects.

Every spoken line in the system is text in one YAML file, rendered to a clip at build
time and played from cache during the call (`D24`). This module is the reading half of
that: it parses the file, cross-validates it against the domain pack, and hands the IVR
either a `PromptSpec` or an already-rendered `SpokenLine`.

Two things here are load-bearing rather than tidiness:

**Roles, not ids.** The IVR asks for `PromptRole.INTAKE_OFFER`, never for the string
`"intake.offer"`. The mapping lives in the `flow:` block, so renaming a prompt is a
config edit, `services/` holds no prompt literals (`D28`), and the guard test can check
the mapping from both ends — every role resolves, and every prompt is reachable.

**Slots are declared and checked.** A prompt whose text contains `{position}` must list
it, and a prompt that declares a slot it never uses is a typo. Both fail at load. The
alternative is a `KeyError` inside a live call, or worse, a caller hearing a literal
`{position}`.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from readycall.domainpack import DomainPack
from readycall.errors import ConfigError
from readycall.logging import get_logger

log = get_logger(__name__)

#: `{slot}` in a prompt's text. Deliberately strict: lowercase and underscores only, so a
#: stray brace in Thai punctuation cannot silently become a slot.
_SLOT = re.compile(r"\{([a-z][a-z0-9_]*)\}")


class PromptRole(StrEnum):
    """A point in the flow that needs a line, independent of which line fills it.

    Every member must appear in the `flow:` block of `voice_prompts.yaml`; a missing one
    is a startup error, not a silent gap that only shows up when a caller reaches it.
    """

    RECORDING_NOTICE = "recording_notice"
    MENU_OPTION = "menu_option"
    MENU_RESERVED_HINT = "menu_reserved_hint"
    NO_INPUT = "no_input"
    OPERATOR = "operator"
    IDENTIFY_REQUEST = "identify_request"
    IDENTIFY_OK = "identify_ok"
    IDENTIFY_FAILED = "identify_failed"
    QUEUE_POSITION = "queue_position"
    QUEUE_HOLD = "queue_hold"
    INTAKE_OFFER = "intake_offer"
    INTAKE_REOFFER = "intake_reoffer"
    INTAKE_START = "intake_start"
    INTAKE_DECLINED = "intake_declined"
    INTAKE_DONE = "intake_done"
    VOICEMAIL_OFFER = "voicemail_offer"
    VOICEMAIL_START = "voicemail_start"
    RATING_REQUEST = "rating_request"
    GOODBYE = "goodbye"


def clip_key(text: str, voice: str, engine: str) -> str:
    """The cache key for one rendered clip (`D24`).

    Hashing the **rendered text** rather than the prompt id is what makes dynamic lines
    cacheable at all: "ลำดับที่ 3" is the same clip for every caller who is third in the
    queue, so the pack warms up within minutes instead of never.
    """
    material = "\x1f".join((text, voice, engine))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


@dataclass(frozen=True, slots=True)
class PromptSpec:
    prompt_id: str
    text_th: str
    voice: str
    slots: tuple[str, ...] = ()
    note: str | None = None
    #: Slot values worth rendering at build time, as `((name, value), ...)` sets. A
    #: dynamic line is cached by its rendered text and so warms up on its own within
    #: minutes (`D24`) — `warm` just means the first caller does not pay for it either.
    #: Menu option lines need no entry here: the build derives those from `menus.yaml`.
    warm: tuple[tuple[tuple[str, str], ...], ...] = ()

    @property
    def is_dynamic(self) -> bool:
        """Rendered on first use and cached by rendered text, rather than pre-built."""
        return bool(self.slots)


@dataclass(frozen=True, slots=True)
class SpokenLine:
    """One line, resolved to the exact words the caller will hear.

    Carries the prompt id it came from so a transcript can say *which* line played, not
    just what it said — the same reason every other stage in this system records its
    provenance (`D18`).
    """

    prompt_id: str
    text: str
    voice: str
    slots: tuple[tuple[str, str], ...] = ()

    @property
    def is_dynamic(self) -> bool:
        return bool(self.slots)

    def clip_key(self, engine: str) -> str:
        return clip_key(self.text, self.voice, engine)

    def audio_ref(self, engine: str) -> str:
        """What gets handed to `TelephonyProvider.play()`."""
        return f"prompt://{engine}/{self.clip_key(engine)}"


@dataclass(frozen=True, slots=True)
class PromptPack:
    prompts: dict[str, PromptSpec]
    flow: dict[PromptRole, str]
    default_voice: str
    language: str
    source: Path

    # --- lookups ---------------------------------------------------------------------

    def spec(self, prompt_id: str) -> PromptSpec:
        try:
            return self.prompts[prompt_id]
        except KeyError as exc:
            raise ConfigError(f"unknown prompt id: {prompt_id!r}") from exc

    def id_for(self, role: PromptRole) -> str:
        try:
            return self.flow[role]
        except KeyError as exc:  # pragma: no cover - validate() rejects this at load
            raise ConfigError(f"no prompt mapped to role {role.value!r}") from exc

    def render(self, prompt_id: str, **slots: object) -> SpokenLine:
        """Fill a prompt's slots. Raises rather than guessing.

        A missing slot would reach the caller as a literal `{position}`, and an unexpected
        one means the call site and the wording have drifted apart — both are worth a loud
        failure in a test rather than a quiet one on the phone.
        """
        spec = self.spec(prompt_id)
        given = {name: str(value) for name, value in slots.items()}
        expected = set(spec.slots)
        if set(given) != expected:
            missing = sorted(expected - set(given))
            extra = sorted(set(given) - expected)
            detail = ", ".join(
                part
                for part in (
                    f"missing {missing}" if missing else "",
                    f"unexpected {extra}" if extra else "",
                )
                if part
            )
            raise ConfigError(f"prompt {prompt_id!r} slot mismatch: {detail}")
        text = spec.text_th.format(**given) if given else spec.text_th
        return SpokenLine(
            prompt_id=spec.prompt_id,
            text=text,
            voice=spec.voice,
            slots=tuple(sorted(given.items())),
        )

    def say(self, role: PromptRole, **slots: object) -> SpokenLine:
        return self.render(self.id_for(role), **slots)

    # --- the ids other config files point at -----------------------------------------

    def referenced_ids(self, pack: DomainPack) -> set[str]:
        """Every prompt id named anywhere outside this file.

        Collected here rather than in the test so the startup check and the test agree by
        construction — a guard that lists the references separately drifts from the code
        it is guarding, which is the exact failure it exists to prevent (`D72`).
        """
        ids = {menu.prompt for menu in pack.menus.values()}
        ids |= {did.greeting_prompt for did in pack.dids.values()}
        ids.add(pack.menu_settings.invalid_prompt)
        ids |= set(self.flow.values())
        language_prompt = pack.language_menu_prompt
        if language_prompt:
            ids.add(language_prompt)
        return ids

    # --- validation ------------------------------------------------------------------

    def validate(self) -> None:
        problems: list[str] = []

        for prompt_id, spec in self.prompts.items():
            if not spec.text_th.strip():
                # This is a Thai product; a prompt with no Thai text cannot be spoken.
                problems.append(f"{prompt_id} has no Thai text")
            used = set(_SLOT.findall(spec.text_th))
            declared = set(spec.slots)
            for name in sorted(used - declared):
                problems.append(f"{prompt_id} uses {{{name}}} but does not declare it")
            for name in sorted(declared - used):
                problems.append(f"{prompt_id} declares slot {name!r} but never uses it")
            for entry in spec.warm:
                if {name for name, _ in entry} != declared:
                    # A warm set that does not fill the slots renders nothing, and the
                    # build reports a clip it never produced.
                    problems.append(f"{prompt_id} has a warm set that does not match its slots")

        for role in PromptRole:
            mapped = self.flow.get(role)
            if mapped is None:
                problems.append(f"flow has no prompt for role {role.value!r}")
            elif mapped not in self.prompts:
                problems.append(f"role {role.value!r} -> undefined prompt {mapped!r}")

        if problems:
            raise ConfigError(
                f"{self.source} is inconsistent:\n  - " + "\n  - ".join(sorted(problems))
            )

    def validate_against(self, pack: DomainPack) -> None:
        """Cross-check with the domain pack. Called at startup, and by the guard test.

        The failure this prevents is specific and silent: `menus.yaml` names a prompt id,
        nothing defines it, and the caller hears a gap where a menu should be. The same
        shape as `D72` — a list enforced on one side and read from the other drifts unless
        something checks.
        """
        problems = [
            f"referenced but not defined: {prompt_id}"
            for prompt_id in sorted(self.referenced_ids(pack))
            if prompt_id not in self.prompts
        ]
        orphans = sorted(set(self.prompts) - self.referenced_ids(pack))
        problems += [f"defined but nothing plays it: {prompt_id}" for prompt_id in orphans]
        if problems:
            raise ConfigError(
                "voice prompts and the domain pack disagree:\n  - " + "\n  - ".join(problems)
            )

    # --- loading ---------------------------------------------------------------------

    @classmethod
    def load(cls, path: Path | str = Path("config/voice_prompts.yaml")) -> PromptPack:
        source = Path(path)
        if source.is_dir():
            source = source / "voice_prompts.yaml"
        if not source.exists():
            raise ConfigError(f"voice prompts file missing: {source}")
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ConfigError(f"{source} must contain a mapping at the top level")

        defaults: dict[str, Any] = raw.get("defaults") or {}
        default_voice = str(defaults.get("voice", "default"))

        prompts: dict[str, PromptSpec] = {}
        for prompt_id, body in (raw.get("prompts") or {}).items():
            if not isinstance(body, dict):
                raise ConfigError(f"prompt {prompt_id!r} must be a mapping")
            prompts[str(prompt_id)] = PromptSpec(
                prompt_id=str(prompt_id),
                # `>-` folded scalars keep a trailing newline off but leave inner
                # newlines as spaces, which is what a spoken line wants.
                text_th=str(body.get("text_th", "")).strip(),
                voice=str(body.get("voice", default_voice)),
                slots=tuple(str(s) for s in (body.get("slots") or ())),
                note=str(body["note"]).strip() if body.get("note") else None,
                warm=tuple(
                    tuple(sorted((str(k), str(v)) for k, v in (entry or {}).items()))
                    for entry in (body.get("warm") or ())
                ),
            )
        if not prompts:
            raise ConfigError(f"{source} defines no prompts")

        flow: dict[PromptRole, str] = {}
        for role_name, prompt_id in (raw.get("flow") or {}).items():
            try:
                flow[PromptRole(str(role_name))] = str(prompt_id)
            except ValueError as exc:
                raise ConfigError(f"{source}: unknown flow role {role_name!r}") from exc

        loaded = cls(
            prompts=prompts,
            flow=flow,
            default_voice=default_voice,
            language=str(defaults.get("language", "th")),
            source=source,
        )
        loaded.validate()
        log.info("voice prompts loaded", prompts=len(prompts), voice=default_voice)
        return loaded


def load_prompt_pack(config_dir: Path | str, pack: DomainPack | None = None) -> PromptPack:
    """Load and, when the domain pack is available, cross-validate. Startup gate."""
    prompts = PromptPack.load(Path(config_dir) / "voice_prompts.yaml")
    if pack is not None:
        prompts.validate_against(pack)
    return prompts


__all__ = [
    "PromptPack",
    "PromptRole",
    "PromptSpec",
    "SpokenLine",
    "clip_key",
    "load_prompt_pack",
]
