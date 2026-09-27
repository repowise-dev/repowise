"""Folder-specific documentation floors (issue #633).

Raghav's spec: a floor, not a cap. ``((glob, pct), ...)`` rules promise at
least ``pct`` of the code files under each glob get a file_page, whatever
their global importance score, unioned additively into the selection so the
cost table stays truthful about what the user asked for.

Key property pinned here: a folder pinned at 1.0 gets full file-page
coverage even when its files score below the global cutoff (and therefore
below the cap the folder rule must override).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import SelectionInputs, select_pages


@dataclass
class _FakeFileInfo:
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
class _FakeSymbol:
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
class _FakeParsedFile:
    file_info: _FakeFileInfo
    symbols: list[_FakeSymbol] = field(default_factory=list)
    imports: list[object] = field(default_factory=list)
    exports: list[str] = field(default_factory=list)
    docstring: str | None = None
    parse_errors: list[str] = field(default_factory=list)


def _build_synthetic_repo(
    n_files: int,
) -> tuple[list[_FakeParsedFile], dict[str, float], dict[str, float], dict[str, int]]:
    """PageRank tapers linearly (top 1.0, bottom ~0.01); every 25 files share
    a ``pkg{i//25}`` prefix. Each file carries three public symbols so it
    clears the score floor (same shape as test_selection_contract's fixture)."""
    parsed: list[_FakeParsedFile] = []
    pagerank: dict[str, float] = {}
    betweenness: dict[str, float] = {}
    community: dict[str, int] = {}
    for i in range(n_files):
        path = f"pkg{i // 25}/module_{i}.py"
        syms = [
            _FakeSymbol(name=f"func_{i}_{k}", qualified_name=f"module_{i}.func_{i}_{k}")
            for k in range(3)
        ]
        parsed.append(_FakeParsedFile(file_info=_FakeFileInfo(path=path), symbols=syms))
        pagerank[path] = 1.0 - (i / max(1, n_files - 1)) * 0.99
        betweenness[path] = 0.0
        community[path] = i // 25
    return parsed, pagerank, betweenness, community


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


def test_folder_pinned_at_1_0_covers_every_code_file_under_the_glob() -> None:
    """Files below the global cutoff must still be pinnable (the floor)."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(50)
    # Force a tight global cap so most files fall outside it.
    cfg = GenerationConfig(max_file_pages=10, folder_coverage=(("pkg0", 1.0),))
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))

    assert len(sel.file_page_paths) >= 10  # global pick remains
    # pkg0 holds files 0..24 (i // 25 == 0); all of them must be present.
    pinned = {p for p in sel.file_page_paths if p.startswith("pkg0/")}
    all_pkg0 = {p.file_info.path for p in parsed if p.file_info.path.startswith("pkg0/")}
    assert pinned == all_pkg0, (
        "a 1.0 folder floor must pin every code file under the glob, "
        "including those below the global cutoff"
    )


def test_folder_floor_is_additive_not_displacing() -> None:
    """The union never removes a global pick — the floor only adds pages."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(50)
    cfg = GenerationConfig(max_file_pages=10, folder_coverage=(("pkg0", 1.0),))
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))

    unpinned = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=10))
    )
    global_picks = set(unpinned.file_page_paths)
    assert global_picks <= set(sel.file_page_paths), (
        "folder pins must be additive — global picks stay select()ed"
    )


def test_partial_folder_floor_takes_ceil_pct_highest_scored() -> None:
    parsed, pagerank, betweenness, community = _build_synthetic_repo(80)
    cfg = GenerationConfig(max_file_pages=5, folder_coverage=(("pkg1", 0.5),))
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))

    # pkg1 holds files 25..49 (i // 25 == 1) → 25 files, half = ceil(12.5) = 13.
    pkg1 = {p.file_info.path for p in parsed if p.file_info.path.startswith("pkg1/")}
    got = {p for p in sel.file_page_paths if p in pkg1}
    assert len(got) == 13

    # The pinned 13 must be the highest-scored of pkg1 (PageRank tapers, so
    # the top 13 by pagerank, tie-broken by path).
    ranked = sorted(pkg1, key=lambda p: (-pagerank[p], p))
    assert got == set(ranked[:13])


def test_empty_rules_and_zero_pct_are_no_ops() -> None:
    parsed, pagerank, betweenness, community = _build_synthetic_repo(30)
    cfg = GenerationConfig(
        max_file_pages=5, folder_coverage=(("pkg0", 0.0), ("no/such/dir", 1.0))
    )
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))
    unpinned = select_pages(
        _inputs(parsed, pagerank, betweenness, community, GenerationConfig(max_file_pages=5))
    )
    assert sel.file_page_paths == unpinned.file_page_paths


def test_folder_rule_glob_shape_matches_subtree() -> None:
    """``src/core`` (no glob metachars) matches the dir and everything under it."""
    parsed, pagerank, betweenness, community = _build_synthetic_repo(30)
    # Rename a slice of the synthetic repo into a subtree.
    for p in parsed:
        if p.file_info.path.startswith("pkg1/"):
            p.file_info.path = "src/core/" + p.file_info.path.removeprefix("pkg1/")
    pagerank = {("src/core/" + k.removeprefix("pkg1/")) if k.startswith("pkg1/") else k: v for k, v in pagerank.items()}

    cfg = GenerationConfig(max_file_pages=5, folder_coverage=(("src/core", 1.0),))
    sel = select_pages(_inputs(parsed, pagerank, betweenness, community, cfg))

    under_core = {p.file_info.path for p in parsed if p.file_info.path.startswith("src/core/")}
    got = {p for p in sel.file_page_paths if p.startswith("src/core/")}
    assert got == under_core


def test_config_yaml_parse_accepts_glob_equals_pct_strings() -> None:
    cfg = GenerationConfig.from_repo_config(
        {"folder_coverage": ["src/core=1.0", 'src/other="0.5"']}
    )
    assert cfg.folder_coverage == (("src/core", 1.0), ("src/other", 0.5))


def test_config_yaml_parse_rejects_bad_rules() -> None:
    import pytest

    with pytest.raises(ValueError, match="folder_coverage must be a list"):
        GenerationConfig.from_repo_config({"folder_coverage": "src/core=1.0"})
    with pytest.raises(ValueError, match="not a number"):
        GenerationConfig.from_repo_config({"folder_coverage": ["src/core=high"]})
    with pytest.raises(ValueError, match=r"pct must be in \[0, 1\]"):
        GenerationConfig.from_repo_config({"folder_coverage": ["src/core=1.5"]})
