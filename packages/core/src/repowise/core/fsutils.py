"""Small filesystem helpers shared across core and the CLI."""

from __future__ import annotations

import tempfile
from pathlib import Path


def atomic_write_text(path: Path, content: str, *, newline: str | None = None) -> None:
    """Write *content* to *path* atomically via a temp file + rename.

    Readers never observe a truncated or half-written file: the temp file is
    created in the destination directory (same filesystem, so the rename is
    atomic) and either replaces *path* wholesale or is cleaned up on failure.
    """
    parent = path.parent
    fd, tmp = tempfile.mkstemp(dir=parent, suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8", newline=newline) as f:
            f.write(content)
        Path(tmp).replace(path)
    except Exception:
        Path(tmp).unlink(missing_ok=True)
        raise
