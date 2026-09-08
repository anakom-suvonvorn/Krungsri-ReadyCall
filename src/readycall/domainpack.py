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

from readycall.domain.enums import AssuranceLevel, ProductLine, Urgency
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
    #: Whether this call ENDS by handing the customer to the insurer (`D117`). A broker
    #: does not adjudicate claims - it takes the notification, gathers what the insurer
    #: will ask for, and hands over. The workstation says so, and the wrap-up offers
    #: `handed_to_insurer` as the disposition.
    handoff_to_insurer: bool = False

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


#: How `playbooks.yaml` spells assurance levels. Short on purpose - the file is edited by
#: whoever owns the domain, not by whoever owns the enum.
_ASSURANCE_BY_NAME: dict[str, AssuranceLevel] = {
    "l0": AssuranceLevel.L0_ANONYMOUS,
    "l1": AssuranceLevel.L1_PROBABLE,
    "l2": AssuranceLevel.L2_STRONG,
    "l3": AssuranceLevel.L3_VERIFIED,
}


@dataclass(frozen=True, slots=True)
class PlaybookStep:
    """One recommended action, and the assurance it needs before being shown (`D56`)."""

    text_th: str
    needs: AssuranceLevel


@dataclass(frozen=True, slots=True)
class PlaybookSpec:
    """The ordered actions for one intent (`D118`, closing `Q19`).

    In config rather than in `services/brief/builder.py`, where it lived until the broker
    rewrite. It is insurance content, so `D28` always said it belonged here; what forced
    the move was `D117` roughly doubling it.
    """

    name: str
    steps: tuple[PlaybookStep, ...]


@dataclass(frozen=True, slots=True)
class ComparisonAttribute:
    """One figure a comparison ranks on, and which way is better (`D126`).

    `better` is the whole reason this is config rather than code: *"a higher room rate is
    better and a higher deductible is worse"* is insurance knowledge, and getting it
    backwards is silent — every plan still renders, in the wrong order, with a confident
    reason sentence attached.
    """

    kind: str
    label_th: str
    better: str  # "higher" | "lower"
    weight: float = 1.0

    @property
    def higher_is_better(self) -> bool:
        return self.better == "higher"


@dataclass(frozen=True, slots=True)
class ComparisonLine:
    label_th: str
    attributes: tuple[ComparisonAttribute, ...]


@dataclass(frozen=True, slots=True)
class ComparisonSettings:
    #: A phone table with eight columns is unreadable (`D126`).
    max_candidates: int = 3
    #: Below this, "better" is noise rather than a finding.
    min_material_improvement: float = 0.05


@dataclass(frozen=True, slots=True)
class InsurerSpec:
    """A company a broker can hand a call TO (`D124`).

    This list is a **menu, not a whitelist.** The carrier that underwrote the customer's
    policy comes from `Policy.insurer` and is offered whether or not it is here, because
    the real extract arrives on hackathon day carrying carriers nobody has typed into
    `insurers.yaml` — and a broker who cannot hand a claim to the company that actually
    wrote the policy has no product. Validating the two against each other would refuse
    to boot on exactly the data the `CoreDataProvider` seam exists to absorb (`D3`).
    """

    code: str
    name_th: str
    lines: frozenset[str] = frozenset()

    def covers(self, line: str | None) -> bool:
        return not self.lines or line is None or line in self.lines


@dataclass(frozen=True, slots=True)
class HandoffReasonSpec:
    """Why a call is leaving us (`D124`, `D117`).

    Closed, for `IntentCode`'s reason: free text here becomes twelve spellings of "claim"
    and nothing that can be counted. `requires_policy` means the reason is only coherent
    about cover the customer holds, so the broker is offered the **policy's own carrier**
    rather than the market menu — handing a claim to a company that did not write the
    policy is not a handoff, it is a wrong number.
    """

    code: str
    label_th: str
    requires_policy: bool = False


@dataclass(frozen=True, slots=True)
class AssistFormField:
    """One field on a form the broker pushes to the customer's screen."""

    name: str
    label_th: str
    type: str = "text"
    required: bool = False


@dataclass(frozen=True, slots=True)
class AssistToolGroup:
    group_id: str
    label_th: str
    order: int = 99


@dataclass(frozen=True, slots=True)
class AssistToolSpec:
    """One tool on the rail (`D120`, `D121`).

    `personal` is the gate, and it lives here rather than being derived from `kind`
    because the risk is a property of the tool's PURPOSE. A blank quote request and a
    prefilled claim form are both `kind: form` and are not the same disclosure — the
    first is true for anybody, the second is a statement about one customer.

    It is read from config and never from a request body: the workstation renders
    permissions, it never computes them, and a client able to declare its own push
    non-personal would be this gate's own bypass.
    """

    tool_id: str
    group: str
    label_th: str
    kind: str
    personal: bool
    hint_th: str | None = None
    #: Fields the server may fill from the customer's record. Only meaningful on a
    #: personal tool — prefilling is precisely what makes a form about somebody.
    prefill: tuple[str, ...] = ()
    fields: tuple[AssistFormField, ...] = ()
    #: Looks real, is not implemented, and the customer's screen says so (`D115`).
    stub: bool = False


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


#: The two situations an APP contact can be in (`D122`). Not used by the IVR, which shows
#: every option because a keypad caller has told us nothing yet.
MENU_CONTEXTS = ("plan", "general")


def _menu_contexts(menu_id: str, option: dict[str, Any]) -> frozenset[str]:
    raw = option.get("contexts")
    if raw is None:
        return frozenset(MENU_CONTEXTS)
    values = frozenset(str(v) for v in raw)
    unknown = values - set(MENU_CONTEXTS)
    if unknown:
        raise ConfigError(
            f"menu {menu_id!r} option {option.get('key')!r} names unknown "
            f"context(s) {sorted(unknown)}; valid are {list(MENU_CONTEXTS)}"
        )
    if not values:
        raise ConfigError(
            f"menu {menu_id!r} option {option.get('key')!r} has an empty `contexts` - "
            "an option nobody can ever see is dead config, not a hidden option"
        )
    return values


@dataclass(frozen=True, slots=True)
class MenuOption:
    """One option a caller can pick, on the keypad or in the app.

    `contexts` is what stops the app rendering the phone's menu verbatim (`D122`). The
    IVR ignores it entirely and always offers everything — a caller on a keypad has told
    us nothing about what they hold, so there is nothing to filter on. The app knows
    whether the customer tapped a policy they own or asked about something else, and those
    are genuinely different menus: offering *"buy travel insurance"* under a travel policy
    somebody already holds is the bug this exists to prevent.
    """

    key: str
    label_th: str
    intent: str | None = None
    product_line: ProductLine | None = None
    next_menu: str | None = None
    contexts: frozenset[str] = frozenset(MENU_CONTEXTS)
    #: Wording for the "about a policy I hold" surface, when the same intent is a
    #: different conversation there. Falls back to `label_th`.
    label_plan_th: str | None = None

    def label_for(self, context: str) -> str:
        if context == "plan" and self.label_plan_th:
            return self.label_plan_th
        return self.label_th

    def shown_in(self, context: str) -> bool:
        return context in self.contexts


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
    playbooks: dict[str, PlaybookSpec]
    assist_tools: dict[str, AssistToolSpec]
    assist_groups: dict[str, AssistToolGroup]
    insurers: dict[str, InsurerSpec]
    handoff_reasons: dict[str, HandoffReasonSpec]
    comparison_lines: dict[str, ComparisonLine]
    comparison_settings: ComparisonSettings
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
        playbooks = cls._load_playbooks(_read(directory / "playbooks.yaml"))
        assist_tools, assist_groups = cls._load_assist_tools(_read(directory / "assist_tools.yaml"))
        insurers, handoff_reasons = cls._load_insurers(_read(directory / "insurers.yaml"))
        comparison_lines, comparison_settings = cls._load_comparison(
            _read(directory / "comparison.yaml")
        )
        vocabulary = cls._load_stt_vocabulary(directory)

        pack = cls(
            intents=intents,
            skills=skills,
            queues=queues,
            dids=dids,
            menus=menus,
            challenges=challenges,
            playbooks=playbooks,
            assist_tools=assist_tools,
            assist_groups=assist_groups,
            insurers=insurers,
            handoff_reasons=handoff_reasons,
            comparison_lines=comparison_lines,
            comparison_settings=comparison_settings,
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
            playbooks=len(playbooks),
            assist_tools=len(assist_tools),
            insurers=len(insurers),
        )
        return pack

    @staticmethod
    def _load_assist_tools(
        raw: dict[str, Any],
    ) -> tuple[dict[str, AssistToolSpec], dict[str, AssistToolGroup]]:
        """Load the tool rail (`D120`, `D121`).

        Refuses two shapes at startup rather than letting them surface as a wrong gate:
        a tool in a group that does not exist, and a **non-personal tool declaring
        `prefill`**. The second is the one that matters — prefilling is what turns a form
        that is true for anybody into a statement about one customer, so a tool claiming
        both is claiming something incoherent about its own disclosure.
        """
        groups: dict[str, AssistToolGroup] = {}
        for group_id, entry in (raw.get("groups") or {}).items():
            groups[str(group_id)] = AssistToolGroup(
                group_id=str(group_id),
                label_th=str(entry["label_th"]),
                order=int(entry.get("order", 99)),
            )

        tools: dict[str, AssistToolSpec] = {}
        for tool_id, entry in (raw.get("tools") or {}).items():
            name = str(tool_id)
            try:
                group = str(entry["group"])
                personal = bool(entry["personal"])
                spec = AssistToolSpec(
                    tool_id=name,
                    group=group,
                    label_th=str(entry["label_th"]),
                    kind=str(entry["kind"]),
                    personal=personal,
                    hint_th=str(entry["hint_th"]) if entry.get("hint_th") else None,
                    prefill=tuple(str(f) for f in entry.get("prefill") or ()),
                    fields=tuple(
                        AssistFormField(
                            name=str(f["name"]),
                            label_th=str(f["label_th"]),
                            type=str(f.get("type", "text")),
                            required=bool(f.get("required", False)),
                        )
                        for f in entry.get("fields") or ()
                    ),
                    stub=bool(entry.get("stub", False)),
                )
            except KeyError as exc:
                raise ConfigError(f"assist tool {name!r} is missing {exc}") from exc
            if spec.group not in groups:
                raise ConfigError(f"assist tool {name!r} names unknown group {spec.group!r}")
            if spec.prefill and not spec.personal:
                raise ConfigError(
                    f"assist tool {name!r} declares `prefill` but is not `personal` - "
                    "prefilling a form is what makes it about a specific customer"
                )
            tools[name] = spec

        for group_id in sorted(set(groups) - {t.group for t in tools.values()}):
            # The same both-directions guard `D118` put on playbooks: a group nothing
            # reaches renders as an empty heading on the broker's screen.
            raise ConfigError(f"assist tool group {group_id!r} holds no tools")
        return tools, groups

    @staticmethod
    def _load_playbooks(raw: dict[str, Any]) -> dict[str, PlaybookSpec]:
        out: dict[str, PlaybookSpec] = {}
        for name, steps in (raw.get("playbooks") or {}).items():
            if not isinstance(steps, list) or not steps:
                raise ConfigError(f"playbook {name!r} has no steps")
            parsed: list[PlaybookStep] = []
            for step in steps:
                try:
                    parsed.append(
                        PlaybookStep(
                            text_th=str(step["text_th"]),
                            needs=_ASSURANCE_BY_NAME[str(step.get("needs", "l0")).lower()],
                        )
                    )
                except KeyError as exc:
                    raise ConfigError(f"playbook {name!r}: bad step {step!r} ({exc})") from exc
            out[str(name)] = PlaybookSpec(name=str(name), steps=tuple(parsed))
        if "generic" not in out:
            # The floor every `*.other` and `unknown` falls back to. Without it a missing
            # playbook would render an empty action list, which reads as "nothing to do".
            raise ConfigError("playbooks.yaml must define `generic`")
        return out

    @staticmethod
    def _load_comparison(
        raw: dict[str, Any],
    ) -> tuple[dict[str, ComparisonLine], ComparisonSettings]:
        """`comparison.yaml` — which figures matter, and which way is better (`D126`)."""
        lines: dict[str, ComparisonLine] = {}
        for line_id, body in (raw.get("lines") or {}).items():
            attributes: list[ComparisonAttribute] = []
            for entry in body.get("attributes") or []:
                better = str(entry.get("better", "")).lower()
                if better not in {"higher", "lower"}:
                    # Silent if wrong: every plan still renders, in the wrong order, with a
                    # confident reason attached. So it refuses to boot instead.
                    raise ConfigError(
                        f"comparison.yaml: {line_id}.{entry.get('kind')!r} has "
                        f"better={better!r}; must be 'higher' or 'lower'"
                    )
                weight = float(entry.get("weight", 1.0))
                if weight <= 0:
                    raise ConfigError(
                        f"comparison.yaml: {line_id}.{entry.get('kind')!r} has weight "
                        f"{weight}; an attribute nobody weights is one nobody compares on"
                    )
                attributes.append(
                    ComparisonAttribute(
                        kind=str(entry["kind"]),
                        label_th=str(entry["label_th"]),
                        better=better,
                        weight=weight,
                    )
                )
            if not attributes:
                raise ConfigError(f"comparison.yaml: line {line_id!r} has no attributes")
            kinds = [a.kind for a in attributes]
            if len(kinds) != len(set(kinds)):
                raise ConfigError(f"comparison.yaml: line {line_id!r} names a kind twice")
            lines[str(line_id)] = ComparisonLine(
                label_th=str(body.get("label_th", line_id)), attributes=tuple(attributes)
            )

        if not lines:
            raise ConfigError("comparison.yaml defines no lines")

        settings_raw = raw.get("settings") or {}
        settings = ComparisonSettings(
            max_candidates=int(settings_raw.get("max_candidates", 3)),
            min_material_improvement=float(settings_raw.get("min_material_improvement", 0.05)),
        )
        if settings.max_candidates < 1:
            raise ConfigError("comparison.yaml: max_candidates must be at least 1")
        return lines, settings

    def comparison_for(self, line: ProductLine | str) -> ComparisonLine | None:
        return self.comparison_lines.get(str(line))

    @staticmethod
    def _load_insurers(
        raw: dict[str, Any],
    ) -> tuple[dict[str, InsurerSpec], dict[str, HandoffReasonSpec]]:
        """`insurers.yaml` — who a call may be handed to, and why (`D124`)."""
        insurers: dict[str, InsurerSpec] = {}
        for entry in raw.get("insurers", []):
            spec = InsurerSpec(
                code=str(entry["code"]),
                name_th=str(entry["name_th"]),
                lines=frozenset(str(line) for line in (entry.get("lines") or ())),
            )
            if spec.code in insurers:
                raise ConfigError(f"insurers.yaml: duplicate insurer code {spec.code!r}")
            insurers[spec.code] = spec

        reasons: dict[str, HandoffReasonSpec] = {}
        for entry in raw.get("handoff_reasons", []):
            reason = HandoffReasonSpec(
                code=str(entry["code"]),
                label_th=str(entry["label_th"]),
                requires_policy=bool(entry.get("requires_policy", False)),
            )
            if reason.code in reasons:
                raise ConfigError(f"insurers.yaml: duplicate reason code {reason.code!r}")
            reasons[reason.code] = reason

        if not insurers:
            raise ConfigError("insurers.yaml defines no insurers")
        if not reasons:
            raise ConfigError("insurers.yaml defines no handoff_reasons")
        if not any(not r.requires_policy for r in reasons.values()):
            # Every reason needing a policy would leave a broker with a caller who holds
            # nothing — a complaint about a carrier's service, say — unable to hand over
            # at all. The same shape as `menus.yaml` needing a catch-all in every context
            # (`D122`): a list that can be filtered to empty is a dead end waiting to
            # happen, and it is silent when it happens.
            raise ConfigError(
                "insurers.yaml: every handoff reason requires a policy, so a caller who "
                "holds none could never be handed over"
            )
        return insurers, reasons

    def insurers_for(self, line: str | None = None) -> tuple[InsurerSpec, ...]:
        """The menu, in file order, narrowed to the ones writing this line."""
        return tuple(spec for spec in self.insurers.values() if spec.covers(line))

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
                    handoff_to_insurer=bool(body.get("handoff_to_insurer", False)),
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
                        contexts=_menu_contexts(menu_id, option),
                        label_plan_th=option.get("label_plan_th"),
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
            if spec.playbook not in self.playbooks:
                problems.append(f"intent {code!r} -> unknown playbook {spec.playbook!r}")

        # BOTH DIRECTIONS, the same discipline the prompt ids get: a playbook nothing
        # reaches is dead domain content that somebody will keep editing, and it is the
        # half a one-way check never catches.
        reachable = {spec.playbook for spec in self.intents.values()} | {"generic"}
        for name in sorted(set(self.playbooks) - reachable):
            problems.append(f"playbook {name!r} is defined but no intent uses it")

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

            # And it must survive the FILTER, in every context (`D122`). A menu whose
            # catch-all is narrowed to one surface leaves the other with no way out for a
            # customer whose problem is not on the list — which is exactly the failure
            # `test_every_reason_menu_still_ends_in_a_catch_all` exists to prevent, moved
            # one layer down by making the list depend on where it is rendered.
            if menu_id.endswith("_reason"):
                for context in MENU_CONTEXTS:
                    shown = [o for o in menu.options if o.shown_in(context)]
                    if not shown:
                        problems.append(f"menu {menu_id!r} is empty in context {context!r}")
                    elif not any(
                        o.intent and self.intents[o.intent].is_catch_all
                        for o in shown
                        if o.intent in self.intents
                    ):
                        problems.append(
                            f"menu {menu_id!r} has no catch-all left in context {context!r}"
                        )

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
