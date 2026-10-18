"""On-disk size of a repo's ``.repowise/`` directory, for the Stats page."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path


def walk_index_storage_bytes(repowise_dir: Path) -> int:
    """Total on-disk size of a repo's ``.repowise/`` directory."""
    if not repowise_dir.is_dir():
        return 0
    total = 0
    for path in repowise_dir.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


async def index_storage_bytes(local_path: str | None) -> int | None:
    """``walk_index_storage_bytes`` for a repo checkout, off the event loop;
    ``None`` when there is no local index to measure.

    The walk is a full recursive stat of a directory that holds the vectors,
    the wiki and the symbol store, routinely hundreds of megabytes and tens of
    thousands of files, so it runs in a thread rather than blocking the loop.

    Ceiling: the walk still happens once per Stats page load. The real fix is
    to stamp the size when the index is written and read the number back.
    """
    if not local_path:
        return None
    repowise_dir = Path(local_path) / ".repowise"
    if not repowise_dir.is_dir():
        return None
    return await asyncio.to_thread(walk_index_storage_bytes, repowise_dir)
