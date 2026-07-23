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
    SelectionInputs,
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
