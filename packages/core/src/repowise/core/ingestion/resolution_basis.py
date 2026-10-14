"""How much of a language's call graph rests on a guess.

One rule, read two ways: the MCP tools attach it beside an empty callers
list (from stored edges), and dead code reads it off the in-memory graph
before calling a finding deletion-ready.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

# Resolution origins the vocabulary in ``models.ResolutionOrigin`` annotates at
# or below 0.75 confidence. Each binds a name to a symbol that merely exists
# somewhere in the repo, so the edge is a guess, not a fact. NULL is not in
# here: it means the row predates the vocabulary, which is unknown rather than
# weak, so it counts in the denominator and not as a guess.
GUESSED_ORIGINS: frozenset[str] = frozenset(
    {
        "global_unique",
        "receiver_global",
        "receiver_extension_global",
        "receiver_typed_global",
        "receiver_field_global",
        "receiver_framework_global",
        "return_type_global",
    }
)


def guessed_share(origins: Mapping[str | None, int]) -> float | None:
    """Share of call edges from a guessed origin, or None with no call edge."""
    total = sum(origins.values())
    if not total:
        return None
    return sum(n for origin, n in origins.items() if origin in GUESSED_ORIGINS) / total


def call_origins_by_language(graph: Any) -> dict[str, dict[str | None, int]]:
    """``{language: {resolution origin: call edges}}``, by the caller's language."""
    out: dict[str, dict[str | None, int]] = {}
    nodes = graph.nodes
    for source, _target, data in graph.edges(data=True):
        if data.get("edge_type") != "calls":
            continue
        language = nodes[source].get("language") or ""
        origins = out.setdefault(language, {})
        origin = data.get("resolution_origin")
        origins[origin] = origins.get(origin, 0) + 1
    return out
