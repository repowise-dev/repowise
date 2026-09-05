"""One sentence naming the repo areas a reply did not touch.

The index already groups every file into knowledge-graph layers. Naming the
largest layers a reply never touched keeps an agent from wandering into them.
"""

from __future__ import annotations

import json
from typing import Any

_PREFIX = "Unrelated to what was served: "
_MAX_SENTENCE = 200
_MAX_LAYERS = 3

# Layers are rewritten only when the index is, so one small-table read and one
# json parse per repo per index commit covers every call on that commit.
_CACHE: dict[str, list[tuple[str, frozenset[str]]]] = {}
_CACHE_LIMIT = 16


def reset_cache() -> None:
    """Drop every cached layer set. For tests and for a re-index in-process."""
    _CACHE.clear()


def _layer_paths(node_ids_json: str | None) -> frozenset[str]:
    """Repo-relative file paths in one layer. Node ids come with or without ``file:``."""
    try:
        raw = json.loads(node_ids_json) if node_ids_json else []
    except (TypeError, ValueError):
        return frozenset()
    if not isinstance(raw, list):
        return frozenset()
    paths = set()
    for node_id in raw:
        if not isinstance(node_id, str):
            continue
        path = node_id[5:] if node_id.startswith("file:") else node_id
        if path:
            paths.add(path)
    return frozenset(paths)


async def _load_layers(
    session: Any, repo_id: str, cache_key: str
) -> list[tuple[str, frozenset[str]]]:
    cached = _CACHE.get(cache_key)
    if cached is not None:
        return cached
    from repowise.core.persistence.crud.knowledge_graph import get_kg_layers

    rows = await get_kg_layers(session, repo_id)
    layers = [(row.name, _layer_paths(row.node_ids_json)) for row in rows]
    if len(_CACHE) >= _CACHE_LIMIT:
        _CACHE.clear()
    _CACHE[cache_key] = layers
    return layers


def _render(items: list[tuple[str, int]]) -> str:
    parts = [
        f"{name} ({count:,} file{'' if count == 1 else 's'})"
        if index == 0
        else f"{name} ({count:,})"
        for index, (name, count) in enumerate(items)
    ]
    return f"{_PREFIX}{', '.join(parts)}."


async def unrelated_scope_hint(
    session: Any,
    repo_id: str,
    served_paths: list[str],
    *,
    cache_key: str,
) -> str | None:
    """Name up to three of the largest layers holding none of ``served_paths``."""
    served = {path for path in served_paths if isinstance(path, str) and path}
    if not served:
        return None
    layers = await _load_layers(session, repo_id, cache_key)
    if not layers:
        return None
    unrelated = [
        (name, len(paths)) for name, paths in layers if paths and not (paths & served)
    ]
    if not unrelated:
        return None
    unrelated.sort(key=lambda item: (-item[1], item[0]))
    chosen = unrelated[:_MAX_LAYERS]
    while chosen:
        sentence = _render(chosen)
        if len(sentence) <= _MAX_SENTENCE:
            return sentence
        chosen = chosen[:-1]
    return None


__all__ = ["unrelated_scope_hint"]
