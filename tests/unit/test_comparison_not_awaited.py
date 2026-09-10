"""The comparison's reason sentence is written in the background, never awaited (`D148`).

`D137` awaited it, and every switch of the line selector froze the plan panel for the
whole model round-trip (~2.4 s). These tests use a model that **does not answer until the
test says so**, which is the only way to tell "answered at once" from "answered after a
fast model finished" — the endpoint tests run on the rule-based model, which answers
instantly, and so could never have caught the freeze.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from readycall.api.routers import agent


class _HeldModel:
    """A reason writer that waits for `release` before answering, and counts its calls."""

    def __init__(self, answer: dict[str, str] | None = None, *, fail: bool = False) -> None:
        self.release = asyncio.Event()
        self.calls = 0
        self._answer = answer
        self._fail = fail

    async def write(self, _result: Any) -> dict[str, str] | None:
        self.calls += 1
        await self.release.wait()
        if self._fail:
            raise RuntimeError("provider fell over")
        return self._answer


def _container(model: _HeldModel, timeout_s: float = 3.0) -> Any:
    return SimpleNamespace(
        settings=SimpleNamespace(llm_comparison_timeout_s=timeout_s),
        comparison_reason_cache={},
        comparison_reason_inflight=set(),
        comparison_reason_attempts={},
        comparison_reasons=model,
    )


_HEALTH = SimpleNamespace(line="health")


async def _started() -> None:
    """One turn of the loop, so a task that was only SCHEDULED has actually begun."""
    await asyncio.sleep(0)


async def _drain() -> None:
    """Let every background task this module started run to completion."""
    while agent._BACKGROUND:
        await asyncio.gather(*list(agent._BACKGROUND), return_exceptions=True)


async def test_the_first_answer_does_not_wait_for_the_model() -> None:
    """The freeze itself. The model is held, and the helper still answers — with nothing
    from the model and `pending`, so the panel renders its generated sentences now."""
    model = _HeldModel({"P1": "ครอบคลุมค่าห้องสูงกว่า"})
    box = _container(model)

    sentences, pending = agent._comparison_reasons(box, "call_1", _HEALTH)

    assert (sentences, pending) == ({}, True)
    await _started()
    assert model.calls == 1  # running, and still not answered
    model.release.set()
    await _drain()


async def test_the_models_sentences_arrive_on_a_later_fetch() -> None:
    model = _HeldModel({"P1": "ครอบคลุมค่าห้องสูงกว่า"})
    box = _container(model)
    agent._comparison_reasons(box, "call_1", _HEALTH)

    model.release.set()
    await _drain()

    assert agent._comparison_reasons(box, "call_1", _HEALTH) == (
        {"P1": "ครอบคลุมค่าห้องสูงกว่า"},
        False,
    )


async def test_flipping_back_to_a_line_still_being_written_starts_nothing_new() -> None:
    """A broker flipping health → motor → health must not pay for health twice."""
    model = _HeldModel({"P1": "x"})
    box = _container(model)

    agent._comparison_reasons(box, "call_1", _HEALTH)
    await _started()
    again = agent._comparison_reasons(box, "call_1", _HEALTH)
    await _started()

    assert again == ({}, True)
    assert model.calls == 1
    model.release.set()
    await _drain()


async def test_a_model_that_throws_does_not_leave_the_line_pending_forever() -> None:
    """Without the `finally`, the in-flight flag would stay set, `pending` would stay true,
    and the client would poll this line for the rest of the call for text never coming."""
    model = _HeldModel(fail=True)
    box = _container(model)
    agent._comparison_reasons(box, "call_1", _HEALTH)

    model.release.set()
    await _drain()

    assert box.comparison_reason_inflight == set()
    # And the next fetch is allowed to try again rather than being stuck.
    _, pending = agent._comparison_reasons(box, "call_1", _HEALTH)
    await _started()
    assert pending is True
    assert model.calls == 2
    model.release.set()
    await _drain()


async def test_a_call_that_never_completed_is_not_cached() -> None:
    """`D137`'s rule survives: `None` is transient (a timeout), so it must not be stored as
    this line's answer for the rest of the call."""
    model = _HeldModel(None)
    box = _container(model)
    agent._comparison_reasons(box, "call_1", _HEALTH)

    model.release.set()
    await _drain()

    assert box.comparison_reason_cache == {}
    assert box.comparison_reason_inflight == set()


async def test_a_model_that_refused_every_sentence_is_cached_and_not_asked_again() -> None:
    """`{}` is deterministic — the model answered and the guards refused it all — so asking
    again buys nothing, and `pending` must go false or the client polls forever."""
    model = _HeldModel({})
    box = _container(model)
    agent._comparison_reasons(box, "call_1", _HEALTH)
    model.release.set()
    await _drain()

    assert agent._comparison_reasons(box, "call_1", _HEALTH) == ({}, False)
    assert model.calls == 1


async def test_a_zero_timeout_turns_the_model_off_for_this_panel() -> None:
    model = _HeldModel({"P1": "x"})
    box = _container(model, timeout_s=0)

    assert agent._comparison_reasons(box, "call_1", _HEALTH) == ({}, False)
    assert model.calls == 0


async def test_a_provider_that_keeps_failing_is_not_asked_on_every_poll() -> None:
    """The client polls every 1.5 s while `pending`. Two tries — the cold start's retry —
    then the generated sentences stand and `pending` goes false, or a provider that keeps
    timing out gets a model call every few seconds for as long as the panel is open."""
    model = _HeldModel(None)
    model.release.set()
    box = _container(model)

    for _ in range(2):
        assert agent._comparison_reasons(box, "call_1", _HEALTH) == ({}, True)
        await _drain()

    for _ in range(5):  # five more polls of an open panel
        assert agent._comparison_reasons(box, "call_1", _HEALTH) == ({}, False)
    assert model.calls == 2
