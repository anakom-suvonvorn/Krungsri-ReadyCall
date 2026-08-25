"""Every line the caller hears must actually exist.

`menus.yaml` and `dids.yaml` name prompt ids. `voice_prompts.yaml` defines them. Nothing
connects the two but a string, and the failure mode is silent in the worst possible place:
the config loads, the tests pass, the call is routed correctly, and the caller hears a gap
where a menu should be.

This is the same guard `D72` put on `challenges.yaml`, for the same reason — a list
enforced on one side and read from the other drifts unless something checks. It runs in
both directions, because an orphan prompt is also information: it means either a line
nobody plays, or a reference somebody deleted.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from readycall.domainpack import DomainPack
from readycall.errors import ConfigError
from readycall.voiceprompts import PromptPack, PromptRole, PromptSpec, clip_key
from tests.conftest import REPO_ROOT

CONFIG = REPO_ROOT / "config"


@pytest.fixture(scope="module")
def pack() -> DomainPack:
    return DomainPack.load(CONFIG)


@pytest.fixture(scope="module")
def prompts() -> PromptPack:
    return PromptPack.load(CONFIG / "voice_prompts.yaml")


class TestTheReferencesResolve:
    def test_every_referenced_prompt_id_is_defined(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """The headline guard. Everything else in this file supports it."""
        missing = sorted(
            prompt_id
            for prompt_id in prompts.referenced_ids(pack)
            if prompt_id not in prompts.prompts
        )
        assert not missing, f"referenced but never defined: {missing}"

    def test_nothing_is_defined_that_nothing_plays(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """An orphan is a symptom, not a tidiness problem.

        Either a line was written and never wired up, or a reference was deleted and the
        line left behind. Both are worth knowing; neither is worth discovering on stage.
        """
        orphans = sorted(set(prompts.prompts) - prompts.referenced_ids(pack))
        assert not orphans, f"defined but unreachable: {orphans}"

    def test_the_cross_check_runs_as_a_startup_gate_too(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """The same check the app runs at startup, so this test cannot pass while the
        server refuses to boot."""
        prompts.validate_against(pack)

    def test_every_menu_names_a_prompt_that_exists(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        for menu_id, menu in pack.menus.items():
            assert menu.prompt in prompts.prompts, f"menu {menu_id} -> {menu.prompt}"

    def test_every_published_number_has_a_greeting(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """`D19`: the number someone dialled is the first thing we know about them."""
        for number, did in pack.dids.items():
            assert did.greeting_prompt in prompts.prompts, f"{number} -> {did.greeting_prompt}"

    def test_the_language_menu_prompt_exists_even_though_it_is_off(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """`D38`: modelled now, enabled later. A prompt that only becomes reachable the
        day someone flips a flag is the one nobody notices is missing."""
        assert pack.language_menu is not None
        assert pack.language_menu.enabled is False
        assert pack.language_menu.prompt in prompts.prompts


class TestRoles:
    def test_every_role_is_mapped(self, prompts: PromptPack) -> None:
        """`services/` asks for a role, never for a prompt id (`D28`). An unmapped role
        is a hole in the flow that only shows up when a caller reaches it."""
        unmapped = sorted(role.value for role in PromptRole if role not in prompts.flow)
        assert not unmapped, f"roles with no prompt: {unmapped}"

    def test_every_mapped_role_points_at_a_real_prompt(self, prompts: PromptPack) -> None:
        for role, prompt_id in prompts.flow.items():
            assert prompt_id in prompts.prompts, f"role {role.value} -> {prompt_id}"

    def test_a_typo_in_a_role_name_fails_loudly(self, tmp_path: Path) -> None:
        """The dangerous alternative is an unread key: the flow silently keeps its old
        prompt while the YAML looks like it was changed."""
        body = "\n".join(
            [
                "defaults: {voice: v}",
                "flow: {intake_offr: p}",
                'prompts: {p: {text_th: "สวัสดี"}}',
            ]
        )
        path = tmp_path / "voice_prompts.yaml"
        path.write_text(body, encoding="utf-8")
        with pytest.raises(ConfigError, match="unknown flow role"):
            PromptPack.load(path)


class TestTheTextItself:
    def test_every_prompt_has_thai_in_it(self, prompts: PromptPack) -> None:
        """This is a Thai product. An English placeholder that reaches the caller is worse
        than a missing prompt, because nothing fails."""
        for prompt_id, spec in prompts.prompts.items():
            has_thai = any("฀" <= ch <= "๿" for ch in spec.text_th)
            assert has_thai, f"{prompt_id} has no Thai text: {spec.text_th!r}"

    def test_slots_are_declared_exactly(self, prompts: PromptPack) -> None:
        """Enforced at load; asserted here so the rule is visible where it is read."""
        prompts.validate()

    def test_an_undeclared_slot_is_rejected(self) -> None:
        broken = PromptPack(
            prompts={
                "x": PromptSpec(prompt_id="x", text_th="ลำดับที่ {position}", voice="v"),
            },
            flow=dict.fromkeys(PromptRole, "x"),
            default_voice="v",
            language="th",
            source=CONFIG / "voice_prompts.yaml",
        )
        with pytest.raises(ConfigError, match="does not declare it"):
            broken.validate()

    def test_a_declared_but_unused_slot_is_rejected(self) -> None:
        """A slot that is declared and never used means the wording changed and the
        declaration did not — the same drift, pointing the other way."""
        broken = PromptPack(
            prompts={"x": PromptSpec(prompt_id="x", text_th="สวัสดี", voice="v", slots=("a",))},
            flow=dict.fromkeys(PromptRole, "x"),
            default_voice="v",
            language="th",
            source=CONFIG / "voice_prompts.yaml",
        )
        with pytest.raises(ConfigError, match="never uses it"):
            broken.validate()


class TestRendering:
    def test_a_dynamic_prompt_fills_its_slots(self, prompts: PromptPack) -> None:
        line = prompts.say(PromptRole.QUEUE_POSITION, position=3, wait_minutes=2)
        assert "3" in line.text
        assert "{" not in line.text
        assert line.is_dynamic

    def test_a_missing_slot_raises_rather_than_reaching_the_caller(
        self, prompts: PromptPack
    ) -> None:
        """The alternative is the caller hearing the word "position" in English."""
        with pytest.raises(ConfigError, match="missing"):
            prompts.say(PromptRole.QUEUE_POSITION, position=3)

    def test_an_unexpected_slot_raises_too(self, prompts: PromptPack) -> None:
        """Means the call site and the wording have drifted apart."""
        with pytest.raises(ConfigError, match="unexpected"):
            prompts.say(PromptRole.QUEUE_HOLD, position=3)

    def test_the_reserved_hint_speaks_the_keys_the_ivr_actually_honours(
        self, prompts: PromptPack, pack: DomainPack
    ) -> None:
        """`D37`: `9` repeats and `0` reaches a human, in every menu. The spoken hint is
        rendered from the same settings the IVR reads, so it cannot promise a key that
        does nothing."""
        settings = pack.menu_settings
        line = prompts.say(
            PromptRole.MENU_RESERVED_HINT,
            repeat_key=settings.repeat_key,
            operator_key=settings.operator_key,
        )
        assert settings.repeat_key in line.text
        assert settings.operator_key in line.text

    def test_every_menu_option_can_be_spoken(self, prompts: PromptPack, pack: DomainPack) -> None:
        """`D80`: a menu is a lead-in plus one rendered line per option. If the template
        cannot render an option, that option is silent and unreachable."""
        for menu_id, menu in pack.menus.items():
            for option in menu.options:
                line = prompts.say(PromptRole.MENU_OPTION, key=option.key, label=option.label_th)
                assert option.key in line.text, menu_id
                assert option.label_th in line.text, menu_id


class TestTheClipCache:
    def test_the_key_is_stable_across_processes(self, prompts: PromptPack) -> None:
        """The whole build cache depends on this. `hash()` would not do — Python
        randomises it per process, so every build would re-render everything."""
        assert clip_key("สวัสดี", "v1", "null") == clip_key("สวัสดี", "v1", "null")
        assert clip_key("สวัสดี", "v1", "null") == "be42317611d0abfdbe1d3bfd8bf8cdc3"

    @pytest.mark.parametrize(
        ("text", "voice", "engine"),
        [("อื่น", "v1", "null"), ("สวัสดี", "v2", "null"), ("สวัสดี", "v1", "azure")],
    )
    def test_changing_any_input_changes_the_key(self, text: str, voice: str, engine: str) -> None:
        """Editing a Thai line must produce a different clip, or the re-render is a no-op
        and the caller keeps hearing the old wording (`D24`)."""
        assert clip_key(text, voice, engine) != clip_key("สวัสดี", "v1", "null")

    def test_the_same_rendered_text_is_the_same_clip(self, prompts: PromptPack) -> None:
        """Why dynamic lines are cacheable at all: everyone third in the queue hears the
        same clip, so the pack warms up in minutes rather than never."""
        first = prompts.say(PromptRole.QUEUE_POSITION, position=3, wait_minutes=2)
        second = prompts.say(PromptRole.QUEUE_POSITION, position=3, wait_minutes=2)
        assert first.clip_key("null") == second.clip_key("null")

        different = prompts.say(PromptRole.QUEUE_POSITION, position=4, wait_minutes=2)
        assert different.clip_key("null") != first.clip_key("null")
