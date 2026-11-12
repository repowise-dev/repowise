"""Check a patched C/C++ graph against the include reach the last build saw.

C/C++ resolution reads every file a translation unit reaches through its
includes, transitively: a declaration pairs with the definition whose file
includes its header, a class's base must be reachable from it, a bare call may
land on a free function anywhere in the reach. The file-to-file edges that
carry this reach (includes, the header/implementation pairs, and the type uses
the resolver counts as imports) are only known once the patched files are
re-resolved, and a change to one file's reach changes it for every file that
includes it. So the patch is checked after the build: when an existing file's
include reach is not the one the last build had, the patch may differ from a
full build and the caller falls back to one.

A file the update added has no earlier reach; it is re-resolved in full, and a
file that starts to include it shows up as a changed reach of its own.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

_C_LANGUAGES = frozenset({"c", "cpp"})
_REACH_EDGES = frozenset({"imports", "type_use"})


def _carries_reach(attrs: Mapping[str, Any]) -> bool:
    """An edge the resolver walks as an include (see ``GraphBuilder.build``)."""
    return attrs.get("edge_type", "imports") in _REACH_EDGES and not attrs.get("no_scope_widening")


def _adjacency(edges: Iterable[tuple[str, str, Mapping[str, Any]]]) -> dict[str, set[str]]:
    """``{node: the nodes it reaches in one step}``."""
    adjacency: dict[str, set[str]] = {}
    for source, target, attrs in edges:
        if source != target and _carries_reach(attrs):
            adjacency.setdefault(source, set()).add(target)
    return adjacency


def _reach(start: str, adjacency: Mapping[str, set[str]]) -> set[str]:
    seen: set[str] = set()
    pending = [start]
    while pending:
        fresh = adjacency.get(pending.pop(), set()) - seen
        seen |= fresh
        pending.extend(fresh)
    return seen


def include_reach_refusal(
    graph: Any, seed: Mapping[str, Any], parsed_files: Mapping[str, Any]
) -> str | None:
    """Why the patched *graph* may differ from a full build, or ``None``.

    *seed* is the snapshot the patch started from. Only C/C++ files are
    compared: no other resolver follows includes transitively. A reach is
    compared over the files that exist now: a deleted file leaves every
    reach, and what it declared is gone by name.
    """
    files = set(parsed_files)
    c_files = {path for path, parsed in parsed_files.items() if parsed.file_info.language in _C_LANGUAGES}
    if not c_files:
        return None
    before = _adjacency(seed.get("edges", ()))
    after = _adjacency(graph.edges(data=True))
    known = {node for node, _attrs in seed.get("nodes", ())}
    for path in sorted(c_files & known):
        if before.get(path, set()) == after.get(path, set()):
            continue
        others = files - {path}
        if _reach(path, before) & others != _reach(path, after) & others:
            return f"{path}: the files its includes reach changed"
    return None
