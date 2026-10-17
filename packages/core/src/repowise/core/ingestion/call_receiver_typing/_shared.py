"""Kinds, cache helpers and scope tests every receiver-typing module reads."""

from __future__ import annotations

from typing import TypeVar

from ..models import Symbol

# Which symbols own a class scope, and which of them swallow one. A class span
# contains every method body inside it, so both sets are needed to tell a field
# from a local.
_TYPE_KINDS = frozenset({"class", "struct", "interface", "enum", "trait", "impl"})
_FUNCTION_KINDS = frozenset({"function", "method"})

_SOURCE_CACHE_FILES = 4

_K = TypeVar("_K")
_V = TypeVar("_V")


def _store_capped(cache: dict[_K, _V], key: _K, value: _V, cap: int) -> None:
    """Store *value*, emptying *cache* first once it holds *cap* entries."""
    if len(cache) >= cap:
        cache.clear()
    cache[key] = value


def _is_module_level_function(symbol: Symbol) -> bool:
    return symbol.kind in _FUNCTION_KINDS and not symbol.parent_name
