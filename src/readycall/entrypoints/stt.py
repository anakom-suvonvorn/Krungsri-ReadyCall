"""The STT worker process — the one `D2` planned and `D98` needed (`D112`).

    uv run python -m readycall.entrypoints.stt

Not run by hand in normal use: `SubprocessSttEngine` spawns it. It exists as its own
entrypoint anyway because that is what makes it *killable*, which is the whole point.
`D98` measured a third guard and then said plainly that the **preventer** — a decode
timeout — could not be built, because `asyncio.wait_for` around `to_thread` does not kill
the thread. A runaway decode has to be killed with the process, so there has to be a
process.

It loads the engine **once** (`ports/stt.py`: "a 5-20 second model load can never sit in
the call path"), warms it, says `ready`, and then answers one utterance at a time. One at
a time is not a limitation to fix later: the transcriber has a single consumer by design
(`B20`), and a worker that interleaved requests could not be killed on a deadline without
taking somebody else's utterance with it.

**stdout is the protocol and nothing else.** `sys.stdout` is rebound to stderr before the
engine is built, because model libraries print, and a stray `print` inside a transformers
import would corrupt the stream in a way that looks like a protocol bug.
"""

from __future__ import annotations

import asyncio
import sys
from typing import Any

from readycall.adapters.stt.wire import Frame, encode, read_frame_sync
from readycall.config import get_settings
from readycall.console import enable_utf8
from readycall.logging import configure, get_logger
from readycall.ports.stt import AudioFrame, SttEngine, SttHint

log = get_logger(__name__)


def _stdio() -> tuple[Any, Any]:
    """The raw stdin and stdout buffers — captured before stdout is rebound.

    ⚠️ **Blocking streams, read in a thread.** The first version used
    `loop.connect_read_pipe(..., sys.stdin)` and died on Windows with
    `OSError: [WinError 6] The handle is invalid` under the Proactor loop — a worker that
    would have passed CI and failed on the demo laptop. Blocking reads are also simply
    right here: this process answers one utterance at a time, so its event loop has
    nothing else to do while it waits.
    """
    stdin, out = sys.stdin.buffer, sys.stdout.buffer
    # Anything the engine prints from here on goes to stderr, where the parent lets it
    # through to the console and nothing parses it.
    sys.stdout = sys.stderr
    return stdin, out


def _write(out: Any, header: dict[str, Any]) -> None:
    out.write(encode(header))
    out.flush()


async def _answer(engine: SttEngine, frame: Frame) -> dict[str, Any]:
    header = frame.header
    hint_raw = header.get("hint")
    hint = (
        SttHint(
            language=hint_raw.get("language", "th"),
            vocabulary=tuple(hint_raw.get("vocabulary", ())),
            product_line=hint_raw.get("product_line"),
        )
        if hint_raw
        else None
    )
    audio = AudioFrame(
        samples=list(frame.samples),
        t_start_ms=int(header.get("t_start_ms", 0)),
        sample_rate=int(header.get("sample_rate", 16000)),
    )
    result = await engine.transcribe_utterance([audio], hint=hint)
    return {
        "id": header["id"],
        "ok": True,
        "text": result.text,
        "confidence": result.confidence,
        "t_start_ms": result.t_start_ms,
        "t_end_ms": result.t_end_ms,
        "engine": result.engine,
        "engine_version": result.engine_version,
        "is_final": result.is_final,
    }


async def serve() -> int:
    enable_utf8()
    settings = get_settings()
    configure(log_format=settings.log_format)
    stdin, out = _stdio()

    # Imported here rather than at module scope: `api/deps.py` pulls in FastAPI, and the
    # STT box does not install the web stack (`D2`). The factory itself is what this
    # worker exists to run, so it is worth the awkward import.
    from readycall.api.deps import build_stt

    engine = build_stt(settings)
    await engine.warmup()
    log.info("stt worker ready", engine=engine.info.name, model=engine.info.model)
    _write(
        out,
        {
            "id": 0,
            "ok": True,
            "ready": True,
            "engine": engine.info.name,
            "version": engine.info.version,
            "model": engine.info.model,
            "device": engine.info.device,
        },
    )

    while True:
        try:
            frame = await asyncio.to_thread(read_frame_sync, stdin)
        except (EOFError, ValueError, OSError):
            # The parent went away, or the stream desynchronised. Either way this process
            # has nothing left to answer and exiting is the honest response.
            break
        if frame is None or frame.header.get("op") == "close":
            break
        try:
            _write(out, await _answer(engine, frame))
        except Exception as exc:
            log.exception("transcription failed in the worker")
            _write(out, {"id": frame.header.get("id"), "ok": False, "error": str(exc)})

    await engine.close()
    log.info("stt worker stopped")
    return 0


def main() -> None:
    sys.exit(asyncio.run(serve()))


if __name__ == "__main__":
    main()
