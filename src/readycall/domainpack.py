"""Loads the insurance domain pack from `config/` into typed objects.

Everything insurance-specific lives in YAML, never in code (`D28`) — but "in YAML" must
not mean "unchecked". This module parses `intents.yaml`, `skills.yaml`, `dids.yaml` and
`menus.yaml` once at startup, cross-validates them, and hands the rest of the system typed
objects instead of nested dicts.

Loading it is also a **startup gate**: a dangling skill reference or a queue that overflows
into a loop raises `ConfigError` here rather than surfacing as a mystery at 2am. Two rules
that came from decisions rather than tidiness are enforced too:

* every skill must be held by at least two agents (`D22`) — checked separately, against the
  live roster, since this module only sees config;
* every product line must offer a catch-all reason, or a caller with an unexpected problem
  has nowhere to go.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from readycall.domain.enums import ProductLine, Urgency
from readycall.errors import ConfigError
from readycall.logging import get_logger

log = get_logger(__name__)


def _read(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"domain pack file missing: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    return data


@dataclass(frozen=True, slots=True)
class IntentSpec:
    code: str
    label_th: str
    label_en: str | None
    line: ProductLine
    skill: str
    default_urgency: Urgency
    required_slots: tuple[str, ...]
    playbook: str
    is_catch_all: bool = False
    sensitive: bool = False

    @property
    def is_line_specific(self) -> bool:
        return self.line is not ProductLine.UNKNOWN


@dataclass(frozen=True, slots=True)
class SkillSpec:
    code: str
    label_th: str
    queue: str


@dataclass(frozen=True, slots=True)
class QueueSpec:
    queue_id: str
    label_th: str
    required_skill: str
    sla_seconds: int
    hours: str
    overflow_queue: str | None


@dataclass(frozen=True, slots=True)
class ChallengeSpec:
    """One way an agent may verify who is on the phone (`D42`, `D72`).

    In config rather than in code because the list is domain policy, not mechanism, and
    because it must reach the workstation as data — it existed twice and the copies could
    drift, with the server refusing an option the screen had offered.
    """

    code: str
    label_th: str
    #: Whether this challenge is strong enough to reach `L3_VERIFIED`.
    promotes: bool = True
    #: Whether the agent must type what they actually did. True for `other`, where a blank
    #: is the unfalsifiable audit row `D42` exists to prevent.
    requires_note: bool = False


@dataclass(frozen=True, slots=True)
class DidSpec:
    number: str
    label: str
    product_line: ProductLine
    default_queue: str
    greeting_prompt: str
    skip_product_menu: bool
    urgency_floor: Urgency | None = None
    assumed_intent: str | None = None


@dataclass(frozen=True, slots=True)
class MenuOption:
    key: str
    label_th: str
    intent: str | None = None
    product_line: ProductLine | None = None
    next_menu: str | None = None


@dataclass(frozen=True, slots=True)
class MenuSpec:
    menu_id: str
    prompt: str
    options: tuple[MenuOption, ...]

    def option_for(self, key: str) -> MenuOption | None:
        for option in self.options:
            if option.key == key:
                return option
        return None


@dataclass(frozen=True, slots=True)
class MenuWalk:
    """The outcome of a caller pressing their way through the menus."""

    product_line: ProductLine
    intent_code: str | None
    path: tuple[str, ...]
    completed: bool


@dataclass(frozen=True, slots=True)
class MenuSettings:
    barge_in: bool
    repeat_key: str
    timeout_s: float
    invalid_prompt: str
    #: NOT an attempt limit. A wrong key is never a strike (`D82`) — this exists only to
    #: stop a stuck DTMF sender looping for ever, and is set far past human behaviour.
    runaway_press_guard: int = 40
    #: Silence IS bounded, because it does not prove anybody is there. One re-prompt.
    max_silences: int = 2


@dataclass(frozen=True, slots=True)
class PersonalisationSpec:
    """How a recognised caller's menu is reordered (`D37`).

    Every parameter is here rather than in code because all of them are domain policy the
    bank owns: which claim statuses count as open, how soon a renewal is "soon", how long
    an app view stays relevant (`D28`). The weights are guesses until there is real call
    data to tune them against.
    """

    enabled: bool
    max_promoted: int
    renumber: bool
    weights: dict[str, float]
    open_claim_statuses: frozenset[str]
    renewal_window_days: int
    app_view_window_hours: int


@dataclass(frozen=True, slots=True)
class LanguageMenuSpec:
    """The language menu (`D38`) — modelled now, Thai-only in behaviour.

    Loaded rather than ignored so its prompt id is part of the referenced set that
    `voiceprompts` checks. A prompt that only becomes reachable the day someone flips
    `enabled` is exactly the one nobody notices is missing.
    """

    enabled: bool
    prompt: str
    default: str


@dataclass(frozen=True, slots=True)
class DomainPack:
    intents: dict[str, IntentSpec]
    skills: dict[str, SkillSpec]
    queues: dict[str, QueueSpec]
    dids: dict[str, DidSpec]
    menus: dict[str, MenuSpec]
    challenges: dict[str, ChallengeSpec]
    menu_settings: MenuSettings
    #: Words the ASR is nudged toward (`D9`). Config, not code, because they are
    #: insurance-specific and `services/` may not hold a policy concept (`D28`).
    stt_vocabulary: tuple[str, ...] = ()
    language_menu: LanguageMenuSpec | None = None
    personalisation: PersonalisationSpec | None = None
    source_dir: Path = field(default=Path("config"))

    # --- lookups the rest of the system actually uses --------------------------------

    def intent(self, code: str) -> IntentSpec:
        try:
            return self.intents[code]
        except KeyError as exc:
            raise ConfigError(f"unknown intent code: {code!r}") from exc

    def queue_for_intent(self, code: str) -> str:
        """intent -> skill -> queue. The routing chain, in one place."""
        return self.skills[self.intent(code).skill].queue

    def catch_all_for(self, line: ProductLine) -> IntentSpec:
        """The "something else" intent for a line.

        A caller whose reason is not on our list is a caller, not an error: we keep the
        product context we already have and send them to that line's generalist, rather
        than dropping to a fully generic unknown.
        """
        for spec in self.intents.values():
            if spec.is_catch_all and spec.line is line:
                return spec
        return self.intents["unknown"]

    def reason_menu_for(self, line: ProductLine) -> MenuSpec | None:
        """The step-2 menu for a product line, found via the step-1 menu's links.

        Keeps the two menus in one place: adding a line means editing `menus.yaml`, not
        adding a lookup table here.
        """
        root = self.menus.get("product_line")
        if root is None:
            return None
        for option in root.options:
            if option.product_line is line and option.next_menu:
                return self.menus.get(option.next_menu)
        return None

    def walk_menu(self, keys: list[str], *, start: str = "product_line") -> MenuWalk:
        """Follow a sequence of keypresses through the menu tree.

        Returns what the caller actually selected — the product line, the intent, and the
        path taken. An unrecognised key stops the walk rather than guessing, which is what
        the real IVR does before falling back to a human (`D37`).
        """
        line = ProductLine.UNKNOWN
        intent: str | None = None
        walked: list[str] = []
        current = self.menus.get(start)

        for key in keys:
            if current is None:
                break
            option = current.option_for(key)
            if option is None:
                return MenuWalk(
                    product_line=line, intent_code=intent, path=tuple(walked), completed=False
                )
            walked.append(key)
            if option.product_line is not None:
                line = option.product_line
            if option.intent is not None:
                intent = option.intent
                if line is ProductLine.UNKNOWN:
                    line = self.intents[option.intent].line
            current = self.menus.get(option.next_menu) if option.next_menu else None

        return MenuWalk(
            product_line=line,
            intent_code=intent,
            path=tuple(walked),
            completed=intent is not None,
        )

    @property
    def language_menu_prompt(self) -> str | None:
        """The prompt id the language menu would play, enabled or not (`D38`)."""
        return self.language_menu.prompt if self.language_menu else None

    def did(self, number: str) -> DidSpec | None:
        return self.dids.get(number)

    def line_for_did(self, number: str | None) -> ProductLine:
        if number is None:
            return ProductLine.UNKNOWN
        spec = self.dids.get(number)
        return spec.product_line if spec else ProductLine.UNKNOWN

    # --- loading ---------------------------------------------------------------------

    @staticmethod
    def _load_stt_vocabulary(directory: Path) -> tuple[str, ...]:
        """Optional: a deployment with no jargon worth nudging simply omits the file."""
        path = directory / "stt_vocabulary.yaml"
        if not path.exists():
            return ()
        raw = _read(path)
        terms = raw.get("terms") or []
        if not isinstance(terms, list):
            raise ConfigError(f"{path}: `terms` must be a list")
        return tuple(str(t).strip() for t in terms if str(t).strip())

    @classmethod
    def load(cls, config_dir: Path | str = Path("config")) -> DomainPack:
        directory = Path(config_dir)
        intents = cls._load_intents(_read(directory / "intents.yaml"))
        skills, queues = cls._load_skills(_read(directory / "skills.yaml"))
        dids = cls._load_dids(_read(directory / "dids.yaml"))
        menus, settings, personalisation, language_menu = cls._load_menus(
            _read(directory / "menus.yaml")
        )
        challenges = cls._load_challenges(_read(directory / "challenges.yaml"))
        vocabulary = cls._load_stt_vocabulary(directory)

        pack = cls(
            intents=intents,
            skills=skills,
            queues=queues,
            dids=dids,
            menus=menus,
            challenges=challenges,
            menu_settings=settings,
            stt_vocabulary=vocabulary,
            language_menu=language_menu,
            personalisation=personalisation,
            source_dir=directory,
        )
        pack.validate()
        log.info(
            "domain pack loaded",
            intents=len(intents),
            skills=len(skills),
            queues=len(queues),
            dids=len(dids),
            menus=len(menus),
            challenges=len(challenges),
        )
        return pack

    @staticmethod
    def _load_challenges(raw: dict[str, Any]) -> dict[str, ChallengeSpec]:
        out: dict[str, ChallengeSpec] = {}
        for entry in raw.get("challenges", []):
            spec = ChallengeSpec(
                code=str(entry["code"]),
                label_th=str(entry["label_th"]),
                promotes=bool(entry.get("promotes", True)),
                requires_note=bool(entry.get("requires_note", False)),
            )
            out[spec.code] = spec
        if not out:
            raise ConfigError("challenges.yaml defines no challenges")
        return out

    @staticmethod
    def _load_intents(raw: dict[str, Any]) -> dict[str, IntentSpec]:
        out: dict[str, IntentSpec] = {}
        for code, body in (raw.get("intents") or {}).items():
            try:
                out[code] = IntentSpec(
                    code=code,
                    label_th=body["label_th"],
                    label_en=body.get("label_en"),
                    line=ProductLine(body.get("line", "unknown")),
                    skill=body["skill"],
                    default_urgency=Urgency(body["default_urgency"]),
                    required_slots=tuple(body.get("required_slots") or ()),
                    playbook=body.get("playbook", "generic"),
                    is_catch_all=bool(body.get("is_catch_all", False)),
                    sensitive=bool(body.get("sensitive", False)),
                )
            except (KeyError, ValueError) as exc:
                raise ConfigError(f"intent {code!r} is malformed: {exc}") from exc
        if not out:
            raise ConfigError("intents.yaml defines no intents")
        return out

    @staticmethod
    def _load_skills(raw: dict[str, Any]) -> tuple[dict[str, SkillSpec], dict[str, QueueSpec]]:
        skills = {
            code: SkillSpec(code=code, label_th=body.get("label_th", code), queue=body["queue"])
            for code, body in (raw.get("skills") or {}).items()
        }
        queues = {
            queue_id: QueueSpec(
                queue_id=queue_id,
                label_th=body.get("label_th", queue_id),
                required_skill=body["required_skill"],
                sla_seconds=int(body.get("sla_seconds", 120)),
                hours=body.get("hours", "business"),
                overflow_queue=body.get("overflow_queue"),
            )
            for queue_id, body in (raw.get("queues") or {}).items()
        }
        return skills, queues

    @staticmethod
    def _load_dids(raw: dict[str, Any]) -> dict[str, DidSpec]:
        return {
            number: DidSpec(
                number=number,
                label=body.get("label", number),
                product_line=ProductLine(body.get("product_line", "unknown")),
                default_queue=body["default_queue"],
                greeting_prompt=body.get("greeting_prompt", "greeting.general"),
                skip_product_menu=bool(body.get("skip_product_menu", False)),
                urgency_floor=Urgency(body["urgency_floor"]) if "urgency_floor" in body else None,
                assumed_intent=body.get("assumed_intent"),
            )
            for number, body in (raw.get("dids") or {}).items()
        }

    @staticmethod
    def _load_menus(
        raw: dict[str, Any],
    ) -> tuple[
        dict[str, MenuSpec], MenuSettings, PersonalisationSpec | None, LanguageMenuSpec | None
    ]:
        settings_raw = raw.get("settings") or {}
        settings = MenuSettings(
            barge_in=bool(settings_raw.get("barge_in", True)),
            repeat_key=str(settings_raw.get("repeat_key", "9")),
            timeout_s=float(settings_raw.get("timeout_s", 7)),
            invalid_prompt=str(settings_raw.get("invalid_prompt", "menu.invalid")),
            runaway_press_guard=int(settings_raw.get("runaway_press_guard", 40)),
            max_silences=int(settings_raw.get("max_silences", 2)),
        )
        menus = {
            menu_id: MenuSpec(
                menu_id=menu_id,
                prompt=body["prompt"],
                options=tuple(
                    MenuOption(
                        key=str(option["key"]),
                        label_th=option["label_th"],
                        intent=option.get("intent"),
                        product_line=(
                            ProductLine(option["product_line"])
                            if "product_line" in option
                            else None
                        ),
                        next_menu=option.get("next"),
                    )
                    for option in body["options"]
                ),
            )
            for menu_id, body in (raw.get("menus") or {}).items()
        }
        language_raw = raw.get("language_menu") or {}
        language_menu = (
            LanguageMenuSpec(
                enabled=bool(language_raw.get("enabled", False)),
                prompt=str(language_raw["prompt"]),
                default=str(language_raw.get("default", "th")),
            )
            if language_raw.get("prompt")
            else None
        )
        p_raw = raw.get("personalisation") or {}
        personalisation = (
            PersonalisationSpec(
                enabled=bool(p_raw.get("enabled", True)),
                max_promoted=int(p_raw.get("max_promoted", 2)),
                renumber=bool(p_raw.get("renumber", True)),
                weights={
                    str(rule["signal"]): float(rule.get("weight", 1.0))
                    for rule in (p_raw.get("rules") or ())
                },
                open_claim_statuses=frozenset(
                    str(s) for s in (p_raw.get("open_claim_statuses") or ())
                ),
                renewal_window_days=int(p_raw.get("renewal_window_days", 30)),
                app_view_window_hours=int(p_raw.get("app_view_window_hours", 24)),
            )
            if p_raw
            else None
        )
        return menus, settings, personalisation, language_menu

    # --- validation ------------------------------------------------------------------

    def validate(self) -> None:
        """Cross-file consistency. Raises `ConfigError` with a specific message."""
        problems: list[str] = []

        for code, spec in self.intents.items():
            if spec.skill not in self.skills:
                problems.append(f"intent {code!r} -> unknown skill {spec.skill!r}")

        for code, skill in self.skills.items():
            if skill.queue not in self.queues:
                problems.append(f"skill {code!r} -> unknown queue {skill.queue!r}")

        for queue_id, queue in self.queues.items():
            if queue.required_skill not in self.skills:
                problems.append(f"queue {queue_id!r} -> unknown skill {queue.required_skill!r}")
            seen, current = {queue_id}, queue.overflow_queue
            while current is not None:
                if current not in self.queues:
                    problems.append(f"queue {queue_id!r} overflows to unknown {current!r}")
                    break
                if current in seen:
                    # A cycle means a call could be handed round for ever.
                    problems.append(f"overflow cycle involving {current!r}")
                    break
                seen.add(current)
                current = self.queues[current].overflow_queue

        for number, did in self.dids.items():
            if did.default_queue not in self.queues:
                problems.append(f"did {number} -> unknown queue {did.default_queue!r}")
            if did.assumed_intent and did.assumed_intent not in self.intents:
                problems.append(f"did {number} -> unknown intent {did.assumed_intent!r}")
            if did.skip_product_menu and did.product_line is ProductLine.UNKNOWN:
                # Skipping the question while not knowing the answer is how a caller ends
                # up silently in the wrong queue.
                problems.append(f"did {number} skips the product menu but has no product line")

        reserved = {self.menu_settings.repeat_key}
        for menu_id, menu in self.menus.items():
            keys = [o.key for o in menu.options]
            if len(keys) != len(set(keys)):
                problems.append(f"menu {menu_id!r} has duplicate keys")
            if set(keys) & reserved:
                problems.append(f"menu {menu_id!r} reuses a reserved key")
            for option in menu.options:
                if option.intent and option.intent not in self.intents:
                    problems.append(f"menu {menu_id!r} key {option.key} -> unknown intent")
                if option.next_menu and option.next_menu not in self.menus:
                    problems.append(f"menu {menu_id!r} key {option.key} -> unknown menu")
            if menu_id.endswith("_reason") and not any(
                option.intent and self.intents[option.intent].is_catch_all
                for option in menu.options
                if option.intent in self.intents
            ):
                problems.append(f"menu {menu_id!r} has no catch-all option")

        for line in (ProductLine.MOTOR, ProductLine.HEALTH, ProductLine.TRAVEL, ProductLine.LIFE):
            if not any(s.is_catch_all and s.line is line for s in self.intents.values()):
                problems.append(f"product line {line.value} has no catch-all intent")

        if "unknown" not in self.intents:
            problems.append("intents.yaml must define an `unknown` intent")

        if problems:
            raise ConfigError(
                "domain pack is inconsistent:\n  - " + "\n  - ".join(sorted(problems))
            )

    def validate_roster(self, skills_held: dict[str, int]) -> None:
        """Every skill needs at least two active holders (`D22`).

        If one person is the only one who can handle motor claims, no scoring function can
        save the queue — that is a staffing problem, and the system should say so out loud
        rather than let it surface as mysterious wait times on demo day.
        """
        thin = sorted(code for code in self.skills if skills_held.get(code, 0) < 2)
        if thin:
            raise ConfigError(
                "these skills have fewer than two active agents, so the queue cannot be "
                f"balanced: {', '.join(thin)}"
            )
