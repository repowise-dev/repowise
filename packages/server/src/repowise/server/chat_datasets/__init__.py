"""Chart datasets for chat: whole tables read from the local index, one row per thing.

The chat-only ``chart`` tool (:mod:`repowise.server.chat_chart`) charts a
dataset in one call, so the model never has to find a list inside a tool's
JSON or copy an artifact id. Each dataset declares its fields (type, one-line
meaning, the aggregations that are valid on it) and a loader that reads the
index with the same queries the MCP tools and REST services use. Every row is
kept: the chart transform filters and aggregates server-side.

Rows are cached per repository and index state (``index_state_key``), so a
conversation's second chart over the same dataset costs no query. Datasets are
registered on the ``chat-dataset`` seam, so a plugin adds one without editing
this package. No dataset holds a person, a risk score or an attribution
figure: the view guards would refuse them anyway, and a dataset that never
loads them cannot leak them into a filter.
"""

from __future__ import annotations

import asyncio
import importlib
from collections import OrderedDict
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.registry.plugins import SeamRegistry, load_plugins
from repowise.server.chat_spec import ARROW

#: Aggregations a numeric field takes; the first is the one used when rows must combine.
SUMS = ("sum", "avg", "min", "max")
AVERAGES = ("avg", "min", "max")
#: Text fields are grouped and counted, never added up.
TEXT = ()

_CACHE_LIMIT = 32


@dataclass(frozen=True)
class DatasetField:
    """One column of a dataset: its type, what it means, and how it combines."""

    type: str  # "text", "number", "time" or "bool"
    meaning: str
    aggregates: tuple[str, ...] = TEXT
    #: A numeric field whose ``avg`` is weighted by this field (coverage by coverable lines).
    weight: str | None = None
    #: A date field's calendar step ("week"): a grouped chart fills empty steps with zero.
    step: str | None = None
    #: The least weight one row carries, as the field's own surface floors it.
    weight_floor: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class LoadContext:
    """What a loader reads with: one session, the repository row and its root."""

    session: Any
    repository: Any
    repo_path: str
    #: Rows of another dataset, from the same cache (``files`` maps paths to packages).
    rows: Callable[[str], Awaitable[list[dict[str, Any]]]]
    #: The repository's workspace alias; ``None`` (or ``default``) outside a workspace.
    alias: str | None = None


@dataclass(frozen=True)
class Dataset:
    name: str
    description: str
    fields: Mapping[str, DatasetField]
    load: Callable[[LoadContext], Awaitable[list[dict[str, Any]]]]
    #: ``(kind, source)`` of the values, as a view's basis.
    basis: tuple[str, str]
    #: A spec that charts this dataset, offered when a spec cannot be repaired.
    example: Mapping[str, Any]
    #: Text fields from coarse to fine; a treemap nests by the ones above its x.
    hierarchy: tuple[str, ...] = ()
    #: What one row is, plural, for a chart's summary ("files", "changes").
    noun: str = "rows"


datasets: SeamRegistry[Dataset] = SeamRegistry("chat-dataset")

# Built-in families, imported for their registrations on first use.
_FAMILIES = ("files", "findings", "history", "architecture")


def register_dataset(
    name: str,
    *,
    description: str,
    fields: Mapping[str, DatasetField],
    basis: tuple[str, str],
    example: Mapping[str, Any],
    hierarchy: Iterable[str] = (),
    noun: str = "rows",
) -> Callable[[Callable[[LoadContext], Awaitable[list[dict[str, Any]]]]], Any]:
    """Register a loader as dataset *name*; the function is returned unchanged."""

    def decorate(fn: Callable[[LoadContext], Awaitable[list[dict[str, Any]]]]) -> Any:
        datasets.register(
            name,
            Dataset(
                name=name,
                description=description,
                fields=dict(fields),
                load=fn,
                basis=basis,
                example=dict(example),
                hierarchy=tuple(hierarchy),
                noun=noun,
            ),
        )
        return fn

    return decorate


def dataset_names() -> list[str]:
    for family in _FAMILIES:
        importlib.import_module(f"{__name__}.{family}")
    load_plugins()
    return datasets.names


def get_dataset(name: str) -> Dataset | None:
    return datasets.get(name) if name in dataset_names() else None


def catalog() -> list[dict[str, Any]]:
    """Every dataset with its fields, for the ``chart`` tool's discovery mode."""
    out = []
    for name in dataset_names():
        ds = datasets.get(name)
        out.append(
            {
                "dataset": name,
                "about": ds.description,
                "fields": {key: _field_line(f) for key, f in ds.fields.items()},
                "example": ds.example,
            }
        )
    return out


def _field_line(f: DatasetField) -> str:
    line = f"{f.type}: {f.meaning}"
    if f.aggregates:
        line += f" [{', '.join(f.aggregates)}]"
    if f.weight:
        line += f" (avg weighted by {f.weight})"
    return line


# ---------------------------------------------------------------------------
# Rows, cached per index state
# ---------------------------------------------------------------------------


@dataclass
class DatasetRows:
    rows: list[dict[str, Any]]
    #: The index commit the rows were read at, and whether the code moved past it.
    as_of: str | None
    stale: bool
    cached: bool = False


#: Rows by ``(repository id, index state, dataset)``, oldest first, so the
#: oldest is the one evicted at the cap.
_cache: OrderedDict[tuple[str, str, str], list[dict[str, Any]]] = OrderedDict()
_locks: dict[tuple[str, str, str], asyncio.Lock] = {}


def _store(key: tuple[str, str, str], rows: list[dict[str, Any]]) -> None:
    """Keep *rows*: a repository's older index states go, then the oldest past the cap."""
    repo_id, state, _ = key
    for old in [k for k in _cache if k[0] == repo_id and k[1] != state]:
        del _cache[old]
    _cache[key] = rows
    while len(_cache) > _CACHE_LIMIT:
        _cache.popitem(last=False)


async def load_rows(name: str, repo: str | None) -> DatasetRows:
    """Every row of dataset *name* for the repository *repo* names (None: the served one).

    Rows are cached per index state; the freshness (``as_of``, ``stale``) is
    read on every call, since the checkout can move while the index stays put.
    """
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
    from repowise.server.mcp_server._index_state import index_state_key

    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        state = index_state_key(repository)
        repo_path = str(ctx.path)
        hit = (repository.id, state, name) in _cache

        async def rows(dataset: str) -> list[dict[str, Any]]:
            key = (repository.id, state, dataset)
            if key in _cache:
                return _cache[key]
            lock = _locks.setdefault(key, asyncio.Lock())
            try:
                async with lock:
                    if key not in _cache:
                        ds = get_dataset(dataset)
                        if ds is None:
                            raise KeyError(dataset)
                        context = LoadContext(session, repository, repo_path, rows, ctx.alias)
                        _store(key, await ds.load(context))
            finally:
                # A failed load leaves no lock behind, so the next call reads again.
                _locks.pop(key, None)
            return _cache[key]

        loaded = await rows(name)
        as_of, stale = _fresh(repository)
    return DatasetRows(rows=loaded, as_of=as_of, stale=stale, cached=hit)


async def cached_rows(names: Iterable[str], repo: str | None) -> dict[str, list[dict[str, Any]]]:
    """The rows of each dataset in *names* already cached for the current index state.

    Nothing is loaded: a page chip quoting a figure is not worth a cold read
    (``files`` takes about 2s on a 5,000-file index), so a dataset no chart has
    read yet is simply absent. Warming the cache at index time would lift that.
    """
    from repowise.core.persistence.database import get_session
    from repowise.server.mcp_server._helpers import _get_repo, _resolve_repo_context
    from repowise.server.mcp_server._index_state import index_state_key

    wanted = list(names)
    if not wanted or not _cache:
        return {}
    ctx = await _resolve_repo_context(repo)
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        state = index_state_key(repository)
        keys = {name: (repository.id, state, name) for name in wanted}
    return {name: _cache[key] for name, key in keys.items() if key in _cache}


def _fresh(repository: Any) -> tuple[str | None, bool]:
    """The indexed commit and the stale flag ``_meta`` reports for *repository* now."""
    from repowise.server.mcp_server._meta import build_meta

    meta = build_meta(repository=repository)
    commit = meta.get("indexed_commit")
    return commit if isinstance(commit, str) else None, bool(meta.get("stale_warning"))


def clear_cache() -> None:
    _cache.clear()


__all__ = [
    "ARROW",
    "AVERAGES",
    "SUMS",
    "TEXT",
    "Dataset",
    "DatasetField",
    "DatasetRows",
    "LoadContext",
    "cached_rows",
    "catalog",
    "clear_cache",
    "dataset_names",
    "datasets",
    "get_dataset",
    "load_rows",
    "register_dataset",
]
