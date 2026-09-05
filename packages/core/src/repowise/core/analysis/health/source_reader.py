"""How the health pass obtains file bytes.

Routing every source read through a :class:`SourceReader` lets the pass analyse
content that is not the working tree -- the base side of a diff, a historical
commit -- without checking anything out. The default reads the working tree.

Keyed by ``abs_path``: the key every read site already holds.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class SourceReader(Protocol):
    """Return the bytes of one file, or ``None`` when it cannot be read."""

    def __call__(self, abs_path: str) -> bytes | None: ...


def disk_source_reader(abs_path: str) -> bytes | None:
    """Read *abs_path* from the filesystem, degrading to ``None``."""
    try:
        return Path(abs_path).read_bytes()
    except OSError:
        return None


class MappingSourceReader:
    """Serve bytes from an in-memory map, with no filesystem fallback.

    The absent fallback is the point: filling a gap from the working tree would
    report its findings as the revision's. A missing path reads as unreadable,
    which the health pass already degrades on and the caller can count.
    """

    __slots__ = ("_sources", "misses")

    def __init__(self, sources: dict[str, bytes]) -> None:
        self._sources = sources
        #: Paths asked for and not held, so coverage is reported, not inferred.
        self.misses: set[str] = set()

    def __call__(self, abs_path: str) -> bytes | None:
        source = self._sources.get(abs_path)
        if source is None:
            self.misses.add(abs_path)
        return source
