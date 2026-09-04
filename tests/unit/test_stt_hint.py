"""The vocabulary hint reaches the model, in both adapters (`B19`).

`B19` shipped because nothing checked that an argument the port advertises was actually
used. The engines were then compared against each other while **disagreeing about whether
they read it**, which contaminated the accuracy half of `D102`'s table.

These tests need no GPU and no model: they substitute a fake pipeline and assert on what it
was handed. That is the level the bug lived at — not "is the transcription good" but "did
the parameter arrive".
"""

from __future__ import annotations

import asyncio
from typing import Any

from readycall.adapters.stt.thonburian_hf import ThonburianHfEngine
from readycall.ports.stt import AudioFrame, SttHint

TERMS = ["ผู้เอาประกัน", "เบี้ยประกัน", "กรมธรรม์"]


class FakePipe:
    """Records every `generate_kwargs` it is called with."""

    def __init__(self, *, tokenizer: Any = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.tokenizer = tokenizer
        self.device = "cpu"

    def __call__(self, audio: Any, **kwargs: Any) -> Any:
        self.calls.append(kwargs.get("generate_kwargs", {}))
        if isinstance(audio, list):
            return [{"text": "ok"} for _ in audio]
        return {"text": "ok"}


class FakeTokenizer:
    def __init__(self, *, explode: bool = False) -> None:
        self.explode = explode
        self.prompts: list[str] = []

    def get_prompt_ids(self, text: str, return_tensors: str = "pt") -> Any:
        if self.explode:
            raise RuntimeError("this transformers version has a different signature")
        self.prompts.append(text)
        return _Ids()


class _Ids:
    def to(self, device: str) -> _Ids:
        return self


class FakeArray:
    """Deliberately NOT a list. The adapter passes one array for a single utterance and a
    list of them for a batch, and the fake pipeline tells those apart by type — so a stub
    that returned a plain list would make every single-utterance call look like a batch."""

    def __init__(self, samples: Any) -> None:
        self.samples = list(samples)


class FakeNp:
    """Stands in for numpy, which CI does not install (the `ml` extra is ~3 GB and the
    runner has no GPU). The adapter only ever asks it to make an array."""

    float32 = "float32"

    @staticmethod
    def asarray(samples: Any, dtype: Any = None) -> Any:
        return FakeArray(samples)


def _engine(pipe: FakePipe) -> ThonburianHfEngine:
    engine = ThonburianHfEngine()
    # Substituting the model is the point: this asserts on what the pipeline was
    # HANDED, which is the level `B19` lived at.
    engine._pipe = pipe
    engine._np = FakeNp()
    return engine


def _frames() -> list[AudioFrame]:
    return [AudioFrame(samples=[0.05] * 16000, t_start_ms=0)]


def test_the_vocabulary_reaches_the_model_as_prompt_ids() -> None:
    """`B19`. This adapter took `hint.vocabulary` and dropped it on the floor, which was
    invisible until a hinted and an unhinted run produced CER identical to three decimals
    on four separate files."""
    tokenizer = FakeTokenizer()
    pipe = FakePipe(tokenizer=tokenizer)
    engine = _engine(pipe)

    async def run() -> None:
        await engine.transcribe_utterance(_frames(), hint=SttHint(language="th", vocabulary=TERMS))

    asyncio.run(run())
    assert pipe.calls, "the model was never called"
    assert "prompt_ids" in pipe.calls[0], "the vocabulary was dropped again"
    assert tokenizer.prompts == [" ".join(TERMS)]


def test_no_vocabulary_means_no_prompt_ids() -> None:
    """A hint with only a language must not smuggle an empty prompt into generation."""
    pipe = FakePipe(tokenizer=FakeTokenizer())
    engine = _engine(pipe)

    async def run() -> None:
        await engine.transcribe_utterance(_frames(), hint=SttHint(language="th"))

    asyncio.run(run())
    assert "prompt_ids" not in pipe.calls[0]


def test_the_batch_path_hints_too() -> None:
    """The batch path is where a forgotten parameter hides best — it is newer, and it is
    only exercised when the model has fallen behind."""
    tokenizer = FakeTokenizer()
    pipe = FakePipe(tokenizer=tokenizer)
    engine = _engine(pipe)

    async def run() -> None:
        await engine.transcribe_batch(
            [_frames(), _frames()], hint=SttHint(language="th", vocabulary=TERMS)
        )

    asyncio.run(run())
    assert "prompt_ids" in pipe.calls[0], "the batch path dropped the vocabulary"


def test_a_tokenizer_that_cannot_build_prompts_does_not_break_transcription() -> None:
    """Deliberate: the transformers API for this has moved between versions, and an engine
    that stopped transcribing because a *hint* could not be built would be a far worse bug
    than an unhinted transcript. It must degrade, and say so once."""
    pipe = FakePipe(tokenizer=FakeTokenizer(explode=True))
    engine = _engine(pipe)

    async def run() -> str:
        result = await engine.transcribe_utterance(
            _frames(), hint=SttHint(language="th", vocabulary=TERMS)
        )
        return result.text

    assert asyncio.run(run()) == "ok"
    assert "prompt_ids" not in pipe.calls[0]


def test_the_prompt_is_built_once_and_reused() -> None:
    """Tokenising the same word list per utterance is waste on a path with a 1.5 s budget."""
    tokenizer = FakeTokenizer()
    pipe = FakePipe(tokenizer=tokenizer)
    engine = _engine(pipe)
    hint = SttHint(language="th", vocabulary=TERMS)

    async def run() -> None:
        for _ in range(4):
            await engine.transcribe_utterance(_frames(), hint=hint)

    asyncio.run(run())
    assert len(pipe.calls) == 4
    assert len(tokenizer.prompts) == 1, "rebuilt the prompt for every utterance"
