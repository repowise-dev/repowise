"""Whether the tables a loop reads grow with the repository, and what that does
to the loop's magnitude.

A loop over rows grows with the data only when the table it reads does. In a
schema keyed by code artefacts (files, commits, graph nodes, symbols), a table
that carries one of those keys, or a foreign key to a table that does, grows
with the repository; one that carries none (registered repositories, decision
records, conversations) stays small however often a loop walks it. A loop that
grows only by reading small tables is bounded, and says which tables
(``loop_small_tables``). Nothing here ever makes a loop grow.

Ceiling: the key vocabulary names code artefacts, so the column rule applies
only to a schema whose tables carry keys of at least two of those families.
Elsewhere, and for a table whose columns the repo does not declare, only a
registry name (:func:`.loop_lexicon.is_config_table`) speaks, and it leaves
the loop unmeasured rather than bounded: a name says what is walked, not how
many rows. Upgrade path: per-domain key vocabularies, or row counts read from
a live database.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from . import loop_lexicon as lex
from .loop_facts import LoopSource, combined_magnitude

if TYPE_CHECKING:
    from collections.abc import Iterable

    from .loop_facts import LoopMagnitude
    from .orm_models import ModelIndex

TableClass = Literal["repo_scale", "small", "registry"]

# Key columns by family: exact names, then suffixes (``source_node_id``).
_KEY_FAMILIES: dict[str, tuple[frozenset[str], tuple[str, ...]]] = {
    "file": (
        frozenset({"file_path", "target_path", "source_path", "document_path"}),
        ("_file_path",),
    ),
    # A change keyed by its head or base commit is one row per change, not per repo.
    "commit": (frozenset({"sha", "commit_sha", "head_sha", "base_sha"}), ("_commit_sha",)),
    "symbol": (frozenset({"node_id", "symbol_id", "symbol"}), ("_node_id", "_symbol_id")),
}
_MIN_FAMILIES = 2
# Columns that may key a row per file, commit or symbol under another spelling:
# a table carrying one is of unknown size, never small.
_MAYBE_KEYS = frozenset(
    {
        "path",
        "file",
        "filename",
        "file_name",
        "rel_path",
        "relative_path",
        "file_id",
        "hash",
        "commit_hash",
        "commit_id",
        "revision",
        "symbol_name",
        "qualified_name",
        "module_path",
    }
)


def _family(column: str) -> str | None:
    name = column.lower()
    for family, (exact, suffixes) in _KEY_FAMILIES.items():
        if name in exact or name.endswith(suffixes):
            return family
    return None


@dataclass(frozen=True, slots=True)
class TableScales:
    """Lower-cased model and table names -> ``repo_scale``, ``small`` or ``None``
    (declared, but its size cannot be told)."""

    scale: dict[str, TableClass | None]

    def classify(self, name: str) -> TableClass | None:
        key = name.rsplit(".", 1)[-1].lower()
        if key in self.scale:
            return self.scale[key]
        return "registry" if lex.is_config_table(name) else None


def _closure(seed: set[str], foreign: dict[str, set[str | None]]) -> set[str]:
    """*seed* and every table whose foreign keys reach one in it."""
    found, changed = set(seed), True
    while changed:
        changed = False
        for name, targets in foreign.items():
            if name not in found and found.intersection(targets):
                found.add(name)
                changed = True
    return found


def table_scales(index: ModelIndex) -> TableScales:
    """Each declared model's class, read once per repo; empty off a code-artefact schema.

    A child of a growing table grows with it. A table with no column read, a
    column that may be a key under another name, or a foreign key to a table
    the repo does not declare is of unknown size, and so is a child of one."""
    keyed = {
        name: {f for column in columns if (f := _family(column))}
        for name, columns in index.columns.items()
    }
    if len(set().union(*keyed.values())) < _MIN_FAMILIES:
        return TableScales({})
    aliases = {name.lower(): name for name in index.columns}
    aliases.update({table.lower(): name for name, table in index.table_names.items()})
    # ``None`` stands for a target the repo does not declare.
    foreign = {n: {aliases.get(t) for t in targets} for n, targets in index.foreign_tables.items()}
    grows = _closure({n for n, families in keyed.items() if families}, foreign)
    unsure = {
        n
        for n, columns in index.columns.items()
        if not columns or {c.lower() for c in columns} & _MAYBE_KEYS or None in foreign.get(n, ())
    }
    unknown = _closure(unsure, foreign) - grows
    return TableScales(
        {
            alias: "repo_scale" if name in grows else None if name in unknown else "small"
            for alias, name in aliases.items()
        }
    )


def registry_source(source: LoopSource) -> LoopSource:
    """A registry-named loop whose growth rests on a read that names no table:
    the name alone proves no growth, so the loop is unmeasured."""
    if (
        source.iterates == "config_table"
        and source.magnitude == "grows_with_data"
        and source.tables == ()
    ):
        return replace(source, magnitude="unknown")
    return source


def small_table_candidate(sources: list[LoopSource]) -> tuple[tuple[str, ...], LoopMagnitude]:
    """``(tables, magnitude if they are small)`` when every growing loop grows only
    by reading named tables; ``((), "unknown")`` otherwise."""
    growing = [s for s in sources if s.magnitude == "grows_with_data"]
    if not growing or not all(s.tables for s in growing):
        return (), "unknown"
    rest = [s for s in sources if s.magnitude != "grows_with_data"]
    tables = tuple(sorted({t for s in growing for t in s.tables or ()}))
    return tables, combined_magnitude([*rest, LoopSource(magnitude="bounded")])


def _verdict(tables: tuple[str, ...], scales: TableScales) -> TableClass | None:
    """``small`` when every table is, ``registry`` when each is small or only
    registry-named, else ``None`` (one grows, or one is unknown)."""
    found = {scales.classify(t) for t in tables}
    if None in found or "repo_scale" in found:
        return None
    return "small" if found == {"small"} else "registry"


def settle_small_table_loops(walked: Iterable[tuple[Any, Any]], index: ModelIndex) -> None:
    """Re-judge, in place, every hit whose loop grows only by reading tables.

    The schema is read from Python models only, so a loop in another language
    is judged by registry names alone: a TS ``prisma.repository`` is not the
    Python ``Repository``."""
    scales, names_only = table_scales(index), TableScales({})
    for pf, fcx in walked:
        if any(h.loop is not None and h.loop.read_tables for h in fcx.perf_hits):
            judge = scales if pf.file_info.language == "python" else names_only
            fcx.perf_hits = [_settled(hit, judge) for hit in fcx.perf_hits]


def _settled(hit: Any, scales: TableScales) -> Any:
    loop = hit.loop
    verdict = _verdict(loop.read_tables, scales) if loop is not None and loop.read_tables else None
    if verdict is None:
        return hit
    if verdict == "registry":
        return replace(hit, loop=replace(loop, magnitude="unknown"))
    small = replace(loop, magnitude=loop.small_magnitude, small_tables=loop.read_tables)
    return replace(hit, loop=small)


__all__ = [
    "TableClass",
    "TableScales",
    "registry_source",
    "settle_small_table_loops",
    "small_table_candidate",
    "table_scales",
]
