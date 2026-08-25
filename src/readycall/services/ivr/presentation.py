"""Turning a menu into the words a particular caller hears, in their order.

`D37` reads a recognised caller's likely options **first**, and `menus.yaml` says the
promoted options take the low numbers. Both halves of that have consequences that only
appear once you build it:

**A menu cannot be one clip** (`D80`). The order differs per caller, so the audio is a
lead-in plus one rendered option line each, composed here.

**The key the caller presses is not the key in the config** (`D81`). If health is promoted
to `1`, then `1` means health *for this call only*. So a presentation is not just a list of
lines — it is also the mapping back, and every keypress is resolved through it immediately.
`CallSession.menu_path` therefore always holds **canonical** keys, and means the same thing
on every call, whether or not anything was promoted.

The alternative — reading the numbers out of order ("กด 3 … กด 1 … กด 2") — was rejected:
it is worse than not personalising at all, and it makes the caller do the sorting.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from readycall.domainpack import MenuOption, MenuSettings, MenuSpec
from readycall.voiceprompts import PromptPack, PromptRole, SpokenLine


@dataclass(frozen=True, slots=True)
class PresentedOption:
    """One option as this caller hears it."""

    spoken_key: str
    option: MenuOption
    promoted: bool = False

    @property
    def renumbered(self) -> bool:
        return self.spoken_key != self.option.key


@dataclass(frozen=True, slots=True)
class MenuPresentation:
    menu_id: str
    options: tuple[PresentedOption, ...]
    lines: tuple[SpokenLine, ...]

    def resolve(self, pressed: str) -> MenuOption | None:
        """Spoken key -> the option it actually means. `None` if nothing matched."""
        for presented in self.options:
            if presented.spoken_key == pressed:
                return presented.option
        return None

    def canonical_key(self, pressed: str) -> str | None:
        option = self.resolve(pressed)
        return option.key if option else None

    @property
    def reordered(self) -> bool:
        return any(presented.renumbered for presented in self.options)

    @property
    def promoted_keys(self) -> tuple[str, ...]:
        return tuple(p.option.key for p in self.options if p.promoted)


def present(
    menu: MenuSpec,
    *,
    prompts: PromptPack,
    settings: MenuSettings,
    promoted: Sequence[str] = (),
    renumber: bool = True,
) -> MenuPresentation:
    """Compose one menu for one caller.

    `promoted` is canonical keys, most likely first. Anything not named keeps its
    original relative order, so the menu a caller heard last time is still mostly the
    menu they hear this time — predictability matters more than a perfect ranking.
    """
    by_key = {option.key: option for option in menu.options}
    ordered: list[MenuOption] = []
    promoted_set: set[str] = set()
    for key in promoted:
        option = by_key.get(key)
        if option is not None and key not in promoted_set:
            ordered.append(option)
            promoted_set.add(key)
    ordered += [option for option in menu.options if option.key not in promoted_set]

    presented: list[PresentedOption] = []
    for index, option in enumerate(ordered, start=1):
        # Renumbering only happens when something actually moved. With nothing promoted
        # the spoken key is the canonical key, which keeps the common case identical to
        # the config and keeps the built prompt pack a complete one.
        spoken = str(index) if (renumber and promoted_set) else option.key
        presented.append(
            PresentedOption(spoken_key=spoken, option=option, promoted=option.key in promoted_set)
        )

    lines = [prompts.render(menu.prompt)]
    lines += [
        prompts.say(PromptRole.MENU_OPTION, key=p.spoken_key, label=p.option.label_th)
        for p in presented
    ]
    lines.append(
        prompts.say(
            PromptRole.MENU_RESERVED_HINT,
            repeat_key=settings.repeat_key,
            operator_key=settings.operator_key,
        )
    )

    return MenuPresentation(menu_id=menu.menu_id, options=tuple(presented), lines=tuple(lines))


__all__ = ["MenuPresentation", "PresentedOption", "present"]
