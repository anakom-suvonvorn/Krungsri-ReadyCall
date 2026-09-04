"""The domain pack must stay internally consistent.

`intents.yaml`, `skills.yaml`, `menus.yaml` and `dids.yaml` refer to each other by string.
Nothing stops someone renaming a skill in one file and leaving a dangling reference in
another — except these tests, which are the cheapest possible substitute for a compiler.

They also pin the *rules* the config has to obey (`D22`, `D37`), not just its syntax.
"""

from __future__ import annotations

from typing import Any

import pytest
import yaml

from readycall.domain.enums import ProductLine, Urgency
from tests.conftest import REPO_ROOT

CONFIG = REPO_ROOT / "config"


def _load(name: str) -> dict[str, Any]:
    data = yaml.safe_load((CONFIG / name).read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"{name} must be a mapping"
    return data


@pytest.fixture(scope="module")
def intents() -> dict[str, Any]:
    return _load("intents.yaml")["intents"]


@pytest.fixture(scope="module")
def skills_cfg() -> dict[str, Any]:
    return _load("skills.yaml")


@pytest.fixture(scope="module")
def menus_cfg() -> dict[str, Any]:
    return _load("menus.yaml")


@pytest.fixture(scope="module")
def dids() -> dict[str, Any]:
    return _load("dids.yaml")["dids"]


class TestIntents:
    def test_every_intent_names_a_real_skill(
        self, intents: dict[str, Any], skills_cfg: dict[str, Any]
    ) -> None:
        known = set(skills_cfg["skills"])
        for code, intent in intents.items():
            assert intent["skill"] in known, f"{code} points at unknown skill {intent['skill']}"

    def test_every_intent_has_a_thai_label(self, intents: dict[str, Any]) -> None:
        """This is a Thai product; an intent with no Thai label cannot be spoken or shown."""
        for code, intent in intents.items():
            assert intent.get("label_th"), f"{code} has no label_th"

    def test_urgencies_are_valid(self, intents: dict[str, Any]) -> None:
        valid = {u.value for u in Urgency}
        for code, intent in intents.items():
            assert intent["default_urgency"] in valid, f"{code} has a bad urgency"

    def test_lines_are_valid(self, intents: dict[str, Any]) -> None:
        valid = {line.value for line in ProductLine}
        for code, intent in intents.items():
            if "line" in intent:
                assert intent["line"] in valid, f"{code} has a bad line"

    @pytest.mark.parametrize("line", ["motor", "health", "travel", "life"])
    def test_every_product_line_has_a_catch_all(self, intents: dict[str, Any], line: str) -> None:
        """A caller whose reason is not on our list is a caller, not an error.

        Without `<line>.other`, an unexpected reason falls through to the fully generic
        `unknown` and the agent loses the product context we already had.
        """
        catch_alls = [
            code
            for code, intent in intents.items()
            if intent.get("is_catch_all") and intent.get("line") == line
        ]
        assert catch_alls, f"product line {line} has no catch-all intent"

    def test_the_two_kinds_of_unknown_both_exist(self, intents: dict[str, Any]) -> None:
        """`<line>.other` = we know the line; `unknown` = we do not. Different routing."""
        assert intents["general.other"]["is_catch_all"] is True
        assert intents["unknown"]["is_catch_all"] is True
        assert "line" not in intents["unknown"]

    def test_catch_alls_are_never_high_urgency(self, intents: dict[str, Any]) -> None:
        """We do not know what it is, so we must not claim it is an emergency."""
        for code, intent in intents.items():
            if intent.get("is_catch_all"):
                assert intent["default_urgency"] in {"low", "normal"}, code


class TestSkillsAndQueues:
    def test_every_skill_names_a_real_queue(self, skills_cfg: dict[str, Any]) -> None:
        queues = set(skills_cfg["queues"])
        for code, skill in skills_cfg["skills"].items():
            assert skill["queue"] in queues, f"skill {code} points at unknown queue"

    def test_every_queue_requires_a_real_skill(self, skills_cfg: dict[str, Any]) -> None:
        known = set(skills_cfg["skills"])
        for queue_id, queue in skills_cfg["queues"].items():
            assert queue["required_skill"] in known, f"queue {queue_id} needs unknown skill"

    def test_overflow_queues_exist_and_do_not_loop(self, skills_cfg: dict[str, Any]) -> None:
        queues = skills_cfg["queues"]
        for queue_id, queue in queues.items():
            overflow = queue.get("overflow_queue")
            if overflow is None:
                continue
            assert overflow in queues, f"{queue_id} overflows to unknown {overflow}"
            assert overflow != queue_id, f"{queue_id} overflows to itself"
            # Walk the chain: an overflow cycle would hang a call forever.
            seen, current = {queue_id}, overflow
            while current is not None:
                assert current not in seen, f"overflow cycle through {current}"
                seen.add(current)
                current = queues[current].get("overflow_queue")

    def test_urgent_queues_have_tighter_slas(self, skills_cfg: dict[str, Any]) -> None:
        """Someone at a crash site should not have the same SLA as a renewal question."""
        queues = skills_cfg["queues"]
        assert queues["q_motor_claim"]["sla_seconds"] < queues["q_general"]["sla_seconds"]


class TestMenus:
    def test_every_menu_option_maps_to_something_real(
        self, menus_cfg: dict[str, Any], intents: dict[str, Any]
    ) -> None:
        menus = menus_cfg["menus"]
        for menu_id, menu in menus.items():
            for option in menu["options"]:
                if "intent" in option:
                    assert option["intent"] in intents, (
                        f"{menu_id} key {option['key']} -> unknown intent {option['intent']}"
                    )
                if "next" in option and option["next"] is not None:
                    assert option["next"] in menus, f"{menu_id} -> unknown menu {option['next']}"

    def test_menu_keys_are_unique_within_a_menu(self, menus_cfg: dict[str, Any]) -> None:
        for menu_id, menu in menus_cfg["menus"].items():
            keys = [o["key"] for o in menu["options"]]
            assert len(keys) == len(set(keys)), f"{menu_id} has duplicate keys"

    def test_reserved_keys_are_not_reused_as_options(self, menus_cfg: dict[str, Any]) -> None:
        """Repeat must mean the same thing in every menu. It is now the ONLY reserved key
        (`D86`) — the way out of a menu is its own spoken catch-all option."""
        reserved = {menus_cfg["settings"]["repeat_key"]}
        for menu_id, menu in menus_cfg["menus"].items():
            keys = {o["key"] for o in menu["options"]}
            assert not (keys & reserved), f"{menu_id} reuses a reserved key"

    def test_every_reason_menu_offers_a_way_out(
        self, menus_cfg: dict[str, Any], intents: dict[str, Any]
    ) -> None:
        """`D37`: a menu with no "something else" option traps the caller."""
        for menu_id, menu in menus_cfg["menus"].items():
            if not menu_id.endswith("_reason"):
                continue
            has_catch_all = any(
                intents.get(o.get("intent", ""), {}).get("is_catch_all") for o in menu["options"]
            )
            assert has_catch_all, f"{menu_id} has no catch-all option"

    def test_menus_are_short_enough_to_listen_to(self, menus_cfg: dict[str, Any]) -> None:
        """Past about seven spoken options people stop listening and press 0."""
        for menu_id, menu in menus_cfg["menus"].items():
            assert len(menu["options"]) <= 7, f"{menu_id} has too many options"

    def test_language_menu_is_defined_but_off(self, menus_cfg: dict[str, Any]) -> None:
        """`D38`: modelled now, Thai-only in behaviour until it is actually built."""
        assert menus_cfg["language_menu"]["enabled"] is False
        assert menus_cfg["language_menu"]["default"] == "th"


class TestDids:
    def test_every_did_points_at_a_real_queue_and_line(
        self, dids: dict[str, Any], skills_cfg: dict[str, Any]
    ) -> None:
        queues = set(skills_cfg["queues"])
        lines = {line.value for line in ProductLine}
        for number, did in dids.items():
            assert did["default_queue"] in queues, f"{number} -> unknown queue"
            assert did["product_line"] in lines, f"{number} -> unknown product line"

    def test_a_did_that_skips_the_menu_must_actually_know_the_line(
        self, dids: dict[str, Any]
    ) -> None:
        """Skipping the product question while not knowing the product is how a caller
        ends up silently in the wrong queue."""
        for number, did in dids.items():
            if did.get("skip_product_menu"):
                assert did["product_line"] != "unknown", (
                    f"{number} skips the menu but has no product line"
                )

    def test_assumed_intents_are_real(self, dids: dict[str, Any], intents: dict[str, Any]) -> None:
        for number, did in dids.items():
            if "assumed_intent" in did:
                assert did["assumed_intent"] in intents, f"{number} assumes unknown intent"

    def test_the_general_hotline_asks_rather_than_guesses(self, dids: dict[str, Any]) -> None:
        """`D37`: the common real-world case is one hotline for everything. It must not
        skip the menu, because the menu is the only thing that knows why they called."""
        general = dids["+6621234000"]
        assert general["product_line"] == "unknown"
        assert general["skip_product_menu"] is False


# ---------------------------------------------------------------------------------------
# `B23`: STT_ENGINE=thonburian_ct2 could never have worked on its own.
# ---------------------------------------------------------------------------------------


def test_the_ct2_engine_takes_the_adapters_local_default_when_unset() -> None:
    """`B23`. `stt_model` used to default to the Hugging Face id, so selecting the CT2
    engine handed faster-whisper a transformers checkpoint it cannot read. Blank is the
    only default correct for BOTH Thonburian engines, because they want different things
    from this one field."""
    from readycall.config import Settings

    assert Settings().stt_model == ""


def test_an_hf_id_with_the_ct2_engine_is_refused_at_startup() -> None:
    """It fails at model load otherwise — on the box with the GPU, which is the worst
    place and the worst moment to discover it (`B17` is the same family)."""
    import pytest

    from readycall.config import Settings
    from readycall.errors import ConfigError

    with pytest.raises(ConfigError, match="CTranslate2 directory"):
        Settings(stt_engine="thonburian_ct2", stt_model="biodatlab/whisper-th-medium-combined")


def test_a_real_directory_with_the_ct2_engine_is_accepted(tmp_path: object) -> None:
    """The check must not refuse a legitimate local path that happens to contain a slash."""
    from pathlib import Path

    from readycall.config import Settings

    d = Path(str(tmp_path)) / "models" / "whisper-th-medium-combined-ct2"
    d.mkdir(parents=True)
    assert Settings(stt_engine="thonburian_ct2", stt_model=str(d)).stt_model == str(d)


def test_the_hf_engine_is_not_affected() -> None:
    """`thonburian_hf` genuinely wants a Hugging Face id, so the check is engine-specific."""
    from readycall.config import Settings

    s = Settings(stt_engine="thonburian_hf", stt_model="biodatlab/whisper-th-medium-combined")
    assert s.stt_model == "biodatlab/whisper-th-medium-combined"
