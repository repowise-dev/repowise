"""Phase 7 — KG intuitiveness invariants locked across structurally different
repos (many-isolates regression, flat single-package, deep monorepo) plus the
portable artifact and the AST-untouched guard."""

from __future__ import annotations

import pytest

from repowise.core.analysis.kg_curation import build_portable_kg, validate_kg

from .kg_fixtures import build_repo, build_skeleton, curate

# ---------------------------------------------------------------------------
# Structurally different repos
# ---------------------------------------------------------------------------


@pytest.fixture
def many_isolates_repo():
    """Many weakly-connected files — the historical 103-layers / 73-singletons
    pathology. Curated layers must collapse to a bounded named set."""
    paths: list[str] = []
    for layer_dir in ("api", "services", "models", "ui", "utils", "config"):
        paths += [f"pkg{layer_dir}/{layer_dir}/f{i}.py" for i in range(14)]
    tests = {f"tests/test_{i}.py" for i in range(14)}
    paths += sorted(tests)
    return build_repo(paths, tests=tests)  # no edges → every file an isolate


@pytest.fixture
def flat_repo():
    """A single flat package — few layers, but must stay partitioned/valid."""
    return build_repo([f"src/mod{i}.py" for i in range(40)])


@pytest.fixture
def deep_monorepo():
    """A realistically layered monorepo with two mega-layers."""
    paths: list[str] = []
    for sub in ("ingestion", "analysis", "generation"):
        paths += [f"packages/core/src/repowise/core/{sub}/m{i}.py" for i in range(24)]
    for sub in ("buttons", "forms", "layout"):
        paths += [f"packages/ui/src/components/{sub}/c{i}.tsx" for i in range(24)]
    paths += [f"packages/cli/src/cli/commands/cmd{i}.py" for i in range(20)]
    paths += [f"src/api/r{i}.py" for i in range(12)]
    paths += [f"src/models/m{i}.py" for i in range(10)]
    paths += [f"src/utils/u{i}.py" for i in range(8)]
    paths += [f"config/c{i}.yaml" for i in range(6)]
    tests = {f"tests/unit/test_{i}.py" for i in range(30)}
    paths += sorted(tests)
    return build_repo(paths, tests=tests)


ALL_REPOS = ["many_isolates_repo", "flat_repo", "deep_monorepo"]


@pytest.mark.parametrize("repo_fixture", ALL_REPOS)
class TestInvariantsAcrossRepos:
    def test_layer_count_never_explodes(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        assert len(kg.layers) <= 15  # the 103→bounded guarantee

    def test_partition_holds(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        v = validate_kg(kg)
        assert "partition" not in " ".join(v.errors)
        seen: set[str] = set()
        for layer in kg.layers:
            for nid in layer["nodeIds"]:
                assert nid not in seen
                seen.add(nid)
        file_count = sum(1 for n in kg.nodes if n["id"].startswith("file:"))
        assert len(seen) == file_count

    def test_no_empty_summaries(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        assert all(n["summary"] for n in kg.nodes if n["id"].startswith("file:"))

    def test_entry_points_capped(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        assert len(kg.project.get("entry_points", [])) <= 8

    def test_tour_within_budget_and_opens_overview(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        assert len(kg.tour) <= 12
        if kg.tour:
            assert kg.tour[0]["kind"] == "overview"

    def test_no_hard_validation_errors(self, repo_fixture, request):
        kg = curate(request.getfixturevalue(repo_fixture))
        v = validate_kg(kg)
        assert v.ok, v.errors

    def test_deterministic(self, repo_fixture, request):
        a = curate(request.getfixturevalue(repo_fixture))
        b = curate(request.getfixturevalue(repo_fixture))
        assert a.layers == b.layers
        assert a.tour == b.tour
        assert a.project.get("entry_points") == b.project.get("entry_points")

    def test_ast_graph_untouched(self, repo_fixture, request):
        repo = request.getfixturevalue(repo_fixture)
        g = repo.builder.graph()
        before = (g.number_of_nodes(), g.number_of_edges())
        curate(repo)
        g = repo.builder.graph()
        assert (g.number_of_nodes(), g.number_of_edges()) == before


class TestManyIsolatesRegression:
    def test_does_not_produce_one_layer_per_file(self, many_isolates_repo):
        # Skeleton (community) layers = one per file (the pathology).
        skel = build_skeleton(many_isolates_repo)
        file_count = sum(1 for n in skel.nodes if n["id"].startswith("file:"))
        assert len(skel.layers) == file_count
        # Curated layers collapse to a bounded named set.
        kg = curate(many_isolates_repo)
        assert len(kg.layers) <= 15
        assert len(kg.layers) < file_count


# ---------------------------------------------------------------------------
# Portable artifact (Phase 6)
# ---------------------------------------------------------------------------


class TestPortableArtifact:
    def test_self_contained_and_validated(self, deep_monorepo):
        kg = curate(deep_monorepo)
        data, validation = build_portable_kg(kg)
        for key in ("version", "project", "nodes", "edges", "layers", "tour", "meta"):
            assert key in data
        assert data["meta"]["validation"]["ok"] is validation.ok
        assert data["meta"]["layer_count"] == len(kg.layers)
        assert validation.ok, validation.errors

    def test_default_to_dict_has_no_meta(self, deep_monorepo):
        # The bare export stays byte-identical-shaped (no meta leakage).
        kg = curate(deep_monorepo)
        assert "meta" not in kg.to_dict()

    def test_metrics_block_populated(self, deep_monorepo):
        kg = curate(deep_monorepo)
        m = validate_kg(kg).metrics
        assert m["layer_count"] >= 6
        assert m["summary_completeness_pct"] == 100.0
        assert 0 <= m["largest_layer_pct"] <= 35
