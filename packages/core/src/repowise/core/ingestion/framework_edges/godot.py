"""Godot ``class_name`` global-registry edges.

``class_name Effect`` at the top of a ``.gd`` file registers ``Effect`` as a
**project-global identifier**. Every other script in the project then writes::

    var damage_effect := DamageEffect.new()
    func apply(e: Effect) -> void:

with no import, no path, and no qualifier of any kind — Godot keeps the name
table in ``.godot/global_script_class_cache.cfg`` and the compiler resolves it.
It is the same mechanism as an ``[autoload]`` singleton, one level down: a
name the engine injects rather than one the source declares a dependency on.

So the static graph sees nothing. On the validation corpus this was the single
largest remaining source of dead-code false positives once scene edges landed:
94 of the 180 ``unreachable_file`` findings that survived those edges named a
``.gd`` whose ``class_name`` another file uses by name — every
``src/Classes/*.gd`` in Pixelorama, every ``custom_resources/*.gd`` in
deck_builder_tutorial. Adding these edges cleared all 94; the ``plugin.cfg``
entry point cleared one more, leaving 85.

**Declarations are read from the source text, not from the symbol list.** The
parser emits a ``class`` symbol for ``class_name Effect`` and for an inner
``class Inner:`` alike, and only the first is globally registered — the two are
distinguishable in the symbol list today only by a ``signature`` string that
happens to keep the ``class`` keyword for one of them, which is an artifact of
signature extraction rather than a contract. Godot itself requires
``class_name`` to be a top-level statement at the start of a line, so a
line-anchored match is exact; and this handler has to scan every file's text
anyway to find the *usages*, so reading the declaration from that same pass
keeps the whole mechanism in one place.

**Usage is an identifier-token match**, which counts an occurrence inside a
comment or a string literal. That errs toward "reachable", the safe direction
for dead code, and matches how the dead-code analyzer's own unindexed-token
prepass reads source.

Ceiling: this emits *file*-level edges only. A ``class_name`` script's
individual methods still read as uncalled to the unused-export pass, because
``DamageEffect.new()`` gives the call resolver a receiver it cannot map to a
class symbol — GDScript's script-level class is a sibling of its methods, not
their parent, so there is no ``(class, method)`` pair to look up. That is a
call-graph problem, not a project-awareness one.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext

# ``class_name Effect`` / ``class_name Effect extends RefCounted``. Anchored to
# the start of a line because Godot requires the statement at top level.
_CLASS_NAME_RE = re.compile(r"^class_name[ \t]+([A-Za-z_]\w*)", re.MULTILINE)

_IDENTIFIER_RE = re.compile(r"[A-Za-z_]\w*")


def _has_gdscript(parsed_files: dict[str, Any]) -> bool:
    return any(p.file_info.language == "gdscript" for p in parsed_files.values())


def _source_text(path: str, parsed: Any, source_map: dict[str, bytes]) -> str:
    """Source text for *path*, preferring bytes ingestion already read.

    This handler is the only one whose file set is the repo's dominant
    language, so re-reading it would be a second full pass over the tree.
    ``utf-8-sig`` on both paths: a BOM on line 1 would otherwise hide a
    ``class_name`` declared there.
    """
    raw = source_map.get(path)
    if raw is not None:
        return raw.decode("utf-8-sig", errors="replace")
    return read_text(parsed, encoding="utf-8-sig")


def _add_class_name_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
) -> int:
    from ..resolvers.gdscript import godot_project_root

    source_map = getattr(ctx, "source_map", None) or {}
    texts: dict[str, str] = {}
    roots: dict[str, str] = {}
    # Keyed by (project root, name), not by name alone. Godot's global class
    # table is per *project*, and one repo can hold many: `godot-demo-projects`
    # is dozens of independent projects side by side, several of which declare
    # `class_name Player`. Keying by name alone would collapse them and wire
    # every project's `Player` token to whichever file was parsed last -- the
    # same repo-root mistake resolvers/gdscript.py exists to avoid for res://.
    declared_in: dict[tuple[str, str], str] = {}

    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "gdscript":
            continue
        text = _source_text(path, parsed, source_map)
        if not text:
            continue
        texts[path] = text
        roots[path] = godot_project_root(path, ctx)
        for match in _CLASS_NAME_RE.finditer(text):
            declared_in[(roots[path], match.group(1))] = path

    if not declared_in:
        return 0

    count = 0
    for path, text in texts.items():
        root = roots[path]
        names = {n for n in _IDENTIFIER_RE.findall(text) if (root, n) in declared_in}
        # Sorted, not raw set order: string hashing is randomised per process,
        # so an unsorted iteration would insert this file's edges in a
        # different order on every run. Same reason ResolverContext exposes
        # sorted_paths rather than path_set.
        for name in sorted(names):
            # A self-reference is the declaration itself; _add_edge_if_new
            # rejects it.
            if _add_edge_if_new(graph, path, declared_in[(root, name)]):
                count += 1
    return count


class _GodotClassNameHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _has_gdscript(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_class_name_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [_GodotClassNameHandler()]
