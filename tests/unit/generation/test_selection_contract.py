"""Selection-contract tests, plus the synthetic fixtures its siblings share.

Nothing below the concept tree costs tokens, so selection rations nothing:
every candidate that clears its bucket's floor gets a page. These tests pin
that, and pin the two properties that follow from it: the floor still keeps
tests and pure re-export modules out, and selection does not depend on whether
an API key is present.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import (
    FILE_PAGE_ASK_THRESHOLD,
    FILE_PAGE_AUTO_CEILING,
    SelectionInputs,
    auto_file_page_cap,
    count_documentable_files,
    recommended_file_page_cap,
    select_pages,
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


def _build_synthetic_repo(
    n_files: int, *, entry_points: int = 0
) -> tuple[list[FakeParsedFile], dict, dict, dict]:
    """Return ``(parsed_files, pagerank, betweenness, community)``.

    PageRank tapers linearly so the top file has 1.0 and the bottom
    file has ~0.01. Community assignment buckets every 25 files into
    one community. The first ``entry_points`` files are flagged as
    entry points but given LOW PageRank so they would normally fall
    outside the budget — useful for landmark pull-in tests.
    """
    parsed: list[FakeParsedFile] = []
    pagerank: dict[str, float] = {}
    betweenness: dict[str, float] = {}
    community: dict[str, int] = {}

    for i in range(n_files):
        path = f"pkg{i // 25}/module_{i}.py"
        # Flag the LAST `entry_points` files (lowest PageRank) as entry points.
        is_ep = i >= n_files - entry_points if entry_points else False
        fi = FakeFileInfo(path=path, is_entry_point=is_ep)
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
# Nothing is rationed
# ---------------------------------------------------------------------------


def _inputs(parsed, pagerank, betweenness, community, cfg):
    return SelectionInputs(
        parsed_files=parsed,
        pagerank=pagerank,
        betweenness=betweenness,
        community=community,
        community_info=None,
        sccs=[],
        git_meta_map=None,
        config=cfg,
    )


def test_every_production_file_gets_a_page():
    """No budget, so the file bucket is the whole floored candidate set."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(400)
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    assert len(sel.file_page_paths) == 400
    assert set(sel.file_page_paths) == {p.file_info.path for p in parsed}


def test_importance_floor_excludes_tests_and_reexports():
    """The measured floor survives the tail it used to belong to.

    Test files and pure ``__init__.py`` re-exports were proven to dilute
    retrieval, so they get no page even though nothing is rationed any more.
    """
    parsed, pagerank, betweenness, community = _build_synthetic_repo(6)
    for extra in ("tests/test_thing.py", "pkg0/sub/tests/test_more.py", "pkg0/__init__.py"):
        parsed.append(FakeParsedFile(file_info=FakeFileInfo(path=extra), symbols=[]))
        pagerank[extra] = 1.0
        betweenness[extra] = 0.0
        community[extra] = 0

    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))

    assert "tests/test_thing.py" not in sel.file_page_paths
    assert "pkg0/sub/tests/test_more.py" not in sel.file_page_paths
    assert "pkg0/__init__.py" not in sel.file_page_paths
    assert len(sel.file_page_paths) == 6


def test_selection_does_not_depend_on_having_a_key():
    """Keyed and keyless runs select exactly the same pages.

    ``deterministic`` decides how much the synthesis pages say, never which
    pages exist. This is the property that lets a keyed and a keyless index of
    the same commit share a byte-identical file layer, so it is asserted on the
    selection rather than left to the renderer.
    """
    parsed, pagerank, betweenness, community = _build_synthetic_repo(120)
    keyed = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(deterministic=False))
    )
    keyless = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(deterministic=True))
    )
    assert keyed.counts() == keyless.counts()
    assert keyed.file_page_paths == keyless.file_page_paths
    assert keyed.symbol_spotlights == keyless.symbol_spotlights


def test_spotlights_are_bounded_by_the_percentile():
    """The bucket the budget share used to bound is bounded by the percentile."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(100)
    total_symbols = sum(len(p.symbols) for p in parsed)

    half = select_pages(
        _inputs(
            parsed, pagerank, betweenness, community, GenerationConfig(top_symbol_percentile=0.5)
        )
    )
    tenth = select_pages(
        _inputs(
            parsed, pagerank, betweenness, community, GenerationConfig(top_symbol_percentile=0.1)
        )
    )

    assert len(half.symbol_spotlights) == int(total_symbols * 0.5)
    assert len(tenth.symbol_spotlights) == int(total_symbols * 0.1)
    # Highest-scoring first, so the smaller set is a prefix of the larger.
    assert tenth.symbol_spotlights == half.symbol_spotlights[: len(tenth.symbol_spotlights)]


def test_default_config_takes_the_top_symbol_decile():
    """The stock default is the bound large repos actually get.

    A 5.3k-file monorepo produced 4,996 spotlights inside a 14,027-page wiki at
    the old 0.20. The bucket restates what each symbol's file page already says,
    so the default keeps the strongest decile.
    """
    parsed, pagerank, betweenness, community = _build_synthetic_repo(100)
    total_symbols = sum(len(p.symbols) for p in parsed)

    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))

    assert len(sel.symbol_spotlights) == int(total_symbols * 0.10)


def test_small_repo_still_gets_a_spotlight_at_the_default():
    """The floor of one keeps a tiny repo from losing the bucket entirely."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(1)
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    assert len(sel.symbol_spotlights) >= 1


def test_module_bucket_is_never_rationed():
    """The concept partition is total, so every group is emitted."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(300)
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    covered = {f for g in sel.module_groups for f in g.file_paths}
    assert covered == {p.file_info.path for p in parsed}


def test_empty_repo_emits_no_content_pages():
    sel = select_pages(_inputs([], {}, {}, {}, GenerationConfig()))
    assert sel.file_page_paths == []
    assert sel.symbol_spotlights == []
    assert sel.module_groups == []


# ---------------------------------------------------------------------------
# The one opt-in bound: max_file_pages
# ---------------------------------------------------------------------------


def test_file_bucket_is_unset_by_default():
    """Unset means the size policy decides, not that a cap was chosen."""
    assert GenerationConfig().max_file_pages is None


def test_cap_takes_the_strongest_file_pages():
    """The cap slices the ranking the selector already computed."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(400)
    cfg = GenerationConfig(max_file_pages=50)

    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))
    uncapped = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))

    assert len(sel.file_page_paths) == 50
    # Same ranking, just cut short: the kept pages are the uncapped run's first 50.
    assert sel.file_page_paths == uncapped.file_page_paths[:50]


def test_cap_above_the_candidate_count_changes_nothing():
    """A repo smaller than its cap is untouched by it."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(30)
    capped = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=2000))
    )
    assert len(capped.file_page_paths) == 30


def test_cap_leaves_the_other_buckets_alone():
    """Bounding the file layer is not a request to shrink the concept tree."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(300)
    capped = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=25))
    )
    uncapped = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    assert capped.counts()["file_page"] == 25
    assert capped.counts()["module_page"] == uncapped.counts()["module_page"]
    assert capped.counts()["symbol_spotlight"] == uncapped.counts()["symbol_spotlight"]


def test_cap_is_deterministic():
    """Same inputs, same cut — the sort is total, so the tie-break is the path."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(200)
    cfg = GenerationConfig(max_file_pages=40)
    first = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))
    second = select_pages(_inputs(list(reversed(parsed)), pagerank, betweenness, community, cfg))
    assert first.file_page_paths == second.file_page_paths


# ---------------------------------------------------------------------------
# The size policy: what an unset max_file_pages resolves to
# ---------------------------------------------------------------------------


def test_policy_leaves_normal_repos_alone():
    """A 2,500-file repo would lose 500 pages and gain nothing measured."""
    assert auto_file_page_cap(0) is None
    assert auto_file_page_cap(FILE_PAGE_ASK_THRESHOLD + 500) is None
    assert auto_file_page_cap(FILE_PAGE_AUTO_CEILING) is None


def test_policy_holds_huge_repos_at_the_ceiling():
    assert auto_file_page_cap(FILE_PAGE_AUTO_CEILING + 1) == FILE_PAGE_AUTO_CEILING
    assert auto_file_page_cap(15_000) == FILE_PAGE_AUTO_CEILING


def test_recommendation_never_contradicts_the_policy():
    """Whatever the question recommends, the policy must not override it."""
    for n in (0, 500, 2_000, 2_001, 4_000, 4_500, 4_501, 9_000, 50_000):
        recommended = recommended_file_page_cap(n)
        policy = auto_file_page_cap(n)
        if policy is not None:
            assert recommended == policy, n
        elif recommended is not None:
            assert recommended <= n, n


def test_unset_config_applies_the_policy_to_a_huge_repo():
    parsed, pagerank, betweenness, community = _build_synthetic_repo(FILE_PAGE_AUTO_CEILING + 200)
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    assert len(sel.file_page_paths) == FILE_PAGE_AUTO_CEILING


def test_unset_config_leaves_a_midsize_repo_whole():
    """Just under the ceiling: every eligible file still gets a page."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(FILE_PAGE_AUTO_CEILING - 100)
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, GenerationConfig()))
    assert len(sel.file_page_paths) == FILE_PAGE_AUTO_CEILING - 100


def test_zero_refuses_the_policy():
    """An explicit 0 means every eligible file, on a repo the policy would cap."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(FILE_PAGE_AUTO_CEILING + 200)
    sel = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=0))
    )
    assert len(sel.file_page_paths) == FILE_PAGE_AUTO_CEILING + 200


def test_explicit_cap_beats_the_policy_in_both_directions():
    parsed, pagerank, betweenness, community = _build_synthetic_repo(FILE_PAGE_AUTO_CEILING + 200)
    tighter = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=100))
    )
    assert len(tighter.file_page_paths) == 100
    looser = select_pages(
        _inputs(
            parsed,
            pagerank,
            betweenness,
            community,
            GenerationConfig(max_file_pages=FILE_PAGE_AUTO_CEILING + 100),
        )
    )
    assert len(looser.file_page_paths) == FILE_PAGE_AUTO_CEILING + 100


def test_count_documentable_files_matches_the_floor():
    """The count a caller reports with is the count selection acts on."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(20)
    for extra in ("tests/test_thing.py", "pkg0/__init__.py"):
        parsed.append(FakeParsedFile(file_info=FakeFileInfo(path=extra), symbols=[]))
        pagerank[extra] = 1.0
        betweenness[extra] = 0.0
        community[extra] = 0
    assert count_documentable_files(parsed) == 20
