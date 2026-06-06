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

from repowise.core.analysis.kg_curation import (
    _dominant_language,
    curate_knowledge_graph,
    curation_enabled,
)
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
        # Language follows the extension (registry truth) so polyglot
        # fixtures behave like real repos; bare paths default to python.
        from pathlib import PurePosixPath

        from repowise.core.ingestion.languages.registry import REGISTRY

        lang = REGISTRY.from_extension(PurePosixPath(p).suffix)
        if lang == "unknown":
            lang = "python"
        if p in barrels:
            # A re-export shell: no runtime symbols, exports names only.
            pf = FakeParsedFile(
                FakeFileInfo(p, language=lang, is_test=is_test, is_entry_point=is_entry),
                symbols=[],
                imports=[SimpleNamespace(is_reexport=True)],
                exports=["A", "B"],
            )
        else:
            pf = FakeParsedFile(
                FakeFileInfo(p, language=lang, is_test=is_test, is_entry_point=is_entry),
                symbols=[FakeSymbol(name="thing", kind="function")],
            )
        parsed.append(pf)
        nodes[p] = {"node_type": "file", "language": lang}
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
    # A realistic monorepo HAS imports: wire a dense chain so the honest-
    # degradation detector classifies it as flow, not structural.
    code = [p for p in paths if not p.endswith(".yaml") and p not in tests]
    edges = [
        (code[i], code[i + step])
        for step in (1, 2, 3)
        for i in range(len(code) - step)
    ]
    return build_repo(paths, tests=tests, edges=edges)


# ---------------------------------------------------------------------------
# Phase 0 — the seam
# ---------------------------------------------------------------------------


class TestCurationFlag:
    def test_default_on(self, monkeypatch):
        # Flipped at the cross-language acceptance gate: the 38-repo matrix
        # is the evidence; the env var is an opt-out now.
        monkeypatch.delenv("REPOWISE_KG_CURATION", raising=False)
        assert curation_enabled() is True

    @pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values_enable(self, monkeypatch, val):
        monkeypatch.setenv("REPOWISE_KG_CURATION", val)
        assert curation_enabled() is True

    @pytest.mark.parametrize("val", ["0", "false", "FALSE", "no", "off"])
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


class TestDominantLanguageDeterminism:
    def test_empty_input_returns_empty(self):
        assert _dominant_language([]) == ""

    def test_clear_majority_wins(self):
        assert _dominant_language(["python", "python", "go"]) == "python"

    def test_tie_is_order_independent(self):
        # Counter.most_common breaks count ties by insertion order, which is
        # ingestion-completion-order nondeterministic and reaches persisted
        # output (graph_mode + tour prose). The same multiset must yield the
        # same dominant language whatever order it was accumulated in.
        forward = _dominant_language(["go", "go", "python", "python"])
        reverse = _dominant_language(["python", "python", "go", "go"])
        assert forward == reverse


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

    def test_ruby_spec_dir_entry_excluded_via_language(self):
        # A Ruby file under spec/ is RSpec material whatever its name; ruby
        # declares spec/ as an unambiguous test-dir token, so the entry-point
        # guards must pass language to infer_layer. Without it, config.rb
        # classifies as a runtime layer and (flagged entry_point) leaks into
        # the surfaced entry set.
        repo = build_repo(
            ["src/main.rb", "spec/dummy/config.rb"],
            entries={"src/main.rb", "spec/dummy/config.rb"},
        )
        kg = _curate(repo, enabled=True)
        assert "spec/dummy/config.rb" not in kg.project["entry_points"]
        assert "spec/dummy/config.rb" not in kg.project["entry_candidates"]
        assert "src/main.rb" in kg.project["entry_candidates"]


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


# ---------------------------------------------------------------------------
# Closing-stop selection (suite anchors, descriptors, polyglot)
# ---------------------------------------------------------------------------


def _closing_stops(kg) -> list[dict]:
    return [s for s in kg.tour if "test suite" in s["reason"]]


class TestClosingStopParity:
    def test_descriptor_never_faces_the_suite(self):
        # gson regression: a shallow JPMS module-info.java sits in the Test
        # layer; the closing stop must face a real camel test instead.
        repo = build_repo(
            [
                "src/main/java/com/x/App.java",
                "src/main/java/com/x/Core.java",
                "jpms/src/test/java/module-info.java",
                "src/test/java/com/x/core/AppTest.java",
            ],
            entries={"src/main/java/com/x/App.java"},
            edges=[("src/main/java/com/x/App.java", "src/main/java/com/x/Core.java")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "src/test/java/com/x/core/AppTest.java"

    def test_fixture_file_never_faces_the_suite(self):
        # gson regression: same-package edges gave the test tree's
        # ParameterizedTypeFixtures.java the highest pagerank — but a
        # fixtures file holds test data, it doesn't verify behavior.
        repo = build_repo(
            [
                "src/main/java/com/x/App.java",
                "src/main/java/com/x/Core.java",
                "src/test/java/com/x/TypeFixtures.java",
                "src/test/java/com/x/CoreTest.java",
            ],
            entries={"src/main/java/com/x/App.java"},
            edges=[
                ("src/main/java/com/x/App.java", "src/main/java/com/x/Core.java"),
                # Tests lean on the fixture file — its rank dwarfs the test's.
                ("src/test/java/com/x/CoreTest.java", "src/test/java/com/x/TypeFixtures.java"),
            ],
            pagerank={"src/test/java/com/x/TypeFixtures.java": 0.9},
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "src/test/java/com/x/CoreTest.java"

    def test_fixture_convention_does_not_leak_to_other_languages(self):
        # The Fixture/Fixtures camel rule is declared by java — a python
        # file with the same shape stays an eligible suite face.
        repo = build_repo(
            [
                "src/app.py",
                "src/core.py",
                "tests/DataFixtures.py",
            ],
            entries={"src/app.py"},
            edges=[("src/app.py", "src/core.py")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "tests/DataFixtures.py"

    def test_ruby_suite_anchor_wins(self):
        repo = build_repo(
            [
                "lib/app.rb",
                "lib/core.rb",
                "test/test_helper.rb",
                "test/app_test.rb",
            ],
            entries={"lib/app.rb"},
            edges=[("lib/app.rb", "lib/core.rb")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "test/test_helper.rb"

    def test_runner_face_preserved_without_anchor(self):
        # django-style: no conftest, the suite runner is the shallowest
        # dominant-language file — it must keep facing the suite.
        repo = build_repo(
            [
                "src/app.py",
                "src/core.py",
                "tests/runtests.py",
                "tests/test_deep/test_one.py",
            ],
            entries={"src/app.py"},
            edges=[("src/app.py", "src/core.py")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "tests/runtests.py"

    def test_polyglot_closing_reason_mentions_other_suites(self):
        # 6 python + 4 ts code files (40% ts) with a ts test tree: the stop
        # faces the python suite but names the TypeScript one.
        repo = build_repo(
            [
                "src/a.py", "src/b.py", "src/c.py", "src/d.py", "src/e.py", "src/f.py",
                "web/x.ts", "web/y.ts", "web/z.ts", "web/w.ts",
                "tests/conftest.py",
                "web/__tests__/x.test.ts",
            ],
            entries={"src/a.py"},
            edges=[("src/a.py", "src/b.py")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "tests/conftest.py"
        assert "TypeScript test suite lives alongside it" in closing[0]["reason"]

    def test_monoglot_reason_unchanged(self):
        repo = build_repo(
            ["src/app.py", "src/core.py", "tests/conftest.py"],
            entries={"src/app.py"},
            edges=[("src/app.py", "src/core.py")],
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert closing[0]["reason"] == "The test suite — how the system's behavior is verified."


class TestNonInfraCodeTyping:
    def test_tier3_code_never_typed_infra_by_name(self):
        # Equivalents of the dockerfile.py guard for every code language:
        # code that *handles* infra formats is code, not infra.
        from repowise.core.analysis.kg_curation import _enrich_type

        assert _enrich_type("k8s/dockerfile.nim", "file") == ("file", None)
        assert _enrich_type("build/makefile.clj", "file") == ("file", None)
        assert _enrich_type("ci/.github/workflows/helpers.hs", "file") == ("file", None)
        assert _enrich_type("tools/docker-compose.dart", "file") == ("file", None)

    def test_infra_languages_still_promote(self):
        from repowise.core.analysis.kg_curation import _enrich_type

        assert _enrich_type("deploy/k8s/deploy.sh", "file") == ("service", "infra")
        assert _enrich_type("infra/main.tf", "file") == ("service", "infra")
        assert _enrich_type(".github/workflows/ci.yml", "config") == ("pipeline", "ci")


# ---------------------------------------------------------------------------
# Honest degradation (flow / sparse / structural)
# ---------------------------------------------------------------------------

# Vocabulary that claims an execution flow. Structural tours must never use
# it — "named like an entry file" is the one sanctioned entry phrasing.
_FLOW_CLAIMS = (
    "entry point",
    "imports fan out",
    "imports deep",
    "import path",
    "directly used by the entry",
    "widely-imported",
)


def _edgeless_zig_repo():
    """A Tier-3 repo: real code files, import_support='none', zero edges.

    Zig stays on the structural (no-resolver) tier — elixir, the previous
    fixture language, was promoted to the lightweight regex tier.
    """
    paths = [
        "src/shop.zig",
        "src/application.zig",
        "src/checkout.zig",
        "src/cart.zig",
        "src/billing/invoice.zig",
        "tools/seeds.zig",
        "test/shop_test.zig",
        "test/helper.zig",
        "README.md",
    ]
    return build_repo(paths, tests={"test/shop_test.zig", "test/helper.zig"})


class TestHonestDegradation:
    def test_structural_mode_for_unsupported_language(self):
        repo = _edgeless_zig_repo()
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "structural"

    def test_structural_tour_makes_no_flow_claims(self):
        repo = _edgeless_zig_repo()
        kg = _curate(repo, enabled=True)
        assert kg.tour, "structural repos still get a tour"
        for step in kg.tour:
            low = step["reason"].lower()
            for claim in _FLOW_CLAIMS:
                assert claim not in low, (step["target_path"], step["reason"])

    def test_structural_anchor_states_the_evidence_and_the_gap(self):
        repo = _edgeless_zig_repo()
        kg = _curate(repo, enabled=True)
        code_steps = [s for s in kg.tour if s["kind"] == "code" and "test" not in s["reason"].lower()]
        assert code_steps, kg.tour
        anchor = code_steps[0]
        assert "isn't supported for Zig yet" in anchor["reason"]
        assert anchor["depth"] == 0

    def test_structural_layers_labeled_canonical(self):
        repo = _edgeless_zig_repo()
        kg = _curate(repo, enabled=True)
        assert kg.layers
        assert all(layer.get("order_basis") == "canonical" for layer in kg.layers)

    def test_flow_layers_labeled_imports(self, flow_repo):
        kg = _curate(flow_repo, enabled=True)
        assert any(layer.get("order_basis") == "imports" for layer in kg.layers)

    def test_flow_repo_stays_flow(self, flow_repo):
        kg = _curate(flow_repo, enabled=True)
        assert kg.project["graph_mode"] == "flow"

    def test_sparse_mode_for_low_density_weak_resolution(self):
        # 30 python files (over the small-repo floor), thin internal chain
        # and most import targets landing on external nodes: low density
        # AND weak resolution is the broken-resolver signature -> sparse.
        paths = [f"src/m{i}.py" for i in range(30)]
        edges = [(paths[i], paths[i + 1]) for i in range(29)]
        edges += [(paths[i], f"external:mystery{i}") for i in range(5, 30)]
        repo = build_repo(paths, entries={"src/m0.py"}, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "sparse"

    def test_flow_mode_for_low_density_strong_resolution(self):
        # Same thin chain but almost every target resolves internally: a
        # require-light, well-resolved graph (sinatra after stdlib
        # filtering: 1.31 edges/file at 0.77 resolution) narrates honestly
        # -> flow, not sparse. Low density alone no longer indicts the
        # resolver.
        paths = [f"src/m{i}.py" for i in range(30)]
        edges = [(paths[i], paths[i + 1]) for i in range(29)]
        edges += [(paths[0], "external:gem:rack")]
        repo = build_repo(paths, entries={"src/m0.py"}, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "flow"

    def test_sparse_unreached_reason_blames_the_graph(self):
        # Files off the walk in sparse mode cite incomplete resolution, not
        # the file ("standalone or supporting" would blame the file).
        paths = [f"src/m{i}.py" for i in range(30)]
        # Density in the sparse band with weak resolution, edges
        # concentrated in m0..m5: the walk's later slots fill with
        # unreached files, whose reasons are under test.
        edges = [
            (paths[i], paths[j]) for i in range(6) for j in range(i + 1, 6)
        ][:12]
        edges += [(paths[i], f"external:mystery{i}") for i in range(6, 30)]
        repo = build_repo(paths, entries={"src/m0.py"}, edges=edges)
        kg = _curate(repo, enabled=True)
        unreached_reasons = [
            s["reason"] for s in kg.tour if "import resolution is incomplete" in s["reason"]
        ]
        assert unreached_reasons, [s["reason"] for s in kg.tour]
        assert not any("standalone or supporting" in s["reason"] for s in kg.tour)

    def test_small_repo_density_is_not_evidence(self):
        # 7 healthy python files (mini-taskq shape): low density on a tiny
        # repo must not demote a full-support language out of flow mode.
        paths = [f"src/m{i}.py" for i in range(6)] + ["tests/test_m.py"]
        edges = [("src/m0.py", "src/m1.py")]
        repo = build_repo(paths, entries={"src/m0.py"}, edges=edges, tests={"tests/test_m.py"})
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "flow"


class TestClosingStopHarnessExclusion:
    def test_shared_harness_never_faces_the_suite(self):
        # okio/Alamofire/scopt regression: the base class or helper every
        # test imports (AbstractFileSystemTest, BaseTestCase, SpecUtil)
        # carries the suite's highest pagerank — but it is what the suite
        # runs ON, not where tests start.
        repo = build_repo(
            [
                "src/app.py",
                "src/core.py",
                "tests/harness.py",
                "tests/test_one.py",
                "tests/test_two.py",
            ],
            entries={"src/app.py"},
            edges=[
                ("src/app.py", "src/core.py"),
                ("tests/test_one.py", "tests/harness.py"),
                ("tests/test_two.py", "tests/harness.py"),
            ],
            pagerank={"tests/harness.py": 0.9},
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] in ("tests/test_one.py", "tests/test_two.py")

    def test_single_importer_helper_excluded(self):
        # okio's CipherFactory.kt: even ONE test-file importer marks a
        # helper — leaf tests import helpers, nothing imports leaf tests.
        repo = build_repo(
            [
                "src/app.py",
                "tests/util.py",
                "tests/test_one.py",
            ],
            entries={"src/app.py"},
            edges=[("tests/test_one.py", "tests/util.py")],
            pagerank={"tests/util.py": 0.9},
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "tests/test_one.py"

    def test_test_project_dir_wins_over_shared_helpers(self):
        # Polly regression: test/Shared/TestCancellation.cs is shallower
        # than the .Specs project files, but the declared test-project
        # convention (.Tests/.Specs) is where the suite lives.
        repo = build_repo(
            [
                "src/Core/App.cs",
                "src/Core/Core.cs",
                "test/Shared/TestCancellation.cs",
                "test/Acme.Specs/RetrySpecs.cs",
            ],
            entries={"src/Core/App.cs"},
            edges=[("src/Core/App.cs", "src/Core/Core.cs")],
            pagerank={"test/Shared/TestCancellation.cs": 0.9},
        )
        kg = _curate(repo, enabled=True)
        closing = _closing_stops(kg)
        assert len(closing) == 1
        assert closing[0]["target_path"] == "test/Acme.Specs/RetrySpecs.cs"


class TestSupportDirExclusion:
    def test_doc_site_entries_never_surface(self):
        # libuv/Polly regression: docs/code/*/main.c snippets and docfx
        # template main.js flooded the entry surface and the whole tour.
        repo = build_repo(
            [
                "docs/code/spawn/main.c",
                "docs/template/public/main.js",
                "website/theme/index.ts",
                "src/core.c",
                "src/run.c",
            ],
            entries={
                "docs/code/spawn/main.c",
                "docs/template/public/main.js",
                "website/theme/index.ts",
            },
            edges=[("src/run.c", "src/core.c")],
        )
        kg = _curate(repo, enabled=True)
        entries = kg.project.get("entry_points", [])
        assert all(not e.startswith(("docs/", "website/")) for e in entries)
        tour_paths = {s["target_path"] for s in (kg.tour or [])}
        assert not any(p.startswith(("docs/", "website/")) for p in tour_paths)


class TestFanoutCollapse:
    def _gb(self):
        from types import SimpleNamespace

        import networkx as nx

        g = nx.DiGraph()
        # One Go-style package import fanned out to 3 sibling targets…
        for t in ("pkg/a.py", "pkg/b.py", "pkg/c.py"):
            g.add_edge("tests/test_x.py", t, edge_type="imports", imported_names=["pkg"])
        # …and one explicit single-target import.
        g.add_edge(
            "tests/test_x.py", "tests/harness.py",
            edge_type="imports", imported_names=["Harness"],
        )
        g.add_edge(
            "tests/test_y.py", "tests/harness.py",
            edge_type="imports", imported_names=["Harness"],
        )
        return SimpleNamespace(graph=lambda: g)

    def test_fanout_groups_excluded_from_pairs(self):
        from repowise.core.analysis.kg_curation import _import_pairs_excluding_fanout

        pairs = set(_import_pairs_excluding_fanout(self._gb()))
        # chi regression: a package fan-out is not evidence that each
        # sibling file is individually referenced.
        assert ("tests/test_x.py", "pkg/a.py") not in pairs
        assert ("tests/test_x.py", "tests/harness.py") in pairs
        assert ("tests/test_y.py", "tests/harness.py") in pairs

    def test_anchor_rank_counts_relationships_not_edges(self):
        from repowise.core.analysis.kg_curation import _anchor_fanout_rank

        rank = _anchor_fanout_rank(self._gb())
        # 4 raw out-edges from test_x, but only 2 import relationships
        # (the fan-out collapses to one).
        assert rank["tests/test_x.py"] == 2
        assert rank["tests/test_y.py"] == 1


class TestPartialTierGraphMode:
    """Regex-tier (partial) languages run flow/sparse per REAL density and
    resolution — partial support alone no longer pins sparse."""

    def test_well_resolved_partial_repo_flows(self):
        # 30 elixir files, thin but cleanly-resolved alias chain → flow:
        # blaming "incomplete import resolution" at 0.97 resolution is a lie.
        paths = [f"lib/m{i}.ex" for i in range(30)]
        edges = [(paths[i], paths[i + 1]) for i in range(29)]
        edges += [(paths[0], "external:Phoenix")]
        repo = build_repo(paths, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "flow"

    def test_weakly_resolved_partial_repo_stays_sparse(self):
        paths = [f"lib/m{i}.ex" for i in range(30)]
        edges = [(paths[i], paths[i + 1]) for i in range(9)]
        edges += [(paths[i], f"external:Dep{i}") for i in range(10, 30)]
        repo = build_repo(paths, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "sparse"

    def test_small_partial_repo_with_clean_resolution_flows(self):
        # Density is unmeasurable on tiny repos but resolution is not: a
        # 24-file Elixir repo resolving 0.9 of its aliases must not have
        # its tour blame "incomplete import resolution" for being small.
        paths = [f"lib/m{i}.ex" for i in range(8)]
        edges = [(paths[i], paths[i + 1]) for i in range(7)]
        repo = build_repo(paths, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "flow"

    def test_small_partial_repo_with_weak_resolution_stays_sparse(self):
        paths = [f"lib/m{i}.ex" for i in range(8)]
        edges = [(paths[0], paths[1])]
        edges += [(paths[i], f"external:Dep{i}") for i in range(2, 8)]
        repo = build_repo(paths, edges=edges)
        kg = _curate(repo, enabled=True)
        assert kg.project["graph_mode"] == "sparse"
