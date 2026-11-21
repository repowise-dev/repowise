"""Kinds, cache helpers and scope tests every receiver-typing module reads."""

from __future__ import annotations

from typing import TypeVar

from ..models import Symbol

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})

#: Files each per-file scan cache keeps. Resolution does not visit files in
#: order: one file's calls read the bindings and declarations of the files its
#: callees live in, so a handful missed nearly every lookup (99.5% of
#: ``_bindings_for`` calls rescanned a file on a 24-file update of this
#: repository). Ceiling: 256 files of scan results and source text is a few MB
#: per resolver; a larger repository still rescans past it.
_SOURCE_CACHE_FILES = 256

_K = TypeVar("_K")
_V = TypeVar("_V")


def _store_capped(cache: dict[_K, _V], key: _K, value: _V, cap: int) -> None:
    """Store *value*, evicting *cache*'s oldest entry once it holds *cap* entries.

    Oldest by insertion, so the files the current run of lookups keeps
    returning to stay cached; emptying the whole cache instead threw them out
    with the rest.
    """
    if key not in cache and len(cache) >= cap:
        del cache[next(iter(cache))]
    cache[key] = value


def _is_module_level_function(symbol: Symbol) -> bool:
    return symbol.kind in _FUNCTION_KINDS and not symbol.parent_name
