"""Tests for the KG curation/presentation pass (``kg_curation``).

Grows phase-by-phase. Phase 0 locks the seam: a no-op when the flag is off, a
flag reader, and the AST-graph-untouched guard.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from repowise.core.analysis.kg_curation import curate_knowledge_graph, curation_enabled
from repowise.core.analysis.knowledge_graph import (
    KnowledgeGraphResult,
    build_knowledge_graph_skeleton,
)

# ---------------------------------------------------------------------------
# Fixtures / fakes
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    path: str
    language: str = "python"
    size_bytes: int = 1000
    is_test: bool = False
    is_config: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False
    line_count: int = 100


@dataclass
class FakeSymbol:
    name: str = "my_func"
    kind: str = "function"
    start_line: int = 1
    end_line: int = 10
    is_reexport: bool = False


@dataclass
class FakeParsedFile:
    file_info: FakeFileInfo
    symbols: list = field(default_factory=list)
    imports: list = field(default_factory=list)
    exports: list = field(default_factory=list)


def _make_graph_builder(
    nodes: dict[str, dict],
    edges: list[tuple[str, str, dict]],
    communities: dict[str, int],
    community_infos: dict[int, Any],
    pagerank: dict[str, float],
    betweenness: dict[str, float] | None = None,
):
    import networkx as nx

    g = nx.DiGraph()
    for nid, data in nodes.items():
        g.add_node(nid, **data)
    for u, v, data in edges:
        g.add_edge(u, v, **data)

    builder = MagicMock()
    builder.graph.return_value = g
    builder.pagerank.return_value = pagerank
    builder.betweenness_centrality.return_value = betweenness or {}
    builder.community_detection.return_value = communities
    builder.community_info.return_value = community_infos
    return builder


def _community_info(cid: int, label: str, members: list[str]):
    return SimpleNamespace(
        community_id=cid,
        label=label,
        members=members,
        size=len(members),
        cohesion=0.8,
        dominant_language="python",
    )


@pytest.fixture
def simple_repo():
    """A tiny three-file repo: entry, core, test."""
    parsed = [
        FakeParsedFile(
            FakeFileInfo("src/main.py", is_entry_point=True), symbols=[FakeSymbol("main")]
        ),
        FakeParsedFile(FakeFileInfo("src/core.py"), symbols=[FakeSymbol("Core", "class")]),
        FakeParsedFile(
            FakeFileInfo("tests/test_main.py", is_test=True), symbols=[FakeSymbol("test_main")]
        ),
    ]
    nodes = {
        "src/main.py": {"node_type": "file", "language": "python", "is_entry_point": True},
        "src/core.py": {"node_type": "file", "language": "python"},
        "tests/test_main.py": {"node_type": "file", "language": "python", "is_test": True},
    }
    edges = [
        ("src/main.py", "src/core.py", {"edge_type": "imports", "confidence": 1.0}),
        ("tests/test_main.py", "src/main.py", {"edge_type": "imports", "confidence": 1.0}),
    ]
    communities = {"src/main.py": 0, "src/core.py": 0, "tests/test_main.py": 1}
    infos = {
        0: _community_info(0, "src/core", ["src/main.py", "src/core.py"]),
        1: _community_info(1, "tests", ["tests/test_main.py"]),
    }
    pagerank = {"src/main.py": 0.5, "src/core.py": 0.3, "tests/test_main.py": 0.2}
    builder = _make_graph_builder(nodes, edges, communities, infos, pagerank)
    repo_structure = SimpleNamespace(
        is_monorepo=False,
        total_files=3,
        entry_points=["src/main.py"],
    )
    return SimpleNamespace(parsed=parsed, builder=builder, repo_structure=repo_structure)


def _build_skeleton(repo) -> KnowledgeGraphResult:
    return build_knowledge_graph_skeleton(
        parsed_files=repo.parsed,
        graph_builder=repo.builder,
        repo_structure=repo.repo_structure,
        tech_stack=[],
        external_systems=[],
    )


def _curate(repo, **kw) -> KnowledgeGraphResult:
    return curate_knowledge_graph(
        _build_skeleton(repo),
        parsed_files=repo.parsed,
        graph_builder=repo.builder,
        repo_structure=repo.repo_structure,
        community_info=repo.builder.community_info(),
        **kw,
    )


def build_repo(
    paths: list[str],
    *,
    tests: set[str] | None = None,
    entries: set[str] | None = None,
    edges: list[tuple[str, str]] | None = None,
    barrels: set[str] | None = None,
    pagerank: dict[str, float] | None = None,
    betweenness: dict[str, float] | None = None,
):
    """Build a synthetic repo (parsed files + mock graph builder) from paths."""
    tests = tests or set()
    entries = entries or set()
    barrels = barrels or set()

    parsed = []
    nodes: dict[str, dict] = {}
    for p in paths:
        is_test = p in tests
        is_entry = p in entries
        if p in barrels:
            # A re-export shell: no runtime symbols, exports names only.
            pf = FakeParsedFile(
                FakeFileInfo(p, is_test=is_test, is_entry_point=is_entry),
                symbols=[],
                imports=[SimpleNamespace(is_reexport=True)],
                exports=["A", "B"],
            )
        else:
            pf = FakeParsedFile(
                FakeFileInfo(p, is_test=is_test, is_entry_point=is_entry),
                symbols=[FakeSymbol(name="thing", kind="function")],
            )
        parsed.append(pf)
        nodes[p] = {"node_type": "file", "language": "python"}
        if is_test:
            nodes[p]["is_test"] = True
        if is_entry:
            nodes[p]["is_entry_point"] = True

    graph_edges = [(u, v, {"edge_type": "imports", "confidence": 1.0}) for u, v in (edges or [])]
    communities = {p: 0 for p in paths}
    infos = {0: _community_info(0, "all", list(paths))}
    pr = pagerank or {p: 1.0 / max(len(paths), 1) for p in paths}
    builder = _make_graph_builder(nodes, graph_edges, communities, infos, pr, betweenness)
    repo_structure = SimpleNamespace(
        is_monorepo=True, total_files=len(paths), entry_points=sorted(entries)
    )
    return SimpleNamespace(parsed=parsed, builder=builder, repo_structure=repo_structure)


@pytest.fixture
def large_repo():
    """A realistically-shaped monorepo: several layers, two mega-layers."""
    paths: list[str] = []
    # Service mega-layer (core/*) spanning sub-dirs → should sub-split.
    for sub in ("ingestion", "analysis", "generation"):
        paths += [f"packages/core/src/repowise/core/{sub}/mod{i}.py" for i in range(24)]
    # UI mega-layer, spanning sub-dirs → should also sub-split.
    for sub in ("buttons", "forms", "layout"):
        paths += [f"packages/ui/src/components/{sub}/C{i}.tsx" for i in range(24)]
    # CLI (edge case A — must not be Application).
    paths += [f"packages/cli/src/repowise/cli/commands/cmd{i}.py" for i in range(20)]
    # API, Data, Config, Test, Utility — smaller named layers.
    paths += [f"src/api/route{i}.py" for i in range(12)]
    paths += [f"src/models/model{i}.py" for i in range(10)]
    paths += [f"src/utils/util{i}.py" for i in range(8)]
    paths += [f"config/conf{i}.yaml" for i in range(6)]
    tests = {f"tests/unit/test_{i}.py" for i in range(30)}
    paths += sorted(tests)
    return build_repo(paths, tests=tests)


# ---------------------------------------------------------------------------
# Phase 0 — the seam
# ---------------------------------------------------------------------------


class TestCurationFlag:
    def test_default_off(self, monkeypatch):
        monkeypatch.delenv("REPOWISE_KG_CURATION", raising=False)
        assert curation_enabled() is False

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("REPOWISE_KG_CURATION", val)
        assert curation_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "no", "off", "", "garbage"])
    def test_falsy_values_disable(self, monkeypatch, val):
        monkeypatch.setenv("REPOWISE_KG_CURATION", val)
        assert curation_enabled() is False


class TestIdentityPass:
    def test_noop_returns_input_unchanged(self, simple_repo):
        kg = _build_skeleton(simple_repo)
        before = kg.to_dict()
        out = curate_knowledge_graph(
            kg,
            parsed_files=simple_repo.parsed,
            graph_builder=simple_repo.builder,
            repo_structure=simple_repo.repo_structure,
            community_info=simple_repo.builder.community_info(),
            enabled=False,
        )
        assert out is kg
        assert out.to_dict() == before

    def test_ast_graph_untouched(self, simple_repo):
        """The §4D guard: graph node/edge counts identical pre/post curation."""
        g = simple_repo.builder.graph()
        before = (g.number_of_nodes(), g.number_of_edges())
        _curate(simple_repo, enabled=True)
        g = simple_repo.builder.graph()
        assert (g.number_of_nodes(), g.number_of_edges()) == before


# ---------------------------------------------------------------------------
# Phase 1 — curated layers
# ---------------------------------------------------------------------------


def _layer_names(kg) -> set[str]:
    return {layer["name"] for layer in kg.layers}


def _file_node_count(kg) -> int:
    return sum(1 for n in kg.nodes if n["id"].startswith("file:"))


class TestCuratedLayers:
    def test_flag_off_keeps_community_layers(self, large_repo):
        kg = _curate(large_repo, enabled=False)
        # The skeleton's community layers: one community "all" → one layer.
        assert _layer_names(kg) == {"all"}

    def test_layer_count_bounded(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        assert 6 <= len(kg.layers) <= 15

    def test_partition_invariant(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        seen: set[str] = set()
        for layer in kg.layers:
            for nid in layer["nodeIds"]:
                assert nid not in seen, "a file appears in two layers"
                seen.add(nid)
        assert len(seen) == _file_node_count(kg), "every file in exactly one layer"

    def test_no_singleton_spam(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        singletons = sum(1 for layer in kg.layers if len(layer["nodeIds"]) == 1)
        assert singletons / len(kg.layers) < 0.10

    def test_cli_is_its_own_layer(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        assert "CLI" in _layer_names(kg)
        assert "Application" not in _layer_names(kg)  # nothing falls through here

    def test_mega_layers_sub_split(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        by_name = {layer["name"]: layer for layer in kg.layers}
        for mega in ("Service", "UI"):
            sub = by_name[mega].get("subGroups")
            assert sub and len(sub) >= 2, f"{mega} should sub-split"
            # Sub-groups partition their parent layer.
            sub_ids = [nid for grp in sub for nid in grp["nodeIds"]]
            assert sorted(sub_ids) == sorted(by_name[mega]["nodeIds"])

    def test_largest_primary_layer_within_bound(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        total = _file_node_count(kg)
        largest = max(len(layer["nodeIds"]) for layer in kg.layers)
        assert largest / total <= 0.35

    def test_layers_are_dependency_ordered(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        orders = [layer["display_order"] for layer in kg.layers]
        assert orders == list(range(len(kg.layers)))

    def test_deterministic(self, large_repo):
        a = _curate(large_repo, enabled=True)
        b = _curate(large_repo, enabled=True)
        assert a.layers == b.layers


# ---------------------------------------------------------------------------
# Phase 2 — entry-point precision
# ---------------------------------------------------------------------------


@pytest.fixture
def entry_repo():
    """Real runtime entries plus re-export barrels, all flagged entry_point."""
    reals = [f"src/app{i}/main.py" for i in range(12)]
    barrels = {f"packages/p{i}/index.ts" for i in range(5)}
    paths = reals + sorted(barrels)
    entries = set(reals) | barrels
    # Give barrels deliberately high PageRank — they must still be demoted.
    pagerank = {p: (12 - i) / 100.0 for i, p in enumerate(reals)}
    for b in barrels:
        pagerank[b] = 0.9
    return build_repo(paths, entries=entries, barrels=barrels, pagerank=pagerank)


def _project(kg) -> dict:
    return kg.project


class TestEntryPointPrecision:
    def test_barrels_demoted_in_presentation(self, entry_repo):
        kg = _curate(entry_repo, enabled=True)
        for node in kg.nodes:
            if node.get("filePath", "").endswith("index.ts"):
                assert "entry_point" not in node["tags"]
                assert "barrel" in node["tags"]

    def test_no_barrel_in_surfaced_set(self, entry_repo):
        kg = _curate(entry_repo, enabled=True)
        assert all(not p.endswith("index.ts") for p in _project(kg)["entry_points"])
        assert all(not p.endswith("index.ts") for p in _project(kg)["entry_candidates"])

    def test_surfaced_set_capped(self, entry_repo):
        kg = _curate(entry_repo, enabled=True)
        assert len(_project(kg)["entry_points"]) <= 8

    def test_ranked_by_centrality(self, entry_repo):
        kg = _curate(entry_repo, enabled=True)
        # app0 has the highest PageRank among reals → ranks first.
        assert _project(kg)["entry_points"][0] == "src/app0/main.py"

    def test_full_candidate_list_kept(self, entry_repo):
        kg = _curate(entry_repo, enabled=True)
        # All 12 real entries survive as candidates; 5 barrels excluded.
        assert len(_project(kg)["entry_candidates"]) == 12

    def test_ast_is_entry_point_flag_untouched(self, entry_repo):
        """Demotion is presentation-only — the graph flag stays for dead-code."""
        _curate(entry_repo, enabled=True)
        g = entry_repo.builder.graph()
        for path, data in g.nodes(data=True):
            if path.endswith("index.ts"):
                assert data.get("is_entry_point") is True

    def test_deterministic(self, entry_repo):
        a = _curate(entry_repo, enabled=True)
        b = _curate(entry_repo, enabled=True)
        assert a.project["entry_points"] == b.project["entry_points"]
        assert a.project["entry_candidates"] == b.project["entry_candidates"]

    def test_flag_off_leaves_entry_points_untouched(self, entry_repo):
        kg = _curate(entry_repo, enabled=False)
        assert "entry_candidates" not in kg.project


# ---------------------------------------------------------------------------
# Phase 3 — canonical, layer-aware tour
# ---------------------------------------------------------------------------


@pytest.fixture
def readme_repo():
    """large_repo shape plus a real root README to anchor the tour."""
    paths = ["README.md", "src/api/route0.py", "src/api/route1.py"]
    paths += [f"src/models/model{i}.py" for i in range(4)]
    paths += [f"src/utils/util{i}.py" for i in range(3)]
    paths += [f"packages/cli/src/cli/commands/cmd{i}.py" for i in range(3)]
    return build_repo(paths)


@pytest.fixture
def flow_repo():
    """A repo with a real entry point, an import chain, and a test suite."""
    paths = [
        "README.md",
        "src/cli/main.py",
        "src/api/route.py",
        "src/services/svc.py",
        "src/models/model.py",
        "src/utils/helpers.py",
        "tests/conftest.py",
        "tests/test_svc.py",
    ]
    edges = [
        ("src/cli/main.py", "src/api/route.py"),
        ("src/api/route.py", "src/services/svc.py"),
        ("src/services/svc.py", "src/models/model.py"),
        ("tests/test_svc.py", "src/services/svc.py"),
        ("tests/conftest.py", "src/cli/main.py"),
    ]
    return build_repo(paths, entries={"src/cli/main.py"}, edges=edges)


def _layer_ids(kg) -> set[str]:
    return {layer["id"] for layer in kg.layers}


class TestCuratedTour:
    def test_within_step_budget(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        assert 0 < len(kg.tour) <= 12

    def test_opens_with_overview(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        assert kg.tour[0]["kind"] == "overview"
        assert kg.tour[0]["order"] == 1

    def test_every_step_maps_to_a_curated_layer(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        ids = _layer_ids(kg)
        for step in kg.tour:
            if step["kind"] == "overview":
                continue  # overview maps to a layer only when a README exists
            assert step["layer_id"] in ids

    def test_covers_most_layers(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        covered = {s["layer_id"] for s in kg.tour if s["kind"] != "overview"}
        assert len(covered) / len(_layer_ids(kg)) >= 0.90

    def test_orders_are_contiguous(self, large_repo):
        kg = _curate(large_repo, enabled=True)
        assert [s["order"] for s in kg.tour] == list(range(1, len(kg.tour) + 1))

    def test_readme_is_first_stop(self, readme_repo):
        kg = _curate(readme_repo, enabled=True)
        assert kg.tour[0]["kind"] == "overview"
        assert kg.tour[0]["target_path"] == "README.md"

    def test_deterministic(self, large_repo):
        a = _curate(large_repo, enabled=True)
        b = _curate(large_repo, enabled=True)
        assert a.tour == b.tour

    def test_flag_off_leaves_tour_empty(self, large_repo):
        kg = _curate(large_repo, enabled=False)
        assert kg.tour == []

    def test_entry_point_leads_the_walk(self, flow_repo):
        # Execution-flow order: right after the overview comes a real entry
        # point, never a test fixture or an arbitrary "top layer" file.
        kg = _curate(flow_repo, enabled=True)
        assert kg.tour[0]["kind"] == "overview"
        assert kg.tour[1]["target_path"] == "src/cli/main.py"
        assert "entry point" in kg.tour[1]["reason"]

    def test_walk_follows_import_depth(self, flow_repo):
        # main -> route -> svc -> model: the chain appears in BFS order.
        kg = _curate(flow_repo, enabled=True)
        pos = {s["target_path"]: i for i, s in enumerate(kg.tour)}
        assert (
            pos["src/cli/main.py"]
            < pos["src/api/route.py"]
            < pos["src/services/svc.py"]
            < pos["src/models/model.py"]
        )

    def test_tests_take_one_closing_stop(self, flow_repo):
        # The Test layer never competes for walk slots — exactly one closing
        # stop, after every runtime code stop.
        kg = _curate(flow_repo, enabled=True)
        test_steps = [s for s in kg.tour if s["layer_id"] == "layer:test"]
        assert len(test_steps) == 1
        last_runtime = max(
            i
            for i, s in enumerate(kg.tour)
            if s["kind"] == "code" and s["layer_id"] != "layer:test"
        )
        assert kg.tour.index(test_steps[0]) > last_runtime
        assert "verified" in test_steps[0]["reason"]

    def test_no_stack_position_claims(self, flow_repo, large_repo):
        # Reasons state evidence, never sort position ("Top of the stack"
        # branded conftest.py the start of the control flow).
        for repo in (flow_repo, large_repo):
            kg = _curate(repo, enabled=True)
            for step in kg.tour:
                assert "Top of the stack" not in step["reason"]
                assert "mid-stack" not in step["reason"]

    def test_readme_never_visited_twice(self, flow_repo):
        # The overview retargets to the root README — it must not reappear
        # as a code stop later in the walk.
        kg = _curate(flow_repo, enabled=True)
        readme_steps = [s for s in kg.tour if s["target_path"] == "README.md"]
        assert len(readme_steps) == 1
        assert readme_steps[0]["kind"] == "overview"

    def test_codeless_layers_get_no_manufactured_stop(self):
        # A "plugins" dir of JSON manifests mints a Middleware layer with no
        # code — the tour must not manufacture an anchor stop for it. The
        # code files exceed the walk budget so a manifest could only appear
        # via diversification.
        code = ["src/cli/main.py"] + [f"src/services/svc{i}.py" for i in range(14)]
        paths = code + [f"plugins/p{i}/plugin.json" for i in range(3)]
        repo = build_repo(
            paths,
            entries={"src/cli/main.py"},
            edges=[("src/cli/main.py", p) for p in code[1:]],
        )
        kg = _curate(repo, enabled=True)
        for step in kg.tour:
            assert not step["target_path"].endswith("plugin.json")

    def test_example_programs_never_take_tour_slots(self):
        # examples/ are documentation-by-code: no walk slots, no layer faces.
        code = ["src/cli/main.py"] + [f"src/services/svc{i}.py" for i in range(3)]
        paths = code + [f"examples/demo{i}/main.py" for i in range(5)]
        paths += ["examples/versions/data/errors.py"]  # would front Data otherwise
        repo = build_repo(
            paths,
            entries={"src/cli/main.py"},
            edges=[("src/cli/main.py", p) for p in code[1:]],
        )
        kg = _curate(repo, enabled=True)
        for step in kg.tour:
            assert not step["target_path"].startswith("examples/")

    def test_barrel_steps_never_claim_entry_point(self):
        # An index.ts barrel may legitimately seed the walk, but its reason
        # must say re-export hub, not execution entry point.
        barrel = "packages/types/src/index.ts"
        repo = build_repo(
            [barrel, "src/services/svc.py"],
            entries={barrel},
            barrels={barrel},
            edges=[(barrel, "src/services/svc.py")],
        )
        kg = _curate(repo, enabled=True)
        barrel_steps = [s for s in kg.tour if s["target_path"] == barrel]
        assert barrel_steps, "barrel should still appear on the walk"
        for s in barrel_steps:
            assert "An entry point" not in s["reason"]
            assert "re-export hub" in s["reason"]


class TestEntryPointFallback:
    def test_filename_scorers_fill_in_when_nothing_is_flagged(self):
        # No ingestion entry flags at all: the entry-style filename (main.py)
        # still surfaces, so the orientation panel never opens empty.
        repo = build_repo(
            ["src/cli/main.py", "src/services/svc.py", "tests/test_svc.py"],
            edges=[("src/cli/main.py", "src/services/svc.py")],
        )
        kg = _curate(repo, enabled=True)
        assert kg.project["entry_points"] == ["src/cli/main.py"]

    def test_fallback_skips_test_files(self):
        # A test named like an entry must not be surfaced by the fallback.
        repo = build_repo(["tests/main.py", "src/services/svc.py"])
        kg = _curate(repo, enabled=True)
        assert kg.project["entry_points"] == []

    def test_code_files_never_typed_infra_by_name(self):
        # A Python module that *parses* Dockerfiles is code, not infra.
        repo = build_repo(
            ["core/ingestion/languages/specs/dockerfile.py", "Dockerfile"]
        )
        kg = _curate(repo, enabled=True)
        by_path = {
            n["filePath"]: n
            for n in kg.nodes
            if n.get("filePath") and str(n.get("id", "")).startswith("file:")
        }
        spec = by_path["core/ingestion/languages/specs/dockerfile.py"]
        assert spec.get("type") != "service"
        assert "infra" not in (spec.get("tags") or [])
        assert by_path["Dockerfile"].get("type") == "service"  # real one still promoted

    def test_flagged_test_fixtures_not_surfaced(self):
        # Ingestion may flag a wsgi.py inside tests/ as an entry point; the
        # presentation surface must keep it out (a reader enters via src/).
        repo = build_repo(
            ["src/app/wsgi.py", "tests/test_apps/helloworld/wsgi.py"],
            entries={"src/app/wsgi.py", "tests/test_apps/helloworld/wsgi.py"},
        )
        kg = _curate(repo, enabled=True)
        assert kg.project["entry_points"] == ["src/app/wsgi.py"]


# ---------------------------------------------------------------------------
# Phase 4 — node typing & never-empty summaries
# ---------------------------------------------------------------------------


@pytest.fixture
def typed_repo():
    """A repo exercising infra/CI/data typing plus a barrel and a test."""
    barrel = "packages/p/index.ts"
    paths = [
        ".github/workflows/ci.yml",
        "Dockerfile",
        "infra/main.tf",
        "db/migrations/001_init.sql",
        "config/app.yaml",
        "README.md",
        "src/api/route.py",
        "tests/unit/test_route.py",
        barrel,
    ]
    return build_repo(
        paths,
        tests={"tests/unit/test_route.py"},
        entries={barrel},
        barrels={barrel},
    )


def _node_by_path(kg, path):
    return next(n for n in kg.nodes if n.get("filePath") == path)


class TestNodeTyping:
    def test_ci_workflow_is_pipeline(self, typed_repo):
        kg = _curate(typed_repo, enabled=True)
        n = _node_by_path(kg, ".github/workflows/ci.yml")
        assert n["type"] == "pipeline"
        assert "ci" in n["tags"]

    def test_dockerfile_and_terraform_are_infra(self, typed_repo):
        kg = _curate(typed_repo, enabled=True)
        for p in ("Dockerfile", "infra/main.tf"):
            n = _node_by_path(kg, p)
            assert n["type"] == "service"
            assert "infra" in n["tags"]

    def test_migration_sql_is_schema(self, typed_repo):
        kg = _curate(typed_repo, enabled=True)
        n = _node_by_path(kg, "db/migrations/001_init.sql")
        assert n["type"] == "schema"
        assert "data" in n["tags"]


class TestSummaryFloor:
    def test_no_empty_file_summary(self, typed_repo, large_repo):
        for repo in (typed_repo, large_repo):
            kg = _curate(repo, enabled=True)
            for n in kg.nodes:
                if n["id"].startswith("file:"):
                    assert n["summary"], f"empty summary for {n['filePath']}"

    def test_barrel_summary_is_honest(self, typed_repo):
        kg = _curate(typed_repo, enabled=True)
        n = _node_by_path(kg, "packages/p/index.ts")
        assert "barrel" in n["summary"].lower()

    def test_test_summary_names_target(self, typed_repo):
        kg = _curate(typed_repo, enabled=True)
        n = _node_by_path(kg, "tests/unit/test_route.py")
        assert n["summary"].lower().startswith("tests for")

    def test_flag_off_leaves_summaries_empty(self, typed_repo):
        kg = _curate(typed_repo, enabled=False)
        assert all(n["summary"] == "" for n in kg.nodes if n["id"].startswith("file:"))

    def test_deterministic(self, typed_repo):
        a = _curate(typed_repo, enabled=True)
        b = _curate(typed_repo, enabled=True)
        assert [n.get("summary") for n in a.nodes] == [n.get("summary") for n in b.nodes]


class TestSummaryFloorDeferral:
    def test_defer_leaves_summaries_for_later(self, typed_repo):
        # Generate mode defers the floor so page backfill can win first.
        kg = curate_knowledge_graph(
            _build_skeleton(typed_repo),
            parsed_files=typed_repo.parsed,
            graph_builder=typed_repo.builder,
            repo_structure=typed_repo.repo_structure,
            community_info=typed_repo.builder.community_info(),
            enabled=True,
            defer_summary_floor=True,
        )
        assert any(n["summary"] == "" for n in kg.nodes if n["id"].startswith("file:"))

    def test_apply_floor_fills_only_empties(self, typed_repo):
        from repowise.core.analysis.kg_curation import apply_summary_floor

        kg = _build_skeleton(typed_repo)
        # Simulate a rich page summary already backfilled onto one node.
        _node_by_path(kg, "src/api/route.py")["summary"] = "Rich page summary."
        apply_summary_floor(kg, typed_repo.parsed)
        assert _node_by_path(kg, "src/api/route.py")["summary"] == "Rich page summary."
        assert all(n["summary"] for n in kg.nodes if n["id"].startswith("file:"))
