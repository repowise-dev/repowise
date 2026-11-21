"""The symbol enclosing a served line, named once per run of rows in it."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, NamedTuple

from sqlalchemy import select

from repowise.core.analysis.test_reach.db import stored_origins
from repowise.core.code_origin import path_origin
from repowise.core.ingestion.symbol_identity import (
    SYMBOL_SEP,
    id_segment_name,
    local_symbol_id,
)
from repowise.core.persistence.models import WikiSymbol
from repowise.core.symbol_spans import symbol_at

# Symbols a line can sit inside. A variable, constant or property spans its own
# statement only, so naming it would just restate the line.
_ENCLOSING_KINDS = frozenset(
    {"function", "method", "constructor", "class", "interface", "struct", "enum", "trait", "impl"}
)
# The ``in`` of a module-level row that follows rows inside a symbol in its file.
MODULE_LEVEL = "<module>"


class _Span(NamedTuple):
    start_line: int
    end_line: int
    name: str


def display_name(symbol_id: str) -> str:
    """``Owner.member`` from ``path::Owner::member``, overload and variant
    discriminators left out (``C::f#1(int)`` names ``C.f``)."""
    return ".".join(id_segment_name(s) for s in local_symbol_id(symbol_id).split(SYMBOL_SEP))


async def enclosing_ranges(
    session: Any, repo_id: str, paths: Iterable[str]
) -> dict[str, list[_Span]]:
    """Indexed symbol spans per production path that a line can sit inside.

    Test, generated, vendored and other files get none, so their rows keep
    their plain shape: an impact question asks for the shipped callers.
    """
    asked = sorted(set(paths))
    stored = await stored_origins(session, repo_id, asked) if asked else {}
    wanted = [p for p in asked if (stored.get(p) or path_origin(p)) == "production"]
    if not wanted:
        return {}
    res = await session.execute(
        select(WikiSymbol.file_path, WikiSymbol.start_line, WikiSymbol.end_line, WikiSymbol.symbol_id)
        .where(
            WikiSymbol.repository_id == repo_id,
            WikiSymbol.file_path.in_(wanted),
            WikiSymbol.kind.in_(_ENCLOSING_KINDS),
        )
    )
    ranges: dict[str, list[_Span]] = {}
    for path, start, end, symbol_id in res.all():
        if start and end and SYMBOL_SEP in symbol_id:
            ranges.setdefault(path, []).append(_Span(start, end, display_name(symbol_id)))
    return ranges


def enclosing_at(ranges: dict[str, list[_Span]], path: str, line: int) -> str | None:
    """The innermost indexed symbol holding *line* of *path*, else ``None``."""
    span = symbol_at(ranges.get(path, ()), line)
    return span.name if span else None


def mark_enclosing(rows: list[dict[str, Any]], ranges: dict[Any, dict[str, list[_Span]]]) -> None:
    """Add ``in`` to each non-definition row, once per run of consecutive rows
    in one file and one symbol: a row without it is in the symbol the row above
    in the same file names. A module-level row after such a run says
    ``<module>``; one opening a file's run says nothing. *ranges* is keyed by
    each row's ``repo`` (``None`` for a single-repo reply)."""
    last: tuple[Any, str | None] = (None, None)
    for row in rows:
        if row.get("kind") == "definition":
            # A definition row ends the run: the next row names its own symbol.
            last = (None, None)
            continue
        name = enclosing_at(ranges.get(row.get("repo"), {}), row["path"], row["line"])
        where = (row.get("repo"), row["path"])
        if where != last[0]:
            if name:
                row["in"] = name
        elif name != last[1]:
            row["in"] = name or MODULE_LEVEL
        last = (where, name)
