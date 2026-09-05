"""The decode timeout, and the process it needs to be real (`D112`, `D98`).

`D98` designed this guard and then refused to build it, in one of the more useful
sentences in `DECISIONS.md`:

> `asyncio.wait_for` around `to_thread` does not kill the thread, so a runaway decode has
> to be killed with the process ... half-implementing it now would be a guard that looks
> like one and is not — which is `B7`'s entire family.

So the assertion this suite exists to make is not "a `TimeoutError` was raised". It is
**the child process is gone**. A test that only checked the exception would pass just as
happily on the fake guard `D98` refused.

Real subprocesses, no mocks. They run the scripted engine or a purpose-built stub, so
nothing here needs a GPU, the `ml` extra, or a model download.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from readycall.adapters.stt.worker import SubprocessSttEngine
from readycall.errors import TransientError
from readycall.media.audio import AudioFormat, Encoding
from readycall.ports.stt import AudioFrame, SttHint

REPO = Path(__file__).resolve().parents[2]
#: **Deliberately bigger than an OS pipe buffer** (~64 KB): 40,000 float32 samples is
#: 160 KB, or 2.5 seconds of 16 kHz audio — an entirely ordinary Thai sentence.
#:
#: The first version used 8,000 samples (32 KB), which fits in a pipe buffer — so every
#: send completed immediately and nothing exercised the case where a wedged worker stops
#: READING. That case was seen to hang once during development and does not reproduce
#: reliably, because whether it blocks depends on the OS buffer and on how much the child
#: consumed before it wedged. The send is inside the deadline for that reason, and the
#: payload is realistic here so the path is at least exercised.
UTTERANCE = [AudioFrame(samples=[0.1] * 40000, t_start_ms=0)]


def _child_env(**extra: str) -> dict[str, str]:
    """The child needs the package importable and the config directory findable."""
    env = {**os.environ, "PYTHONPATH": str(REPO / "src"), **extra}
    env.pop("STT_WORKER", None)
    return env


# --- a worker that answers ----------------------------------------------------------------


async def test_a_real_child_process_transcribes_and_comes_back() -> None:
    """The whole round trip: spawn, load, warm, send float samples, get Thai back.

    On the scripted engine, so this is a protocol test rather than a model test — which
    is the point, because the protocol is the part that can break silently.
    """
    engine = SubprocessSttEngine(timeout_s=60.0, env=_child_env())
    try:
        await engine.warmup()
        assert engine.running

        result = await engine.transcribe_utterance(UTTERANCE, hint=SttHint(language="th"))

        assert isinstance(result.text, str)
        assert "scripted" in engine.info.name
    finally:
        await engine.close()
    assert not engine.running


# --- a worker that hangs ------------------------------------------------------------------


HANGING_WORKER = """
import sys, time
from readycall.adapters.stt.wire import encode, MAGIC
out = sys.stdout.buffer
out.write(encode({"id": 0, "ok": True, "ready": True, "engine": "hanging",
                  "version": "0", "model": "none", "device": "cpu"}))
out.flush()
# Read one request and then never answer it. This is a decode that has decided to take
# a minute - the exact thing `asyncio.wait_for` around a thread could not stop.
sys.stdin.buffer.read(1)
time.sleep(600)
"""


async def test_a_runaway_decode_is_KILLED_not_merely_timed_out(tmp_path: Path) -> None:
    """The assertion `D98` was waiting for.

    Note what is checked: the process is **gone**. A guard that raised on a deadline and
    left the decode running would pass an exception test and would still hold the GPU,
    still burn the power, and still be there for the next utterance.
    """
    script = tmp_path / "hanging_worker.py"
    script.write_text(HANGING_WORKER, encoding="utf-8")
    engine = SubprocessSttEngine(
        timeout_s=1.0,
        env=_child_env(),
        command=[sys.executable, str(script)],
    )
    await engine.warmup()
    pid = engine._proc.pid if engine._proc else None
    assert pid is not None

    with pytest.raises(TransientError, match="killed"):
        await engine.transcribe_utterance(UTTERANCE)

    assert engine.kills == 1
    assert not engine.running, "the runaway decode must be GONE, not merely given up on"
    assert not _alive(pid), f"process {pid} survived its deadline"


def _alive(pid: int) -> bool:
    """Whether a process id is still running, without a dependency on psutil."""
    if sys.platform == "win32":
        import subprocess

        out = subprocess.run(
            ["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True, check=False
        ).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


async def test_the_worker_comes_back_for_the_next_utterance(tmp_path: Path) -> None:
    """A killed worker is not a dead engine — the next call pays a reload and works.

    This is the other half of the trade `D112` makes, and it is what stops the timeout
    turning one bad utterance into a call with no transcript at all.
    """
    script = tmp_path / "hanging_worker.py"
    script.write_text(HANGING_WORKER, encoding="utf-8")
    engine = SubprocessSttEngine(
        timeout_s=1.0, env=_child_env(), command=[sys.executable, str(script)]
    )
    try:
        with pytest.raises(TransientError):
            await engine.transcribe_utterance(UTTERANCE)
        first = engine.kills

        with pytest.raises(TransientError):
            await engine.transcribe_utterance(UTTERANCE)

        assert engine.kills == first + 1, (
            "the second utterance got its own worker and its own deadline"
        )
    finally:
        await engine.close()


# --- a worker that dies -------------------------------------------------------------------


DYING_WORKER = """
import sys
from readycall.adapters.stt.wire import encode
out = sys.stdout.buffer
out.write(encode({"id": 0, "ok": True, "ready": True, "engine": "dying",
                  "version": "0", "model": "none", "device": "cpu"}))
out.flush()
sys.exit(1)
"""


async def test_a_worker_that_dies_is_a_transient_error_not_a_hang(tmp_path: Path) -> None:
    """`D12`: the call is never blocked on any of this. A dead worker must fail fast and
    be reported, not leave the single consumer waiting on a pipe that will never answer."""
    script = tmp_path / "dying_worker.py"
    script.write_text(DYING_WORKER, encoding="utf-8")
    engine = SubprocessSttEngine(
        timeout_s=30.0, env=_child_env(), command=[sys.executable, str(script)]
    )
    try:
        with pytest.raises(TransientError):
            await engine.transcribe_utterance(UTTERANCE)
    finally:
        await engine.close()


SILENT_WORKER = """
import time
time.sleep(600)
"""


async def test_a_worker_that_never_says_ready_does_not_hang_forever(tmp_path: Path) -> None:
    """The startup bound. A model that fails to load must not look like a slow one."""
    script = tmp_path / "silent_worker.py"
    script.write_text(SILENT_WORKER, encoding="utf-8")
    engine = SubprocessSttEngine(
        timeout_s=30.0,
        startup_timeout_s=1.0,
        env=_child_env(),
        command=[sys.executable, str(script)],
    )
    with pytest.raises(TransientError, match="did not start"):
        await engine.warmup()
    assert not engine.running


# --- and the two halves of the same session, composed ---------------------------------------


async def test_a_killed_decode_becomes_a_reported_loss(tmp_path: Path) -> None:
    """`D112` and `D111` meet here, and neither is much use without the other.

    The timeout makes a runaway decode stop; the loss reporting makes the agent's screen
    say *why* their transcript is empty. Before either, a hung model produced a blank
    panel indistinguishable from a caller who said nothing.
    """
    from readycall.adapters.vad.energy import EnergyVad
    from readycall.clock import ManualClock
    from readycall.config import Settings
    from readycall.services.transcription.service import TranscriptionService

    from .test_transcription_service import LossCountingIntake, silent_packet, speech_packet

    script = tmp_path / "hanging_worker.py"
    script.write_text(HANGING_WORKER, encoding="utf-8")
    engine = SubprocessSttEngine(
        timeout_s=1.0, env=_child_env(), command=[sys.executable, str(script)]
    )
    intake = LossCountingIntake()
    service = TranscriptionService(
        intake=intake,  # type: ignore[arg-type]
        vad_factory=EnergyVad,
        stt=engine,
        clock=ManualClock(),
        settings=Settings(),
    )
    try:
        await service.open("call_hang", fmt=AudioFormat(encoding=Encoding.PCM16, sample_rate=16000))
        for _ in range(60):
            await service.push("call_hang", speech_packet())
        for _ in range(40):
            await service.push("call_hang", silent_packet())
        await service.close("call_hang")
    finally:
        await engine.close()

    assert engine.kills >= 1, "the decode must actually have been killed"
    assert intake.turns == []
    assert intake.lost >= 1, "and the loss must reach the intake, which is what the screen reads"
