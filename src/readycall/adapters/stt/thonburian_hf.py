"""ThonburianHfEngine — the reference default (`D9`, `D30`), through HF transformers.

This is the model the team already proved on this hardware in their earlier project, so it
is the **known-good baseline the bake-off measures everything else against** — not
necessarily the one that ships. `D30` is explicit that the choice is made on WER, latency
and VRAM measured on the same audio, rather than on which one is familiar.

**It is the memory-hungry option**, and on a card with ~3.2 GiB free (`D95`) that is the
whole tension. fp16 is the default here because fp32 medium does not comfortably fit
alongside anything else; `FasterWhisperEngine` exists because even fp16 may not be enough.

What is deliberately *not* reused from the reference project (`D9`): the batch shape. That
one VAD-ed a whole file, wrote chunks to disk, and ran a pipeline over the directory. Here
the segment arrives already endpointed and in memory, and nothing touches the disk — the
audio is a caller's health information and it does not need a temp file.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from typing import Any

from readycall.errors import ConfigError
from readycall.logging import get_logger
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult

log = get_logger(__name__)

DEFAULT_MODEL = "biodatlab/whisper-th-medium-combined"


def _warmup_tone(seconds: float = 1.0) -> list[float]:
    """A tone, not silence — see `B14`. Whisper hallucinates on zeros, slowly."""
    import math

    return [0.2 * math.sin(2 * math.pi * 220 * i / 16000) for i in range(int(16000 * seconds))]


class ThonburianHfEngine:
    def __init__(
        self,
        *,
        model: str = DEFAULT_MODEL,
        device: str = "cuda",
        dtype: str = "float16",
        language: str = "th",
    ) -> None:
        self._model_name = model
        self._device = device
        self._dtype = dtype
        self._language = language
        self._pipe: Any = None
        #: `prompt_ids` for one vocabulary, cached by its contents (`B19`). Built once
        #: because tokenising the same word list per utterance is pure waste on a path
        #: with a 1.5 s budget.
        self._prompt_cache: dict[tuple[str, ...], Any] = {}
        #: Set when prompt_ids cannot be built on this transformers version, so the
        #: warning is said once and the engine keeps working unhinted.
        self._prompt_unavailable = False
        self._np: Any = None
        self._warned_vocabulary = False

    @property
    def info(self) -> EngineInfo:
        return EngineInfo(
            name="thonburian_hf",
            version=self._dtype,
            device=self._device,
            model=self._model_name,
            extra={"language": self._language},
        )

    async def warmup(self) -> None:
        if self._pipe is not None:  # pragma: no cover - the worker warms once
            return
        try:
            import numpy
            import torch
            from transformers import pipeline
        except ImportError as exc:
            raise ConfigError(
                "ThonburianHfEngine needs the `ml` extra: uv sync --extra ml"
            ) from exc
        self._np = numpy

        def load() -> Any:
            return pipeline(
                task="automatic-speech-recognition",
                model=self._model_name,
                torch_dtype=getattr(torch, self._dtype),
                device=self._device,
            )

        self._pipe = await asyncio.to_thread(load)
        log.info(
            "thonburian loaded",
            model=self._model_name,
            device=self._device,
            dtype=self._dtype,
        )
        await self.transcribe_utterance([AudioFrame(samples=_warmup_tone(), t_start_ms=0)])
        log.info("thonburian warm")

    async def transcribe_batch(
        self,
        utterances: Sequence[Sequence[AudioFrame]],
        *,
        hint: SttHint | None = None,
    ) -> list[SttResult]:
        """Several utterances through the GPU in one pass (`D101`, `BatchSttEngine`).

        This is the mechanism behind the team's earlier Thai project being fast: it passed
        `batch_size=4` to this same pipeline. Whisper encodes a fixed 30-second window per
        clip whatever its length, so four short utterances cost four full windows either
        way — batching overlaps them on the GPU rather than paying for them one after the
        other.

        **The empty-utterance case is handled by position, not by filtering.** Dropping an
        empty one before the call and appending the results back would silently shift every
        later result onto the wrong segment, which is `B20`'s failure — a fluent sentence
        attributed to the wrong moment — reappearing in a new place. Empties get a
        placeholder result so the returned list stays index-for-index with the input.
        """
        if self._pipe is None:
            await self.warmup()
        language = hint.language if hint else self._language
        prompt_ids = self._prompt_ids(hint)

        audios: list[object] = []
        bounds: list[tuple[int, int]] = []
        keep: list[int] = []
        for index, frames in enumerate(utterances):
            samples: list[float] = []
            for frame in frames:
                samples.extend(frame.samples)
            if not samples:
                bounds.append((0, 0))
                continue
            t_start = frames[0].t_start_ms
            bounds.append((t_start, t_start + int(len(samples) / 16000 * 1000)))
            audios.append(self._np.asarray(samples, dtype=self._np.float32))
            keep.append(index)

        texts: dict[int, str] = {}
        if audios:

            def run() -> list[str]:
                kwargs: dict[str, Any] = {"language": language, "task": "transcribe"}
                if prompt_ids is not None:
                    kwargs["prompt_ids"] = prompt_ids
                out = self._pipe(audios, batch_size=len(audios), generate_kwargs=kwargs)
                # The pipeline returns a list when handed a list, and a bare dict when
                # handed one array. Normalised rather than assumed.
                items = out if isinstance(out, list) else [out]
                return [str(item.get("text", "")).strip() for item in items]

            produced = await asyncio.to_thread(run)
            for index, text in zip(keep, produced, strict=False):
                texts[index] = text

        results: list[SttResult] = []
        for index in range(len(utterances)):
            t_start, t_end = bounds[index]
            results.append(
                SttResult(
                    text=texts.get(index, ""),
                    confidence=None if index in texts else 0.0,
                    t_start_ms=t_start,
                    t_end_ms=t_end,
                    engine="thonburian_hf",
                    engine_version=self._model_name,
                    is_final=True,
                )
            )
        return results

    def _prompt_ids(self, hint: SttHint | None) -> Any:
        """Turn the vocabulary hint into Whisper `prompt_ids` (`B19`), or `None`.

        **This is the fix for `B19`**, which was found by measuring: hinted and unhinted
        runs produced CER identical to three decimals on four files, and byte-identical
        output is not what "off-domain vocabulary does not help" looks like — it is what a
        discarded argument looks like. `FasterWhisperEngine` honours the hint through
        `initial_prompt`; the HF pipeline does not take one, and wants `prompt_ids` in
        `generate_kwargs` instead.

        **Two hazards, both already handled elsewhere and worth naming here.** Whisper can
        hand a prompt straight back as if the caller had said it (`B14`) — that is what
        `echoes_the_prompt()` is for, and it becomes *more* load-bearing the moment this
        works. And the terms come from `config/stt_vocabulary.yaml` (`D28`), so adding a
        common word there would make that guard start eating real sentences.

        Failure is non-fatal on purpose: the transformers API for this has moved between
        versions, and an engine that stops transcribing because a *hint* could not be built
        would be a far worse bug than an unhinted transcript.
        """
        if hint is None or not hint.vocabulary or self._prompt_unavailable:
            return None
        key = tuple(hint.vocabulary)
        if key in self._prompt_cache:
            return self._prompt_cache[key]
        try:
            tokenizer = self._pipe.tokenizer
            ids = tokenizer.get_prompt_ids(" ".join(hint.vocabulary), return_tensors="pt")
            ids = ids.to(self._pipe.device)
        except Exception as exc:  # pragma: no cover - depends on the transformers version
            self._prompt_unavailable = True
            log.warning(
                "could not build prompt_ids - continuing UNHINTED (`B19`). Any accuracy "
                "number from this run is an unhinted one",
                engine="thonburian_hf",
                error=f"{type(exc).__name__}: {exc}",
            )
            return None
        if not self._warned_vocabulary:
            self._warned_vocabulary = True
            log.info(
                "vocabulary hint applied via prompt_ids (`B19` fixed)",
                terms=len(hint.vocabulary),
                engine="thonburian_hf",
            )
        self._prompt_cache[key] = ids
        return ids

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        if self._pipe is None:
            await self.warmup()
        samples: list[float] = []
        for frame in frames:
            samples.extend(frame.samples)
        if not samples:
            return SttResult(text="", confidence=0.0, engine="thonburian_hf")

        t_start = frames[0].t_start_ms
        t_end = t_start + int(len(samples) / 16000 * 1000)
        audio = self._np.asarray(samples, dtype=self._np.float32)
        language = hint.language if hint else self._language
        prompt_ids = self._prompt_ids(hint)

        def run() -> str:
            kwargs: dict[str, Any] = {"language": language, "task": "transcribe"}
            if prompt_ids is not None:
                kwargs["prompt_ids"] = prompt_ids
            out = self._pipe(audio, generate_kwargs=kwargs)
            return str(out.get("text", "")).strip()

        text = await asyncio.to_thread(run)
        return SttResult(
            text=text,
            # The HF pipeline does not return a usable per-utterance score, and inventing
            # one would be worse than admitting there is none: `D13` says a number on the
            # agent's screen has to mean something, and this one would not.
            confidence=None,
            t_start_ms=t_start,
            t_end_ms=t_end,
            engine="thonburian_hf",
            engine_version=f"{self._model_name}:{self._dtype}",
            is_final=True,
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        raise NotImplementedError(
            "Whisper is not streaming; use TranscriptionStream with transcribe_utterance"
        )
        yield  # type: ignore[unreachable]  # pragma: no cover - makes this a generator

    async def close(self) -> None:
        """Release the model AND the device memory it is holding (`B22`).

        Dropping the Python reference is not enough: torch keeps freed blocks in its own
        caching allocator, so `mem_get_info()` — which asks the **driver** — still counts
        them as in use. Two consequences, and the second is the one that matters:

        * a bake-off row for the second engine is measured against a baseline that still
          contains the first engine's weights, so its VRAM column is not that engine's
          cost;
        * on a 4 GiB card, loading a second model without releasing the first is most of
          the way to an out-of-memory failure, and `D2` explicitly plans for engines to be
          swappable.

        `empty_cache()` is normally a smell — it fights the allocator that exists to avoid
        re-allocating. Here it is correct, because the point is precisely that this process
        is done with the model and something else needs the card.
        """
        self._pipe = None
        try:
            import gc

            import torch

            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # pragma: no cover - releasing memory must never fail a call
            pass


__all__ = ["DEFAULT_MODEL", "ThonburianHfEngine"]
