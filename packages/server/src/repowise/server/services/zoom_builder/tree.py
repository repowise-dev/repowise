"""Assemble the nested containment tree for the zoom map.

    system -> layer -> sub-group -> folder(s) -> file

Layers and their curated sub-groups come from the architecture view; the
folder tier is a directory trie built here from the file paths, with
single-child folder chains compressed (``a -> a/b -> a/b/c`` collapses to one
``a/b/c`` node) so the zoom does not waste a level on pass-through directories.

This is deliberately NOT ``derive_modules`` (which right-sizes wiki modules to
8-120 files): the zoom tree wants the true directory nesting so a viewer can
zoom folder by folder.

Pure and framework-agnostic: inputs are plain specs, output is a dict of frozen
``ZoomNode``. Importance, metrics, and layout are filled by later passes.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .models import ZoomNode


@dataclass(frozen=True)
class GroupSpec:
    """A curated sub-group inside a layer."""

    id: str
    name: str
    node_ids: list[str]  # repo-relative file paths


@dataclass(frozen=True)
class LayerSpec:
    """A curated architectural layer, with its files and optional sub-groups."""

    id: str
    name: str
    display_order: int
    node_ids: list[str]  # repo-relative file paths (every file in the layer)
    sub_groups: list[GroupSpec] = field(default_factory=list)


@dataclass(frozen=True)
class LeafInfo:
    """Per-file presentation + signal flags carried onto the file leaf node."""

    summary: str = ""
    language: str | None = None
    is_entry_point: bool = False
    is_hotspot: bool = False
    is_dead: bool = False
    is_test: bool = False
    on_flow: bool = False


SYSTEM_ID = "zm:sys"


def layer_id(raw: str) -> str:
    return f"zm:L:{raw}"


def group_id(raw: str) -> str:
    return f"zm:G:{raw}"


def folder_id(scope: str, path: str) -> str:
    """Folder ids are scoped by their owning layer/group.

    A directory path is NOT globally unique in this tree: the same dir can hold
    files assigned to different layers, and can appear under both a sub-group and
    its layer's ungrouped bucket. Qualifying the id with the owning scope keeps
    every folder node id unique (within one scope the trie paths are unique).
    """
    return f"zm:D:{scope}:{path}"


def file_id(path: str) -> str:
    # Files partition across the whole tree (each file is in exactly one layer
    # and one sub-group), so a path-based id is globally unique and stable for
    # deep-linking / focus.
    return f"zm:F:{path}"


# ---------------------------------------------------------------------------
# Directory trie (mutable during construction)
# ---------------------------------------------------------------------------


class _Dir:
    __slots__ = ("files", "name", "path", "subdirs")

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        self.subdirs: dict[str, _Dir] = {}
        self.files: list[str] = []


def _insert(root: _Dir, path: str) -> None:
    parts = path.split("/")
    *dirs, _fname = parts
    if not _fname:
        # Malformed (empty or trailing-slash) path: skip rather than mint a
        # blank-named leaf. The architecture view never emits these.
        return
    cur = root
    acc: list[str] = []
    for d in dirs:
        acc.append(d)
        key = "/".join(acc)
        nxt = cur.subdirs.get(d)
        if nxt is None:
            nxt = _Dir(name=d, path=key)
            cur.subdirs[d] = nxt
        cur = nxt
    cur.files.append(path)


def _compress(node: _Dir) -> tuple[str, _Dir]:
    """Collapse single-child folder chains. Returns the (joined-name, deepest
    folder) so ``a/(only)b/(only)c`` becomes one node named ``a/b/c``."""
    name = node.name
    while len(node.subdirs) == 1 and not node.files:
        (only,) = node.subdirs.values()
        name = f"{name}/{only.name}"
        node = only
    return name, node


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


class _Builder:
    def __init__(self, leaf_info: dict[str, LeafInfo]) -> None:
        self.leaf_info = leaf_info
        self.nodes: dict[str, ZoomNode] = {}

    def add(self, node: ZoomNode) -> str:
        self.nodes[node.id] = node
        return node.id

    def emit_file(self, path: str, parent_id: str, level: int) -> str:
        info = self.leaf_info.get(path, LeafInfo())
        name = path.rsplit("/", 1)[-1]
        return self.add(
            ZoomNode(
                id=file_id(path),
                parent_id=parent_id,
                level=level,
                kind="file",
                name=name,
                path=path,
                summary=info.summary,
                language=info.language,
                is_entry_point=info.is_entry_point,
                is_hotspot=info.is_hotspot,
                is_dead=info.is_dead,
                is_test=info.is_test,
                on_flow=info.on_flow,
            )
        )

    def emit_dir(self, dnode: _Dir, parent_id: str, level: int, scope: str) -> str:
        name, node = _compress(dnode)
        fid = folder_id(scope, node.path)
        children: list[str] = []
        for sub in sorted(node.subdirs.values(), key=lambda d: d.name):
            children.append(self.emit_dir(sub, fid, level + 1, scope))
        for path in sorted(node.files):
            children.append(self.emit_file(path, fid, level + 1))
        self.add(
            ZoomNode(
                id=fid,
                parent_id=parent_id,
                level=level,
                kind="folder",
                name=name,
                path=node.path,
                children=tuple(children),
            )
        )
        return fid

    def attach_files(
        self, file_paths: list[str], parent_id: str, child_level: int, scope: str
    ) -> list[str]:
        """Build a folder trie from ``file_paths`` and attach it under ``parent_id``.

        ``scope`` (the owning layer/group id) qualifies folder node ids so the
        same directory path under two different parents cannot collide. Files at
        the parent's own level attach as file leaves; everything else nests under
        compressed folder nodes.
        """
        root = _Dir(name="", path="")
        for path in file_paths:
            _insert(root, path)
        children: list[str] = []
        for sub in sorted(root.subdirs.values(), key=lambda d: d.name):
            children.append(self.emit_dir(sub, parent_id, child_level, scope))
        for path in sorted(root.files):
            children.append(self.emit_file(path, parent_id, child_level))
        return children


def build_tree(
    project_name: str,
    layers: list[LayerSpec],
    leaf_info: dict[str, LeafInfo],
) -> tuple[str, dict[str, ZoomNode]]:
    """Build the containment tree. Returns ``(root_id, nodes)``.

    ``layers`` should already be ordered (the caller passes them by
    ``display_order``); files not claimed by any sub-group attach directly under
    their layer so the file partition is preserved (every file appears once).
    """
    b = _Builder(leaf_info)
    layer_ids: list[str] = []

    for layer in sorted(layers, key=lambda layer_: layer_.display_order):
        lid = layer_id(layer.id)
        layer_children: list[str] = []
        layer_node_set = set(layer.node_ids)
        grouped: set[str] = set()

        for grp in layer.sub_groups:
            # Keep only files that belong to this layer AND have not already been
            # claimed by an earlier sub-group, so an overlapping membership can
            # never emit the same file leaf under two parents (which would
            # corrupt the tree into a non-partition).
            group_files = [p for p in grp.node_ids if p in layer_node_set and p not in grouped]
            if not group_files:
                continue
            grouped.update(group_files)
            gid = group_id(grp.id)
            group_children = b.attach_files(group_files, gid, child_level=3, scope=gid)
            b.add(
                ZoomNode(
                    id=gid,
                    parent_id=lid,
                    level=2,
                    kind="group",
                    name=grp.name,
                    children=tuple(group_children),
                )
            )
            layer_children.append(gid)

        # Files not claimed by any sub-group hang directly off the layer.
        ungrouped = [p for p in layer.node_ids if p not in grouped]
        layer_children.extend(b.attach_files(ungrouped, lid, child_level=2, scope=lid))

        b.add(
            ZoomNode(
                id=lid,
                parent_id=SYSTEM_ID,
                level=1,
                kind="layer",
                name=layer.name,
                children=tuple(layer_children),
            )
        )
        layer_ids.append(lid)

    b.add(
        ZoomNode(
            id=SYSTEM_ID,
            parent_id=None,
            level=0,
            kind="system",
            name=project_name,
            children=tuple(layer_ids),
        )
    )
    return SYSTEM_ID, b.nodes
