"""The frame format the STT worker process speaks (`D112`).

Deliberately tiny and deliberately **not** JSON all the way down: an utterance is tens of
thousands of float samples, and encoding those as a JSON array costs more than the
inference does. So every message is a JSON *header* plus an optional raw float32 payload:

    RCW1 | u32 header_len | header (JSON, utf-8) | payload (float32 little-endian)

`array('f')` does the payload in the standard library, which matters for the same reason
`media/audio.py` is pure Python: the worker has to be startable on a box with no `ml`
extra, running the scripted engine, or the stage-safe path stops being stage-safe.

**stdout carries frames and nothing else.** `readycall.logging` already writes to stderr
(a happy accident this design depends on), and the child additionally rebinds `sys.stdout`
to stderr at startup — a `print` inside a model library would otherwise corrupt the stream
in a way that looks like a protocol bug and is not.
"""

from __future__ import annotations

import asyncio
import json
import struct
from array import array
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

MAGIC = b"RCW1"
_LEN = struct.Struct("!I")
#: Refuse anything absurd rather than allocating it. 10 minutes of 16 kHz float32 is
#: 38 MB, which is already far past `INTAKE_MAX_DURATION_S`.
MAX_PAYLOAD_BYTES = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class Frame:
    header: dict[str, Any]
    samples: Sequence[float] = ()


def encode(header: dict[str, Any], samples: Sequence[float] = ()) -> bytes:
    payload = array("f", samples).tobytes()
    head = json.dumps({**header, "n_samples": len(samples)}, ensure_ascii=False).encode("utf-8")
    return MAGIC + _LEN.pack(len(head)) + head + _LEN.pack(len(payload)) + payload


async def read_frame(reader: asyncio.StreamReader) -> Frame | None:
    """One frame, or None at clean EOF.

    Every length is validated before it is used to read, because a desynchronised stream
    otherwise waits forever on a length it invented — a hang that reads as a slow model.
    """
    magic = await reader.readexactly(len(MAGIC)) if not reader.at_eof() else b""
    if not magic:
        return None
    if magic != MAGIC:
        raise ValueError(f"bad frame magic: {magic!r}")
    (head_len,) = _LEN.unpack(await reader.readexactly(_LEN.size))
    if head_len > MAX_PAYLOAD_BYTES:
        raise ValueError(f"header too large: {head_len}")
    header = json.loads((await reader.readexactly(head_len)).decode("utf-8"))
    (payload_len,) = _LEN.unpack(await reader.readexactly(_LEN.size))
    if payload_len > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload too large: {payload_len}")
    samples = array("f")
    if payload_len:
        samples.frombytes(await reader.readexactly(payload_len))
    return Frame(header=header, samples=samples)


def read_frame_sync(stream: Any) -> Frame | None:
    """The same frame, from a BLOCKING binary stream. What the child process uses.

    ⚠️ **Not a convenience — the async version does not work for the child on Windows.**
    `loop.connect_read_pipe(..., sys.stdin)` raises `OSError: [WinError 6] The handle is
    invalid` under the Proactor loop, which is the default on Windows since 3.8. Caught by
    the round-trip test rather than by review, and it would have shipped a worker that
    worked on CI and failed on the demo laptop.

    The child reads this in a thread (`asyncio.to_thread`), which is correct anyway: it
    answers one utterance at a time, so there is nothing for its event loop to do while it
    waits for the next request.
    """

    def _exact(n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = stream.read(n - len(buf))
            if not chunk:
                raise EOFError("stream closed mid-frame")
            buf += chunk
        return buf

    first = stream.read(len(MAGIC))
    if not first:
        return None
    if len(first) < len(MAGIC):
        first += _exact(len(MAGIC) - len(first))
    if first != MAGIC:
        raise ValueError(f"bad frame magic: {first!r}")
    (head_len,) = _LEN.unpack(_exact(_LEN.size))
    if head_len > MAX_PAYLOAD_BYTES:
        raise ValueError(f"header too large: {head_len}")
    header = json.loads(_exact(head_len).decode("utf-8"))
    (payload_len,) = _LEN.unpack(_exact(_LEN.size))
    if payload_len > MAX_PAYLOAD_BYTES:
        raise ValueError(f"payload too large: {payload_len}")
    samples = array("f")
    if payload_len:
        samples.frombytes(_exact(payload_len))
    return Frame(header=header, samples=samples)


__all__ = [
    "MAGIC",
    "MAX_PAYLOAD_BYTES",
    "Frame",
    "encode",
    "read_frame",
    "read_frame_sync",
]
