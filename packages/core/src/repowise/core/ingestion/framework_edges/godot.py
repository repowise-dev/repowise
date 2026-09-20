"""Godot ``class_name`` global-registry edges.

``class_name Effect`` at the top of a ``.gd`` file registers ``Effect`` as a
**project-global identifier**. Every other script in the project then writes::

    var damage_effect := DamageEffect.new()
    func apply(e: Effect) -> void:

with no import, no path, and no qualifier of any kind. Godot keeps the name
table in ``.godot/global_script_class_cache.cfg`` and the compiler resolves it.
It is the same mechanism as an ``[autoload]`` singleton, one level down: a
name the engine injects rather than one the source declares a dependency on.

So the static graph sees nothing. On the validation corpus this was the single
largest remaining source of dead-code false positives once scene edges landed:
94 of the 180 ``unreachable_file`` findings that survived those edges named a
``.gd`` whose ``class_name`` another file uses by name: every
``src/Classes/*.gd`` in Pixelorama, every ``custom_resources/*.gd`` in
deck_builder_tutorial. Adding these edges cleared all 94; the ``plugin.cfg``
entry point cleared one more, leaving 85.

**Declarations are read from the source text, not from the symbol list.** The
parser emits a ``class`` symbol for ``class_name Effect`` and for an inner
``class Inner:`` alike, and only the first is globally registered. The two are
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
class symbol, because GDScript's script-level class is a sibling of its methods, not
their parent, so there is no ``(class, method)`` pair to look up. That is a
call-graph problem, not a project-awareness one.

Scene signal connections
------------------------

The second handler here closes the symbol-level half for the case that
dominates the residue. A ``.tscn`` records the editor's signal wiring as::

    [connection signal="pressed" from="StartButton" to="." method="_on_start"]

and ``_on_start`` lives in the script attached to the node ``to`` names. No
script mentions the method, so every editor-wired handler reads as an uncalled
private. The scene is the caller, and it is indexed, so the edge can be real:
scene module symbol to handler method symbol, through
:func:`~..framework_edges.base.add_symbol_edge`, the same shape Express uses
for a route argument and Spring for a container-wired bean.

**The node path is resolved, never the method name.** ``to`` is a path from
the scene root (``.`` is the root itself, ``Player/Sprite`` a descendant), and
each ``[node ...]`` block may carry ``script = ExtResource("id")`` naming a
``[ext_resource]`` header already read for its path. A node with no script of
its own runs its nearest ancestor's, so the walk climbs. Four refusals, each
recording no edge rather than a guess:

* the ``to`` path names no node in this scene,
* neither that node nor any ancestor carries a script,
* the node the script would come from is an ``instance=ExtResource(...)`` of
  another scene, whose script this file does not name,
* the resolved script declares no function by that name.

Matching on the method name alone would be the tempting shortcut and is the
one thing this must not do: ``_on_pressed`` appears in dozens of unrelated
scripts in a repo of many projects, and a wrong edge is worse than no edge.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
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


# ---------------------------------------------------------------------------
# Scene signal connections
# ---------------------------------------------------------------------------

# `[ext_resource type="Script" path="res://x.gd" id="1_abc"]`, and Godot 3's
# unquoted `id=1`. Read for the id, since the path itself is already an import.
_EXT_RESOURCE_ID_RE = re.compile(
    r"""^\[ext_resource\b[^\]]*?\bpath\s*=\s*(["'])(?P<path>.+?)\1[^\]]*?"""
    r"""\bid\s*=\s*(?:(["'])(?P<qid>[^"']+)\3|(?P<bid>\d+))""",
    re.MULTILINE,
)

# `[node name="Player" type="Area2D" parent="." instance=ExtResource("3")]`.
# `parent` is absent on exactly one node per scene, the root.
_NODE_RE = re.compile(r"^\[node\s+(?P<attrs>[^\]]*)\]", re.MULTILINE)

# `[connection signal="pressed" from="Btn" to="." method="_on_pressed"]`
_CONNECTION_RE = re.compile(r"^\[connection\s+(?P<attrs>[^\]]*)\]", re.MULTILINE)

_ATTR_RE = re.compile(r"""(\w+)\s*=\s*(?:(["'])(.*?)\2|(\S+))""")

# `script = ExtResource("2")` / Godot 3's `script = ExtResource( 2 )`, as a
# property line under a node header.
_SCRIPT_PROP_RE = re.compile(
    r"""^script\s*=\s*ExtResource\(\s*(?:(["'])([^"']+)\1|(\d+))\s*\)""",
    re.MULTILINE,
)


def _attrs(blob: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in _ATTR_RE.finditer(blob):
        out[m.group(1)] = m.group(3) if m.group(3) is not None else (m.group(4) or "")
    return out


class _SceneNode:
    """One ``[node]`` block: where it sits, and what script it runs."""

    __slots__ = ("instance_id", "parent", "script_id")

    def __init__(self, parent: str | None, script_id: str | None, instance_id: str | None):
        self.parent = parent
        self.script_id = script_id
        self.instance_id = instance_id


def parse_scene(text: str) -> tuple[dict[str, str], dict[str, _SceneNode], list[dict[str, str]]]:
    """Return ``(ext_resource id -> path, node path -> node, connections)``.

    Node paths are keyed the way ``[connection]`` writes them: ``"."`` is the
    root, a child of the root is its bare name, and anything deeper is
    slash-joined. That is the same spelling Godot uses, so no translation
    happens at lookup time.
    """
    resources: dict[str, str] = {}
    for m in _EXT_RESOURCE_ID_RE.finditer(text):
        rid = m.group("qid") or m.group("bid")
        if rid:
            resources[rid] = m.group("path")

    nodes: dict[str, _SceneNode] = {}
    # A node's `script =` property sits on its own line *after* the header, so
    # each block runs to the next header (of any kind) rather than to the next
    # blank line: Godot writes other properties in between.
    headers = list(_NODE_RE.finditer(text))
    for i, m in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[m.end() : end]
        # A `[connection]` or `[sub_resource]` between two node headers ends
        # this node's property block; anything after it belongs to neither.
        cut = body.find("\n[")
        if cut != -1:
            body = body[:cut]
        attrs = _attrs(m.group("attrs"))
        name = attrs.get("name")
        if not name:
            continue
        parent = attrs.get("parent")
        path = "." if parent is None else (name if parent == "." else f"{parent}/{name}")
        script = _SCRIPT_PROP_RE.search(body)
        script_id = (script.group(2) or script.group(3)) if script else None
        instance = attrs.get("instance") or ""
        inst_match = re.search(r"""ExtResource\(\s*["']?([^"')]+)["']?\s*\)""", instance)
        nodes[path] = _SceneNode(parent, script_id, inst_match.group(1) if inst_match else None)

    connections = [_attrs(m.group("attrs")) for m in _CONNECTION_RE.finditer(text)]
    return resources, nodes, connections


def _parent_path(path: str, nodes: dict[str, _SceneNode]) -> str | None:
    """The path of *path*'s parent node, or None once past the root."""
    if path == ".":
        return None
    node = nodes.get(path)
    if node is None or node.parent is None:
        return None
    return "." if node.parent == "." else node.parent


def resolve_handler_node(
    to: str, nodes: dict[str, _SceneNode]
) -> tuple[str | None, str]:
    """Return ``(ext_resource id of the script that runs *to*, reason)``.

    The reason is a short tag naming the refusal when the id is None, so a
    caller counting refusals does not have to re-derive why.
    """
    path = "." if to in ("", ".") else to
    if path not in nodes:
        return None, "node_not_found"
    seen: set[str] = set()
    while path is not None and path not in seen:
        seen.add(path)
        node = nodes.get(path)
        if node is None:
            return None, "node_not_found"
        if node.script_id is not None:
            return node.script_id, "ok"
        if node.instance_id is not None:
            # The script lives in the instanced scene, which this file does
            # not name. Climbing past it would attribute the handler to an
            # ancestor that does not own it.
            return None, "instanced_scene"
        path = _parent_path(path, nodes)
    return None, "no_script"


def _add_connection_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
) -> int:
    from ..resolvers.gdscript import resolve_gdscript_import

    source_map = getattr(ctx, "source_map", None) or {}
    # `{script path: {function name: symbol id}}`. Methods included: GDScript
    # writes a handler as a plain `func`, but an inner class puts one under a
    # parent, and either is a legitimate target for a connection.
    functions: dict[str, dict[str, str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "gdscript":
            continue
        table = {
            sym.name: sym.id
            for sym in parsed.symbols
            if sym.kind in ("function", "method")
        }
        if table:
            functions[path] = table
    if not functions:
        return 0

    count = 0
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "godot_resource":
            continue
        if not path.lower().endswith((".tscn", ".escn")):
            continue
        raw = source_map.get(path)
        text = (
            raw.decode("utf-8-sig", errors="replace")
            if raw is not None
            else read_text(parsed, encoding="utf-8-sig")
        )
        if "[connection" not in text:
            continue
        resources, nodes, connections = parse_scene(text)
        if not nodes:
            continue
        module_sym = f"{path}::__module__"
        for conn in connections:
            method = conn.get("method")
            if not method:
                continue
            script_id, _reason = resolve_handler_node(conn.get("to", "."), nodes)
            if script_id is None:
                continue
            raw_path = resources.get(script_id)
            if raw_path is None:
                continue
            target_file = resolve_gdscript_import(raw_path, path, ctx)
            if target_file is None or target_file not in ctx.path_set:
                continue
            sym_id = functions.get(target_file, {}).get(method)
            if sym_id is not None and add_symbol_edge(graph, module_sym, sym_id):
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


class _GodotSceneConnectionHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _has_gdscript(dctx.parsed_files) and any(
            p.file_info.language == "godot_resource" for p in dctx.parsed_files.values()
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_connection_edges(graph, parsed_files, ctx)


HANDLERS: list[FrameworkHandler] = [
    _GodotClassNameHandler(),
    _GodotSceneConnectionHandler(),
]
