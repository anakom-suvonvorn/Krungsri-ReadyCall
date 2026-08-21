"""Language as a hard filter (`D38`).

Thai-only is what ships today, but the model is in place so that adding English later is
a matching-rule change rather than a schema migration.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from readycall.domain.enums import CallState, CefrLevel, EntryChannel, Language
from readycall.domain.models import Agent, AgentLanguage, CallSession


def agent(*languages: AgentLanguage) -> Agent:
    return Agent(
        agent_id="A001",
        display_name="Test",
        team="motor",
        languages=languages or (AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),),
    )


def test_cefr_levels_are_ordered() -> None:
    assert CefrLevel.NATIVE.at_least(CefrLevel.C2)
    assert CefrLevel.B2.at_least(CefrLevel.B1)
    assert not CefrLevel.A1.at_least(CefrLevel.B1)
    assert not CefrLevel.NONE.at_least(CefrLevel.A1)


def test_an_agent_with_no_english_does_not_speak_english() -> None:
    thai_only = agent()
    assert thai_only.speaks(Language.TH)
    assert not thai_only.speaks(Language.EN)
    assert thai_only.level_in(Language.EN) is CefrLevel.NONE


def test_beginner_english_is_not_enough_for_a_real_conversation() -> None:
    """A yes/no "speaks English" flag would route this agent an English claim call.

    Graded proficiency is the whole point: a longer wait is recoverable, a conversation
    neither party can hold is not.
    """
    beginner = agent(
        AgentLanguage(language=Language.TH, level=CefrLevel.NATIVE),
        AgentLanguage(language=Language.EN, level=CefrLevel.A1),
    )
    assert not beginner.speaks(Language.EN)  # default bar is B1
    assert beginner.speaks(Language.EN, at_least=CefrLevel.A1)


def test_a_call_defaults_to_thai_preferred_and_thai_acceptable() -> None:
    session = CallSession(
        call_session_id="call_1",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.CONNECTING,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert session.preferred_language is Language.TH
    assert session.acceptable_languages == (Language.TH,)


def test_preferred_and_acceptable_are_separate_things() -> None:
    """A keypress says what someone PREFERS, not what they can understand. A bilingual
    caller who picks English is still routable to a Thai speaker (`D38`)."""
    session = CallSession(
        call_session_id="call_2",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.CONNECTING,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        preferred_language=Language.EN,
        acceptable_languages=(Language.EN, Language.TH),
    )
    assert session.preferred_language is Language.EN
    assert Language.TH in session.acceptable_languages


@pytest.mark.parametrize(
    ("acceptable", "expected"),
    [((Language.TH,), True), ((Language.EN,), False), ((Language.EN, Language.TH), True)],
)
def test_a_thai_only_agent_is_eligible_only_when_thai_is_acceptable(
    acceptable: tuple[Language, ...], expected: bool
) -> None:
    thai_only = agent()
    eligible = any(thai_only.speaks(lang) for lang in acceptable)
    assert eligible is expected


def test_menu_path_is_recorded_on_the_session() -> None:
    """`D37`: what the caller pressed is the most reliable intent evidence we have."""
    session = CallSession(
        call_session_id="call_3",
        entry_channel=EntryChannel.HOTLINE,
        state=CallState.CONNECTING,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        menu_path=("1", "2"),
        menu_intent_code="motor.roadside_assist",
    )
    assert session.menu_path == ("1", "2")
    assert session.menu_intent_code == "motor.roadside_assist"
