"""Unit tests for the zoom-map builder (pure functions, no DB)."""

from __future__ import annotations

from repowise.server.services.c4_builder.models import (
    ArchEdge,
    ArchitectureView,
    ArchLayer,
    ArchNode,
    ArchSubGroup,
    ArchTourStep,
)
from repowise.server.services.zoom_builder import assemble_zoom_map
from repowise.server.services.zoom_builder.layout import lay_out
from repowise.server.services.zoom_builder.metrics import rollup_health, rollup_metrics
from repowise.server.services.zoom_builder.models import ZoomNode
from repowise.server.services.zoom_builder.relations import aggregate_relations
from repowise.server.services.zoom_builder.scoring import (
    FileStat,
    compute_file_signals,
    score_tree,
)
from repowise.server.services.zoom_builder.tree import (
    GroupSpec,
    LayerSpec,
    LeafInfo,
    build_tree,
    file_id,
    folder_id,
)

# ---------------------------------------------------------------------------
# tree
# ---------------------------------------------------------------------------


def test_build_tree_partitions_every_file_once():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["a/b/c/f1.py", "a/b/c/f2.py", "a/d/g1.py"],
        ),
        LayerSpec(
            id="layer:test",
            name="Test",
            display_order=1,
            node_ids=["tests/t1.py"],
        ),
    ]
    root_id, nodes = build_tree("proj", layers, {})

    files = [n for n in nodes.values() if n.kind == "file"]
    paths = sorted(n.path for n in files)
    assert paths == ["a/b/c/f1.py", "a/b/c/f2.py", "a/d/g1.py", "tests/t1.py"]
    # exactly one leaf per file
    assert len(files) == 4
    # root is the system, with the two layers as children
    assert nodes[root_id].kind == "system"
    assert len(nodes[root_id].children) == 2


def test_build_tree_compresses_single_child_folder_chains():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["a/b/c/f1.py", "a/b/c/f2.py"],
        ),
    ]
    _root, nodes = build_tree("proj", layers, {})
    # a -> b -> c collapses to a single folder "a/b/c"
    folders = [n for n in nodes.values() if n.kind == "folder"]
    assert len(folders) == 1
    assert folders[0].name == "a/b/c"
    assert folders[0].path == "a/b/c"
    assert len(folders[0].children) == 2


def test_build_tree_subgroups_and_ungrouped_files():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["svc/ingest/a.py", "svc/ingest/b.py", "svc/loose.py"],
            sub_groups=[
                GroupSpec(
                    id="layer:service:ingest",
                    name="ingest",
                    node_ids=["svc/ingest/a.py", "svc/ingest/b.py"],
                ),
            ],
        ),
    ]
    _root, nodes = build_tree("proj", layers, {})
    groups = [n for n in nodes.values() if n.kind == "group"]
    assert len(groups) == 1
    grp = groups[0]
    # the grouped files live under the group
    grouped_files = {nodes[c].path for cid in grp.children for c in _descend_files(nodes, cid)}
    assert grouped_files == {"svc/ingest/a.py", "svc/ingest/b.py"}
    # the ungrouped file attaches under the layer directly, not the group
    lid = grp.parent_id
    layer_file_paths = {
        nodes[fid_].path
        for cid in nodes[lid].children
        for fid_ in _descend_files(nodes, cid)
    }
    assert "svc/loose.py" in layer_file_paths


def test_build_tree_overlapping_subgroups_keep_partition():
    # Two sub-groups both claim "svc/shared.py"; the file must appear exactly
    # once (under the first group), never duplicated into a non-tree.
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["svc/a.py", "svc/b.py", "svc/shared.py"],
            sub_groups=[
                GroupSpec(id="g1", name="g1", node_ids=["svc/a.py", "svc/shared.py"]),
                GroupSpec(id="g2", name="g2", node_ids=["svc/b.py", "svc/shared.py"]),
            ],
        ),
    ]
    _root, nodes = build_tree("proj", layers, {})
    leaves = [n for n in nodes.values() if n.kind == "file"]
    # every file appears exactly once
    assert sorted(n.path for n in leaves) == ["svc/a.py", "svc/b.py", "svc/shared.py"]
    shared = [n for n in leaves if n.path == "svc/shared.py"]
    assert len(shared) == 1
    # and it is referenced by exactly one parent's children list
    parents = [n for n in nodes.values() if shared[0].id in n.children]
    assert len(parents) == 1
    assert parents[0].id == shared[0].parent_id


def test_build_tree_skips_trailing_slash_paths():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["svc/real.py", "svc/bogus/"],
        ),
    ]
    _root, nodes = build_tree("proj", layers, {})
    leaves = [n.path for n in nodes.values() if n.kind == "file"]
    assert leaves == ["svc/real.py"]
    assert all(n.name for n in nodes.values())  # no blank-named node


def _descend_files(nodes: dict[str, ZoomNode], node_id: str) -> list[str]:
    node = nodes[node_id]
    if node.kind == "file":
        return [node_id]
    out: list[str] = []
    for c in node.children:
        out.extend(_descend_files(nodes, c))
    return out


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def test_compute_file_signals_bfs_reachability():
    stats = [FileStat(path="main.py"), FileStat(path="util.py"), FileStat(path="orphan.py")]
    edges = [("main.py", "util.py", "imports")]
    signals = compute_file_signals(stats, edges, entry_points=["main.py"], tour_paths=set())

    assert signals["main.py"].entry_dist == 0
    assert signals["main.py"].on_flow is True
    assert signals["util.py"].entry_dist == 1
    assert signals["util.py"].on_flow is True
    assert signals["orphan.py"].entry_dist is None
    assert signals["orphan.py"].on_flow is False


def test_score_tree_ranks_entry_point_above_inert_sibling():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["pkg/main.py", "pkg/dead.py"],
        ),
    ]
    stats = [
        FileStat(path="pkg/main.py", is_entry_point=True, pagerank_pct=90.0, degree=10),
        FileStat(path="pkg/dead.py", is_dead=True, pagerank_pct=1.0, degree=0),
    ]
    signals = compute_file_signals(stats, [], entry_points=["pkg/main.py"], tour_paths=set())
    leaf_info = {
        "pkg/main.py": LeafInfo(is_entry_point=True, on_flow=True),
        "pkg/dead.py": LeafInfo(is_dead=True),
    }
    root_id, nodes = build_tree("proj", layers, leaf_info)
    nodes = score_tree(root_id, nodes, signals)

    main_node = nodes[file_id("pkg/main.py")]
    dead_node = nodes[file_id("pkg/dead.py")]
    assert main_node.sibling_rank == 1
    assert dead_node.sibling_rank == 2
    assert main_node.importance > dead_node.importance
    # top sibling normalizes to 1.0
    assert main_node.importance == 1.0


# ---------------------------------------------------------------------------
# metrics
# ---------------------------------------------------------------------------


def test_rollup_metrics_counts_subtree():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["pkg/a.py", "pkg/b.py", "pkg/sub/c.py"],
        ),
    ]
    leaf_info = {
        "pkg/a.py": LeafInfo(is_entry_point=True, on_flow=True),
        "pkg/b.py": LeafInfo(is_hotspot=True),
        "pkg/sub/c.py": LeafInfo(is_dead=True),
    }
    root_id, nodes = build_tree("proj", layers, leaf_info)
    nodes = rollup_metrics(root_id, nodes)

    root = nodes[root_id]
    assert root.metrics.file_count == 3
    assert root.metrics.hotspot_count == 1
    assert root.metrics.dead_count == 1
    assert root.metrics.entry_point_count == 1
    assert root.metrics.on_flow_count == 1


def test_rollup_health_loc_weighted_mean():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["pkg/a.py", "pkg/b.py", "pkg/c.py"],
        ),
    ]
    leaf_info = {
        "pkg/a.py": LeafInfo(health_score=2.0, loc=100),
        "pkg/b.py": LeafInfo(health_score=8.0, loc=300),
        # c.py is unscored: it must drop out of the mean entirely (weight 0), the
        # same way the /files treemap excludes files with no measured score.
        "pkg/c.py": LeafInfo(health_score=None, loc=50),
    }
    root_id, nodes = build_tree("proj", layers, leaf_info)
    nodes = rollup_health(root_id, nodes)

    # A file keeps its own score; an unscored file stays None.
    assert nodes[file_id("pkg/a.py")].health_score == 2.0
    assert nodes[file_id("pkg/c.py")].health_score is None
    # Container = loc-weighted mean over scored descendants only:
    # (2*100 + 8*300) / (100 + 300) = 2600 / 400 = 6.5
    assert nodes[root_id].health_score == 6.5


def test_rollup_health_none_when_no_scored_descendant():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["pkg/x.py", "pkg/y.py"],
        ),
    ]
    leaf_info = {"pkg/x.py": LeafInfo(), "pkg/y.py": LeafInfo()}  # both unscored
    root_id, nodes = build_tree("proj", layers, leaf_info)
    nodes = rollup_health(root_id, nodes)
    assert nodes[root_id].health_score is None


# ---------------------------------------------------------------------------
# layout
# ---------------------------------------------------------------------------


def test_layout_is_deterministic_and_within_unit_box():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=[f"pkg/f{i}.py" for i in range(6)],
        ),
    ]
    leaf_info = {f"pkg/f{i}.py": LeafInfo() for i in range(6)}
    root_id, nodes = build_tree("proj", layers, leaf_info)
    nodes = score_tree(root_id, nodes, compute_file_signals(
        [FileStat(path=f"pkg/f{i}.py", degree=i) for i in range(6)], [], [], set()
    ))
    a = lay_out(root_id, nodes)
    b = lay_out(root_id, nodes)

    for nid, node in a.items():
        assert node.layout == b[nid].layout  # deterministic
        rect = node.layout
        assert rect is not None
        assert -1e-6 <= rect.x <= 1.0 + 1e-6
        assert -1e-6 <= rect.y <= 1.0 + 1e-6
        assert rect.x + rect.w <= 1.0 + 1e-6
        assert rect.y + rect.h <= 1.0 + 1e-6

    # children of a parent tile its unit box (areas sum ~ 1)
    folder = next(n for n in a.values() if n.children and n.kind != "system")
    total = sum(a[c].layout.w * a[c].layout.h for c in folder.children)
    assert abs(total - 1.0) < 1e-3


# ---------------------------------------------------------------------------
# relations
# ---------------------------------------------------------------------------


def test_aggregate_relations_attributes_edge_to_lca():
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["svc/api/a.py", "svc/db/b.py"],
        ),
    ]
    root_id, nodes = build_tree("proj", layers, {})
    nodes = rollup_metrics(root_id, nodes)
    edges = [("svc/api/a.py", "svc/db/b.py", "imports")]
    rels = aggregate_relations(nodes, edges)

    assert len(rels) == 1
    rel = rels[0]
    # both files share the "svc" prefix (a single intermediate folder), so the
    # LCA is that folder and the relation is between its "api" and "db" children.
    # Folder ids are scoped by the owning layer.
    scope = "zm:L:layer:service"
    assert rel.parent_id == folder_id(scope, "svc")
    assert rel.source_id == folder_id(scope, "svc/api")
    assert rel.target_id == folder_id(scope, "svc/db")
    assert rel.edge_count == 1
    assert rel.label  # readable label set


def test_build_tree_same_dir_across_layers_no_id_collision():
    # A single directory "shared/" holds files assigned to two different layers.
    # The folder nodes must get distinct (scoped) ids, so neither overwrites the
    # other and both files survive under the right layer.
    layers = [
        LayerSpec(
            id="layer:service",
            name="Service",
            display_order=0,
            node_ids=["shared/svc.py"],
        ),
        LayerSpec(
            id="layer:util",
            name="Utility",
            display_order=1,
            node_ids=["shared/util.py"],
        ),
    ]
    _root, nodes = build_tree("proj", layers, {})
    leaves = sorted(n.path for n in nodes.values() if n.kind == "file")
    assert leaves == ["shared/svc.py", "shared/util.py"]
    shared_folders = [n for n in nodes.values() if n.kind == "folder" and n.path == "shared"]
    assert len(shared_folders) == 2  # one per layer, distinct ids
    assert len({f.id for f in shared_folders}) == 2
    # each shared folder is under a different layer
    assert {nodes[f.parent_id].kind for f in shared_folders} == {"layer"}
    assert len({f.parent_id for f in shared_folders}) == 2


# ---------------------------------------------------------------------------
# assemble + prune (end-to-end over a synthetic ArchitectureView)
# ---------------------------------------------------------------------------


def _node(path: str, **over) -> ArchNode:
    base = dict(
        id=path,
        node_type="file",
        name=path.rsplit("/", 1)[-1],
        file_path=path,
        line_range=None,
        summary="",
        complexity="simple",
        tags=[],
        language="python",
        pagerank=0.0,
        pagerank_percentile=0.0,
        betweenness=0.0,
        in_degree=0,
        out_degree=0,
        community_id=None,
        is_entry_point=False,
        is_test=False,
        is_hotspot=False,
        is_dead=False,
        has_doc=False,
        primary_owner=None,
        primary_owner_pct=None,
        bus_factor=None,
    )
    base.update(over)
    return ArchNode(**base)


def _view() -> ArchitectureView:
    nodes = [
        _node("pkg/main.py", is_entry_point=True, pagerank_percentile=95.0, in_degree=1, out_degree=2),
        _node("pkg/core/engine.py", pagerank_percentile=80.0, in_degree=2),
        _node("pkg/core/util.py", pagerank_percentile=10.0),
    ]
    layers = [
        ArchLayer(
            id="layer:service",
            name="Service",
            description="",
            node_ids=["pkg/main.py", "pkg/core/engine.py", "pkg/core/util.py"],
            file_count=3,
            complexity_distribution={"simple": 3, "moderate": 0, "complex": 0},
            health_score=None,
            sub_groups=[
                ArchSubGroup(
                    id="layer:service:core",
                    name="core",
                    node_ids=["pkg/core/engine.py", "pkg/core/util.py"],
                ),
            ],
            display_order=0,
        ),
    ]
    edges = [
        ArchEdge("pkg/main.py", "pkg/core/engine.py", "imports", "forward", 1.0, 1.0),
        ArchEdge("pkg/core/engine.py", "pkg/core/util.py", "imports", "forward", 1.0, 1.0),
    ]
    tour = [
        ArchTourStep(order=0, title="Start", description="", node_ids=["pkg/main.py"]),
    ]
    return ArchitectureView(
        project_name="proj",
        project_description="",
        layers=layers,
        nodes=nodes,
        edges=edges,
        tour=tour,
        total_files=3,
        total_symbols=0,
        total_edges=2,
        languages=["python"],
        frameworks=[],
        external_systems=[],
        entry_points=["pkg/main.py"],
    )


def test_assemble_zoom_map_full():
    zoom = assemble_zoom_map(_view())
    assert zoom.root_id == "zm:sys"
    assert zoom.total_files == 3
    assert zoom.nodes[zoom.root_id].kind == "system"
    # every file became a leaf with on_flow set (all reachable from main.py)
    files = [n for n in zoom.nodes.values() if n.kind == "file"]
    assert {n.path for n in files} == {"pkg/main.py", "pkg/core/engine.py", "pkg/core/util.py"}
    assert all(n.on_flow for n in files)
    # the entry-point file is the top-ranked sibling under its parent
    main = zoom.nodes[file_id("pkg/main.py")]
    assert main.is_entry_point and main.sibling_rank == 1
    # layout assigned everywhere
    assert all(n.layout is not None for n in zoom.nodes.values())
    assert not zoom.truncated


def test_assemble_zoom_map_health_rolls_up():
    # Effective score keyed by path -> (score, loc); util.py is omitted, so it
    # reads as unscored and drops out of every container mean.
    health = {
        "pkg/main.py": (9.0, 100),
        "pkg/core/engine.py": (3.0, 300),
    }
    zoom = assemble_zoom_map(_view(), health=health)

    assert zoom.nodes[file_id("pkg/main.py")].health_score == 9.0
    assert zoom.nodes[file_id("pkg/core/util.py")].health_score is None
    # The "core" group holds engine (scored) + util (unscored): only engine
    # contributes, so the group mean is engine's score.
    group = next(n for n in zoom.nodes.values() if n.kind == "group")
    assert group.health_score == 3.0
    # The system rolls both scored files: (9*100 + 3*300) / (100 + 300) = 4.5
    assert zoom.nodes[zoom.root_id].health_score == 4.5


def test_assemble_zoom_map_health_optional_defaults_to_unscored():
    # No health argument: every node reads as unscored (None), so the pure
    # assembly still works without a DB read.
    zoom = assemble_zoom_map(_view())
    assert all(n.health_score is None for n in zoom.nodes.values())


def test_assemble_zoom_map_max_depth_prunes_and_flags_truncated():
    zoom = assemble_zoom_map(_view(), max_depth=1)
    # only system (level 0) + layers (level 1) survive
    levels = {n.level for n in zoom.nodes.values()}
    assert levels == {0, 1}
    assert zoom.truncated is True
    # the layer's children were dropped at the frontier
    layer = next(n for n in zoom.nodes.values() if n.kind == "layer")
    assert layer.children == ()


def test_assemble_zoom_map_focus_reroots_subtree():
    full = assemble_zoom_map(_view())
    group = next(n for n in full.nodes.values() if n.kind == "group")
    focused = assemble_zoom_map(_view(), focus=group.id)
    assert focused.root_id == group.id
    assert focused.nodes[group.id].parent_id is None
    # only the group subtree is served
    assert all(
        n.id == group.id or _has_ancestor(focused.nodes, n.id, group.id)
        for n in focused.nodes.values()
    )


def test_assemble_zoom_map_unknown_focus_falls_back_to_system():
    zoom = assemble_zoom_map(_view(), focus="zm:F:does/not/exist")
    assert zoom.root_id == "zm:sys"


def _has_ancestor(nodes: dict[str, ZoomNode], node_id: str, ancestor_id: str) -> bool:
    cur = nodes[node_id].parent_id
    while cur is not None:
        if cur == ancestor_id:
            return True
        cur = nodes[cur].parent_id
    return False
