"""Curated module grouping in the selection layer.

Covers the ``module_grouping="curated"`` source (key = real dir path,
display = curated name, cohesion = None, Σ-PageRank ranking), every row of
the fallback matrix, and the golden inertness contract: with the default
``"community"`` grouping, passing ``kg_modules`` changes NOTHING — the
ship-dark guarantee that every existing path stays byte-identical.
"""

from __future__ import annotations

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import SelectionInputs, select_pages
from repowise.core.generation.selection.selector import _build_module_groups
from tests.unit.generation.test_selection_budget import (
    FakeFileInfo,
    FakeParsedFile,
    FakeSymbol,
    _build_synthetic_repo,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _repo_with_modules():
    """A synthetic repo plus a matching curated-modules artifact."""
    paths = (
        [f"packages/app/src/core/ingestion/f{i}.py" for i in range(8)]
        + [f"packages/app/src/core/analysis/f{i}.py" for i in range(6)]
        + [f"packages/app/src/ui/c4/f{i}.py" for i in range(5)]
        + ["packages/app/src/ui/tiny/f0.py", "packages/app/src/ui/tiny/f1.py"]
    )
    parsed = [
        FakeParsedFile(file_info=FakeFileInfo(path=p), symbols=[FakeSymbol(name="fn")])
        for p in paths
    ]
    # ui/c4 members carry the highest PageRank so Σ-PageRank must rank that
    # module first despite it being smaller than core/ingestion.
    pagerank = {p: (1.0 if "/ui/c4/" in p else 0.1) for p in paths}
    community = {p: (0 if "/core/" in p else 1) for p in paths}

    modules = [
        {
            "id": "module:packages-app-src-core-ingestion",
            "name": "core/ingestion",
            "path": "packages/app/src/core/ingestion",
            "layerId": "layer:service",
            "nodeIds": [f"file:{p}" for p in paths if "/core/ingestion/" in p],
            "language": "python",
        },
        {
            "id": "module:packages-app-src-core-analysis",
            "name": "core/analysis",
            "path": "packages/app/src/core/analysis",
            "layerId": "layer:service",
            "nodeIds": [f"file:{p}" for p in paths if "/core/analysis/" in p],
            "language": "python",
        },
        {
            "id": "module:packages-app-src-ui-c4",
            "name": "ui/c4",
            "path": "packages/app/src/ui/c4",
            "layerId": "layer:ui",
            "nodeIds": [f"file:{p}" for p in paths if "/ui/c4/" in p],
            "language": "python",
        },
        {
            # Below min_module_size=3 → must be skipped, never a page.
            "id": "module:packages-app-src-ui-tiny",
            "name": "ui/tiny",
            "path": "packages/app/src/ui/tiny",
            "layerId": "layer:ui",
            "nodeIds": [f"file:{p}" for p in paths if "/ui/tiny/" in p],
            "language": "python",
        },
    ]
    return parsed, pagerank, community, modules


def _inputs(parsed, pagerank, community, *, cfg=None, kg_modules=None):
    return SelectionInputs(
        parsed_files=parsed,
        pagerank=pagerank,
        betweenness=dict.fromkeys(pagerank, 0.0),
        community=community,
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=cfg or GenerationConfig(),
        kg_modules=kg_modules,
    )


def _cfg(grouping: str) -> GenerationConfig:
    return GenerationConfig(module_grouping=grouping)


# ---------------------------------------------------------------------------
# Curated grouping source
# ---------------------------------------------------------------------------


class TestCuratedGrouping:
    def test_groups_keyed_by_dir_path_with_curated_names(self):
        parsed, pr, comm, modules = _repo_with_modules()
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=modules)
        )
        by_key = {g.key: g for _, g in groups}
        assert "packages/app/src/core/ingestion" in by_key
        g = by_key["packages/app/src/core/ingestion"]
        assert g.display == "core/ingestion"
        assert g.label == "core/ingestion"
        assert g.cohesion is None
        assert g.language == "python"
        assert len(g.file_paths) == 8

    def test_ranked_by_sum_pagerank_of_members(self):
        parsed, pr, comm, modules = _repo_with_modules()
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=modules)
        )
        # ui/c4: 5 x 1.0 = 5.0 beats core/ingestion: 8 x 0.1 = 0.8.
        assert groups[0][1].display == "ui/c4"
        scores = [s for s, _ in groups]
        assert scores == sorted(scores, reverse=True)

    def test_min_module_size_floor_kills_tiny_modules(self):
        parsed, pr, comm, modules = _repo_with_modules()
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=modules)
        )
        assert "packages/app/src/ui/tiny" not in {g.key for _, g in groups}

    def test_members_restricted_to_parsed_code_files(self):
        parsed, pr, comm, modules = _repo_with_modules()
        modules[0]["nodeIds"].append("file:packages/app/src/core/ingestion/ghost.py")
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=modules)
        )
        by_key = {g.key: g for _, g in groups}
        assert not any(
            "ghost" in p
            for p in by_key["packages/app/src/core/ingestion"].file_paths
        )

    def test_no_community_ids_in_curated_keys(self):
        parsed, pr, comm, modules = _repo_with_modules()
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=modules)
        )
        assert not any(g.key.startswith("community-") for _, g in groups)


# ---------------------------------------------------------------------------
# Fallback matrix (one test per row)
# ---------------------------------------------------------------------------


class TestFallbackMatrix:
    def test_curated_without_artifact_falls_back_to_community(self):
        """curation off / degraded / no KG json → today's community path."""
        parsed, pr, _bet, comm = _build_synthetic_repo(100)
        curated_no_artifact = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=None)
        )
        community = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("community"))
        )
        assert curated_no_artifact == community

    def test_curated_with_all_modules_below_floor_emits_none_not_community(self):
        """A present-but-filtered artifact must not mix vocabularies."""
        parsed, pr, comm, modules = _repo_with_modules()
        tiny_only = [m for m in modules if m["name"] == "ui/tiny"]
        groups = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("curated"), kg_modules=tiny_only)
        )
        assert groups == []

    def test_explicit_community_honored_even_with_artifact(self):
        parsed, pr, comm, modules = _repo_with_modules()
        with_artifact = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("community"), kg_modules=modules)
        )
        without = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("community"))
        )
        assert with_artifact == without
        assert not any(g.key.startswith("packages/") for _, g in with_artifact)

    def test_explicit_top_dir_honored_even_with_artifact(self):
        parsed, pr, comm, modules = _repo_with_modules()
        with_artifact = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("top_dir"), kg_modules=modules)
        )
        without = _build_module_groups(
            _inputs(parsed, pr, comm, cfg=_cfg("top_dir"))
        )
        assert with_artifact == without
        assert {g.key for _, g in with_artifact} == {"packages"}


# ---------------------------------------------------------------------------
# Golden inertness — the ship-dark contract
# ---------------------------------------------------------------------------


class TestGoldenByteIdentity:
    def test_default_selection_identical_with_and_without_kg_modules(self):
        """With the default grouping, kg_modules must change NOTHING.

        This is the rollout guarantee: every phase lands dark, and every
        existing user/CI path produces an identical Selection until the
        default flips in its own one-line commit.
        """
        parsed, pr, bet, comm = _build_synthetic_repo(300)
        _, _, _, modules = _repo_with_modules()
        cfg = GenerationConfig()  # default module_grouping ("community")
        assert cfg.module_grouping == "community"

        def run(kg_modules):
            return select_pages(
                SelectionInputs(
                    parsed_files=parsed,
                    pagerank=pr,
                    betweenness=bet,
                    community=comm,
                    community_info=None,
                    sccs=[],
                    git_meta_map=None,
                    config=cfg,
                    kg_modules=kg_modules,
                )
            )

        assert run(None) == run(modules)

    def test_curated_selection_is_deterministic(self):
        parsed, pr, comm, modules = _repo_with_modules()
        cfg = _cfg("curated")

        def run():
            return select_pages(
                _inputs(parsed, pr, comm, cfg=cfg, kg_modules=modules)
            )

        a, b = run(), run()
        assert a.module_groups == b.module_groups
        assert a == b
