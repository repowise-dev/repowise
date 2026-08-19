"""Layers group the docs tree without needing a page to hang it off.

The tree used to express "this module belongs to the Analysis layer" by
parenting the module onto the *Analysis layer page*. That made a reading page
carry a structural job: the only way to keep the grouping was to keep eleven
pages whose whole content was a restatement of the same template, and which
scored as near-duplicates of each other.

The grouping moves onto the pages that are grouped. Every page that belongs to
a layer carries that layer's id and display name, so a reader-facing tree can
build the layer rows itself — the same way the Onboarding folder is already
built from a slot stamped on its members rather than from a page.

``layer_id`` is the join key and is always a stable slug; ``layer_name`` is
display text and may drift, so nothing keys on it.
"""

from __future__ import annotations

from repowise.core.generation.page_tree import TreeNode, assign_page_tree


def _node(page_id: str, page_type: str, target_path: str, **metadata) -> TreeNode:
    return TreeNode(
        page_id=page_id,
        page_type=page_type,
        target_path=target_path,
        metadata=dict(metadata),
    )


def _repo() -> list[TreeNode]:
    """An overview, two layers' worth of files, a module and a cycle."""
    return [
        _node("repo_overview:demo", "repo_overview", "demo"),
        _node(
            "file_page:core/analysis/health.py",
            "file_page",
            "core/analysis/health.py",
            layer_id="layer:analysis",
            layer_name="Analysis",
        ),
        _node(
            "file_page:core/analysis/risk.py",
            "file_page",
            "core/analysis/risk.py",
            layer_id="layer:analysis",
            layer_name="Analysis",
        ),
        _node(
            "file_page:core/ingestion/walker.py",
            "file_page",
            "core/ingestion/walker.py",
            layer_id="layer:ingestion",
            layer_name="Ingestion",
        ),
        _node(
            "module_page:core/analysis",
            "module_page",
            "core/analysis",
            file_paths=["core/analysis/health.py", "core/analysis/risk.py"],
        ),
        _node(
            "scc_page:cycle-1",
            "scc_page",
            "cycle-1",
            files=["core/analysis/health.py", "core/analysis/risk.py"],
        ),
    ]


def _by_id(nodes: list[TreeNode]) -> dict[str, TreeNode]:
    return {n.page_id: n for n in nodes}


class TestLayerProvenanceReachesGroupedPages:
    def test_module_carries_the_layer_it_belongs_to(self):
        nodes = _repo()
        assign_page_tree(nodes, ["layer:ingestion", "layer:analysis"])
        module = _by_id(nodes)["module_page:core/analysis"]
        assert module.metadata["layer_id"] == "layer:analysis"
        assert module.metadata["layer_name"] == "Analysis"

    def test_cycle_carries_the_layer_it_belongs_to(self):
        nodes = _repo()
        assign_page_tree(nodes, ["layer:ingestion", "layer:analysis"])
        cycle = _by_id(nodes)["scc_page:cycle-1"]
        assert cycle.metadata["layer_id"] == "layer:analysis"
        assert cycle.metadata["layer_name"] == "Analysis"

    def test_display_name_comes_from_the_files_not_the_slug(self):
        """The slug is derived and lossy; the name is the one the KG curated."""
        nodes = _repo()
        for n in nodes:
            if n.metadata.get("layer_id") == "layer:analysis":
                n.metadata["layer_name"] = "Analysis & Scoring"
        assign_page_tree(nodes, ["layer:analysis"])
        module = _by_id(nodes)["module_page:core/analysis"]
        assert module.metadata["layer_name"] == "Analysis & Scoring"

    def test_a_page_with_no_resolvable_layer_is_not_stamped(self):
        """Absent is honest; a made-up layer id would group pages wrongly."""
        nodes = [
            _node("repo_overview:demo", "repo_overview", "demo"),
            _node("module_page:misc", "module_page", "misc", file_paths=["misc/a.py"]),
        ]
        assign_page_tree(nodes, [])
        module = _by_id(nodes)["module_page:misc"]
        assert "layer_id" not in module.metadata

    def test_grouping_does_not_depend_on_a_layer_page_existing(self):
        """The whole point: no layer_page in the set, grouping still resolves."""
        nodes = _repo()
        assert not any(n.page_type == "layer_page" for n in nodes)
        assign_page_tree(nodes, ["layer:analysis"])
        module = _by_id(nodes)["module_page:core/analysis"]
        assert module.metadata["layer_id"] == "layer:analysis"
        # And it parents onto the overview rather than a page that is not there.
        assert module.parent_page_id == "repo_overview:demo"
