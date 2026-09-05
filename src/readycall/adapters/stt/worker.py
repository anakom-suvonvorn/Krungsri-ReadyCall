"""`SubprocessSttEngine` — the decode timeout `D98` said could not be built yet (`D112`).

`D98` added a third guard on the *shape* of what came back, and then said plainly what it
did not do: it is a detector, not a preventer. By the time the character rate is known the
eight seconds `B14` measured have already been spent. The preventer is a deadline, and a
deadline is only real if something can be killed when it expires —

> `asyncio.wait_for` around `to_thread` does not kill the thread, so a runaway decode has
> to be killed with the process.

So this adapter puts the engine in a process. It implements `SttEngine`, wraps any other
engine by configuration, and is selected with `STT_WORKER=subprocess`. Everything above it
is unchanged: `TranscriptionStream` cannot tell, which is the ports-and-adapters point
(`D3`) and also the reason this could be added after the fact at all.

**What happens on a timeout, exactly.** The child is killed, a `TransientError` is raised,
and `TranscriptionStream._consume` catches it the way it catches any engine failure — so
the utterance is lost, counted, and since `D111` the *reason the brief is thin* reaches the
agent's screen. The next utterance respawns the worker and pays the model load once. That
is the trade the timeout makes: one lost sentence and a reload, against a decode that would
otherwise hold the single consumer for as long as it liked while every later utterance
queued behind it (`B20`'s compounding backlog, from the other end).

**One request at a time, on purpose.** `TranscriptionStream` has a single consumer by
design, and a worker that interleaved requests could not be killed on a deadline without
taking somebody else's utterance with it.

**No `transcribe_batch`.** `BatchSttEngine` is a capability (`D101`) and this deliberately
does not offer it, so the stream falls back to a plain loop. That costs nothing measurable:
`D101` measured batching as a **no-op on this GPU** and shipped it anyway for the case
where it would not be. Adding it here would mean a partial batch dying on one utterance's
deadline, which is a worse trade than the one it buys.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator, Sequence
from typing import Any

from readycall.adapters.stt.wire import encode, read_frame
from readycall.errors import TransientError
from readycall.logging import get_logger
from readycall.ports.stt import AudioFrame, EngineInfo, SttHint, SttResult

log = get_logger(__name__)

#: How long to wait for the child to load a model and say `ready`. Typhoon takes seconds
#: on a warm cache and much longer on a cold one, so this is generous by design — it is a
#: startup bound, and nothing is waiting on a call while it runs.
DEFAULT_STARTUP_TIMEOUT_S = 180.0


class SubprocessSttEngine:
    """Runs another engine in a child process, with a deadline that can be enforced."""

    def __init__(
        self,
        *,
        timeout_s: float,
        env: dict[str, str] | None = None,
        startup_timeout_s: float = DEFAULT_STARTUP_TIMEOUT_S,
        command: Sequence[str] | None = None,
    ) -> None:
        self._timeout_s = timeout_s
        self._env = {**env, "READYCALL_STT_WORKER_CHILD": "1"} if env is not None else None
        self._startup_timeout_s = startup_timeout_s
        self._command = list(command or (sys.executable, "-m", "readycall.entrypoints.stt"))
        self._proc: asyncio.subprocess.Process | None = None
        self._reader: asyncio.StreamReader | None = None
        self._info = EngineInfo(name="subprocess", version="0", device="unknown", model="unknown")
        #: Serialises requests AND the respawn, so two utterances arriving either side of
        #: a timeout cannot start two workers.
        self._lock = asyncio.Lock()
        self._seq = 0
        self.kills = 0

    @property
    def info(self) -> EngineInfo:
        return self._info

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    # --- lifecycle ----------------------------------------------------------------------

    async def warmup(self) -> None:
        """Start the child and wait for it to load its model.

        Called at open time so the first real utterance is not the slow one — the same
        reason `SttEngine.warmup` exists at all.
        """
        async with self._lock:
            await self._ensure()

    async def close(self) -> None:
        async with self._lock:
            await self._stop()

    async def _ensure(self) -> None:
        if self.running:
            return
        proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            env=self._env,
        )
        self._proc = proc
        assert proc.stdout is not None
        self._reader = proc.stdout
        try:
            hello = await asyncio.wait_for(read_frame(self._reader), self._startup_timeout_s)
        except (TimeoutError, asyncio.IncompleteReadError, ValueError) as exc:
            await self._stop()
            raise TransientError(f"the stt worker did not start: {exc}") from exc
        if hello is None or not hello.header.get("ready"):
            await self._stop()
            raise TransientError("the stt worker did not report ready")
        self._info = EngineInfo(
            name=f"subprocess({hello.header.get('engine', '?')})",
            version=str(hello.header.get("version", "?")),
            device=str(hello.header.get("device", "?")),
            model=str(hello.header.get("model", "?")),
        )
        log.info("stt worker started", engine=self._info.name, pid=proc.pid)

    async def _stop(self) -> None:
        proc, self._proc, self._reader = self._proc, None, None
        if proc is None or proc.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), 5.0)

    # --- the actual work ----------------------------------------------------------------

    async def transcribe_utterance(
        self,
        frames: Sequence[AudioFrame],
        *,
        hint: SttHint | None = None,
    ) -> SttResult:
        samples: list[float] = []
        for frame in frames:
            samples.extend(frame.samples)
        t_start = frames[0].t_start_ms if frames else 0
        rate = frames[0].sample_rate if frames else 16000

        async with self._lock:
            await self._ensure()
            proc, reader = self._proc, self._reader
            assert proc is not None and proc.stdin is not None and reader is not None
            self._seq += 1
            header: dict[str, Any] = {
                "id": self._seq,
                "op": "transcribe",
                "t_start_ms": t_start,
                "sample_rate": rate,
                "hint": (
                    {
                        "language": hint.language,
                        "vocabulary": list(hint.vocabulary),
                        "product_line": hint.product_line,
                    }
                    if hint
                    else None
                ),
            }

            async def _exchange() -> Any:
                assert proc is not None and proc.stdin is not None and reader is not None
                proc.stdin.write(encode(header, samples))
                # **`drain()` is INSIDE the deadline, and the reason is soundness rather
                # than an observed failure.** A wedged worker stops reading as well as
                # answering, and whether that blocks the parent depends on the OS pipe
                # buffer, the utterance length and how much the child managed to consume
                # before it wedged — none of which this code controls. It was seen to
                # hang once, during development, on an utterance around 80 KB; it does
                # not reproduce reliably, which is the argument, not a counter-argument.
                # A deadline that covers half an exchange is not a deadline, and the half
                # it leaves out is the half whose behaviour is platform-dependent.
                await proc.stdin.drain()
                return await read_frame(reader)

            try:
                reply = await asyncio.wait_for(_exchange(), self._timeout_s)
            except (BrokenPipeError, ConnectionResetError) as exc:
                await self._stop()
                raise TransientError(f"the stt worker went away: {exc}") from exc
            except TimeoutError:
                # **The entire reason this class exists.** Nothing else in the system can
                # stop a decode that has decided to take a minute; killing the process
                # can, and `D12` says the call must not wait for it either way.
                self.kills += 1
                log.warning(
                    "stt decode exceeded its deadline - killing the worker",
                    timeout_s=self._timeout_s,
                    audio_ms=round(len(samples) / rate * 1000.0),
                    kills=self.kills,
                )
                await self._stop()
                raise TransientError(f"decode exceeded {self._timeout_s}s and was killed") from None
            except (asyncio.IncompleteReadError, ValueError) as exc:
                await self._stop()
                raise TransientError(f"the stt worker died mid-utterance: {exc}") from exc

        if reply is None:
            await self.close()
            raise TransientError("the stt worker closed its output")
        if not reply.header.get("ok"):
            raise TransientError(str(reply.header.get("error", "unknown worker error")))
        return SttResult(
            text=str(reply.header.get("text", "")),
            confidence=reply.header.get("confidence"),
            t_start_ms=int(reply.header.get("t_start_ms", t_start)),
            t_end_ms=int(reply.header.get("t_end_ms", 0)),
            engine=str(reply.header.get("engine", self._info.name)),
            engine_version=str(reply.header.get("engine_version", self._info.version)),
            is_final=bool(reply.header.get("is_final", True)),
        )

    async def stream(self, frames: AsyncIterator[AudioFrame]) -> AsyncIterator[SttResult]:
        """Continuous mode. Not used: endpointing happens above the port (`D96`).

        Present because the protocol has it, and it is one utterance per frame rather
        than a partial-results implementation, because a worker that streamed partials
        could not be killed on a per-utterance deadline — which is the only thing this
        class exists for.
        """
        async for frame in frames:
            result = await self.transcribe_utterance([frame])
            if result.text:
                yield result


__all__ = ["DEFAULT_STARTUP_TIMEOUT_S", "SubprocessSttEngine"]
