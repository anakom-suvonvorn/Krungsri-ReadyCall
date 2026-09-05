"""Delete recordings whose retention has expired, and erase one call on request.

    uv run python scripts/purge_recordings.py --dry-run
    uv run python scripts/purge_recordings.py
    uv run python scripts/purge_recordings.py --call call_01ABC...     # a PDPA erasure

`D14` promises *"retention per artifact class + an erasure job across both stores and
object storage"*. This is that job for the audio half. It is a **script rather than a
background task in the API**, deliberately: a purge that runs on its own inside the demo
process is one more thing that can go wrong on stage while nobody is watching it, and
deletion is the operation you least want happening unattended. Run it from cron, or by
hand; either way it is a decision somebody made.

**It must be pointed at the same configuration the API runs.** A purge against
`BLOB_STORAGE=memory` deletes nothing and reports success, which is the most misleading
possible outcome — so the store it is about to act on is printed first, every time, and
`--dry-run` exists for the run before the real one.

⚠️ The rows and the objects are both real. There is no undo, and with `D110`'s envelope
encryption there is no recovering an object from a backup of the key ring either.
"""

from __future__ import annotations

import argparse
import asyncio
import sys

from readycall.adapters.blob_storage import build_blob_storage
from readycall.clock import SystemClock
from readycall.config import get_settings
from readycall.console import enable_utf8
from readycall.db.storage import build_storage
from readycall.logging import configure
from readycall.media.gateway import MediaGateway
from readycall.services.recording.service import RecordingService


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="list what would go, delete nothing")
    parser.add_argument(
        "--call", default=None, help="erase EVERY recording for one call, ignoring retention"
    )
    parser.add_argument("--limit", type=int, default=500, help="cap for one run")
    args = parser.parse_args()

    enable_utf8()
    configure()
    settings = get_settings()
    clock = SystemClock()
    storage = build_storage(settings)
    blob = build_blob_storage(settings)
    recorder = RecordingService(
        blob=blob,
        clock=clock,
        settings=settings,
        gateway=MediaGateway(),
        store=storage.recordings,
    )

    print(f"store    : {blob.name}")
    print(f"rows     : {storage.backend}")
    print(f"retention: {settings.recording_retention_days} days")
    if storage.backend == "memory":
        # The one outcome worth refusing rather than reporting: an in-memory store in a
        # fresh process holds nothing, so a purge here is a green tick over an untouched
        # bucket. Better to say so than to be believed.
        print("\nSTORAGE_BACKEND=memory - this process holds no rows, so there is nothing")
        print("to purge and a clean run would mean nothing. Point it at the real backend.")
        return 2

    if args.call:
        if args.dry_run:
            for rec in await recorder.for_call(args.call):
                print(f"would erase {rec.recording_id}  {rec.storage_ref}")
            return 0
        removed = await recorder.erase_call(args.call)
        print(f"\nerased {removed} object(s) for {args.call}")
        return 0

    due = await storage.recordings.due_for_deletion(now=clock.now(), limit=args.limit)
    if not due:
        print("\nnothing is past its retention")
        return 0
    for rec in due:
        age = (clock.now() - rec.created_at).days
        print(
            f"  {rec.recording_id}  {age}d old  due {rec.delete_after:%Y-%m-%d}  {rec.storage_ref}"
        )
    if args.dry_run:
        print(f"\n{len(due)} recording(s) would be deleted. Re-run without --dry-run.")
        return 0

    gone = await recorder.purge_expired(limit=args.limit)
    print(f"\ndeleted {len(gone)} recording(s)")
    if len(gone) == args.limit:
        print("hit the limit - run again for the rest")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
