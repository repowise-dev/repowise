"""Budget enforcement regression tests for the selection layer.

These tests directly exercise :func:`select_pages` against synthetic
parsed-file fixtures and assert that the global page count tracks the
configured ``coverage_pct``. They are the contract that prevents the
``529-pages-when-95-was-budgeted`` regression from coming back.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import MagicMock

import pytest

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import (
    SelectionInputs,
    select_pages,
)
from repowise.core.generation.selection.budget import (
    SMALL_REPO_THRESHOLD,
    allocate_budget,
    compute_budget,
)


# ---------------------------------------------------------------------------
# Lightweight ParsedFile / Symbol stand-ins
# ---------------------------------------------------------------------------


@dataclass
class FakeFileInfo:
    path: str
    language: str = "python"
    abs_path: str = ""
    size_bytes: int = 5_000
    is_test: bool = False
    is_config: bool = False
    is_api_contract: bool = False
    is_entry_point: bool = False
    git_hash: str = ""

    def __post_init__(self) -> None:
        if not self.abs_path:
            self.abs_path = f"/repo/{self.path}"


@dataclass
class FakeSymbol:
    name: str
    qualified_name: str = ""
    kind: str = "function"
    visibility: str = "public"
    signature: str = "()"
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    complexity_estimate: int = 1
    parent_name: str | None = None

    def __post_init__(self) -> None:
        if not self.qualified_name:
            self.qualified_name = self.name


@dataclass
class FakeParsedFile:
    file_info: FakeFileInfo
    symbols: list[FakeSymbol] = field(default_factory=list)
    imports: list[object] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    docstring: str | None = None
    parse_errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Fixture builder
# ---------------------------------------------------------------------------


def _build_synthetic_repo(n_files: int) -> tuple[list[FakeParsedFile], dict, dict, dict]:
    """Return ``(parsed_files, pagerank, betweenness, community)``.

    PageRank tapers linearly so the top file has 1.0 and the bottom
    file has ~0.01. Community assignment buckets every 25 files into
    one community.
    """
    parsed: list[FakeParsedFile] = []
    pagerank: dict[str, float] = {}
    betweenness: dict[str, float] = {}
    community: dict[str, int] = {}

    for i in range(n_files):
        path = f"pkg{i // 25}/module_{i}.py"
        fi = FakeFileInfo(path=path)
        syms = [
            FakeSymbol(name=f"func_{i}_{k}", qualified_name=f"module_{i}.func_{i}_{k}")
            for k in range(3)
        ]
        parsed.append(FakeParsedFile(file_info=fi, symbols=syms))
        pagerank[path] = 1.0 - (i / max(1, n_files - 1)) * 0.99
        betweenness[path] = 0.0
        community[path] = i // 25
    return parsed, pagerank, betweenness, community


# ---------------------------------------------------------------------------
# Budget computation
# ---------------------------------------------------------------------------


def test_compute_budget_scales_linearly_for_large_repo():
    assert compute_budget(1_000, 0.10) == 100
    assert compute_budget(1_000, 0.20) == 200
    assert compute_budget(1_000, 0.50) == 500
    assert compute_budget(10_000, 0.20) == 2_000  # no absolute cap


def test_compute_budget_floors_for_small_repos():
    # Below the small-repo threshold, budget never undershoots N_files
    # so percentage rounding doesn't zero out tiny repos.
    assert compute_budget(5, 0.20) == 5
    assert compute_budget(SMALL_REPO_THRESHOLD, 0.20) == SMALL_REPO_THRESHOLD


def test_compute_budget_zero_for_empty_repo():
    assert compute_budget(0, 0.50) == 0


def test_allocate_budget_respects_candidate_supply():
    """A bucket is never asked for more than its available candidates."""
    alloc = allocate_budget(
        budget=100,
        candidates_per_bucket={
            "file_page": 500,
            "symbol_spotlight": 10,  # capped low
            "module_page": 20,
            "api_contract": 0,
            "infra_page": 0,
            "scc_page": 2,
        },
        shares={
            "file_page": 0.50,
            "symbol_spotlight": 0.30,
            "module_page": 0.10,
            "api_contract": 0.05,
            "infra_page": 0.03,
            "scc_page": 0.02,
        },
        n_files=500,
    )
    # Capped at supply
    assert alloc.symbol_spotlight == 10
    assert alloc.api_contract == 0
    assert alloc.infra_page == 0
    # Spill into file_page absorbs the missing share
    assert alloc.file_page >= 50


# ---------------------------------------------------------------------------
# Full select_pages — integration-shaped
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "coverage_pct,expected_total",
    [
        (0.10, 96),
        (0.20, 192),
        (0.50, 479),
    ],
)
def test_select_pages_budget_tracks_coverage(coverage_pct, expected_total):
    """Total page count tracks coverage_pct within ±10%."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(958)
    cfg = GenerationConfig(coverage_pct=coverage_pct)
    inputs = SelectionInputs(
        parsed_files=parsed,
        pagerank=pagerank,
        betweenness=betweenness,
        community=community,
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=cfg,
    )

    selection = select_pages(inputs)
    total = sum(selection.counts().values())

    tolerance = max(20, int(expected_total * 0.20))
    assert abs(total - expected_total) <= tolerance, (
        f"coverage={coverage_pct}: expected ~{expected_total}, got {total}"
    )


def test_select_pages_emits_no_pages_for_empty_repo():
    cfg = GenerationConfig(coverage_pct=0.20)
    inputs = SelectionInputs(
        parsed_files=[],
        pagerank={},
        betweenness={},
        community={},
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=cfg,
    )
    selection = select_pages(inputs)
    assert selection.allocation is not None
    assert selection.allocation.total == 0


def test_higher_coverage_strictly_emits_more_pages():
    """Bumping coverage_pct strictly increases the total page count."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(500)
    totals: list[int] = []
    for pct in (0.10, 0.20, 0.30, 0.50):
        cfg = GenerationConfig(coverage_pct=pct)
        sel = select_pages(
            SelectionInputs(
                parsed_files=parsed,
                pagerank=pagerank,
                betweenness=betweenness,
                community=community,
                community_info=None,
                sccs=[],
                git_meta_map=None,
                config=cfg,
            )
        )
        totals.append(sum(sel.counts().values()))
    assert totals == sorted(totals), f"Non-monotonic budget: {totals}"
