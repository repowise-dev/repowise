"""The stored origins a surface holds out, and the one scope every hotspot count uses.

Import-light on purpose: the portable folds (module health, area rollups) count
hotspots too, and must not pull in the analyzer, the parser or the database to
do it. :mod:`.finding_registry` re-exports the held sets and
:func:`stored_origin` from here.
"""

from __future__ import annotations

from typing import Any

__all__ = [
    "HELD_ORIGINS",
    "HOTSPOT_HELD_ORIGINS",
    "MAP_HELD_ORIGINS",
    "counts_as_hotspot",
    "stored_origin",
]

#: The stored ``code_origin`` values a health finding is never served on.
HELD_ORIGINS: tuple[str, ...] = ("vendored", "generated")

#: The stored ``code_origin`` values the area map leaves out until the reader
#: brings them back: tests, docs examples, build files and data folders' code,
#: plus everything no health finding is served on. Prose docs have no health
#: row, so never appear.
MAP_HELD_ORIGINS: tuple[str, ...] = ("test", "docs_example", "build", "data", *HELD_ORIGINS)

#: The origins the Code Health hotspot list holds out until the reader brings
#: one back. Every hotspot count a page quotes uses this scope, so the number on
#: a card is the number of rows its link opens.
HOTSPOT_HELD_ORIGINS: frozenset[str] = frozenset(MAP_HELD_ORIGINS)


def stored_origin(code_origin: Any, is_test: Any) -> str:
    """A row's origin, reading a row stamped before ``code_origin`` off ``is_test``."""
    if isinstance(code_origin, str) and code_origin:
        return code_origin
    return "test" if is_test is True else "production"


def counts_as_hotspot(
    is_hotspot: Any,
    code_origin: Any,
    is_test: Any,
    held: frozenset[str] = HOTSPOT_HELD_ORIGINS,
) -> bool:
    """Whether a file is one of the hotspots the list shows in *held*'s scope.

    The in-memory form of ``services.hotspots.count_hotspots``: a flagged file
    counts unless its stored origin is held. A file with no health row reads
    ``production``, as the list's outer join does.
    """
    return bool(is_hotspot) and stored_origin(code_origin, is_test) not in held
