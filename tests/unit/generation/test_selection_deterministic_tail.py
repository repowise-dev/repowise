"""Deterministic coverage-tail tests for the selection layer (Phase G).

Contract: after the LLM budget picks its file pages, every REMAINING code file
gets a zero-LLM deterministic page (``Selection.deterministic_tail_paths``),
importance-floored (test files and pure ``__init__.py`` always excluded) and
optionally capped / dir-restricted. Disabling the tail reproduces prior output.
"""

from __future__ import annotations

from repowise.core.generation.models import GenerationConfig
from repowise.core.generation.selection import SelectionInputs, select_pages

from .test_selection_budget import (
    FakeFileInfo,
    FakeParsedFile,
    FakeSymbol,
    _build_synthetic_repo,
)


def _inputs(parsed, pagerank, betweenness, community, cfg) -> SelectionInputs:
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


def test_tail_covers_every_dropped_code_file():
    parsed, pr, bet, comm = _build_synthetic_repo(200)
    cfg = GenerationConfig(coverage_pct=0.10)  # ~20 file pages, ~180 dropped
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))

    selected = set(sel.file_page_paths)
    tail = set(sel.deterministic_tail_paths)
    # No overlap between the budgeted set and the tail.
    assert not (selected & tail)
    # Every code file is covered by exactly one of the two (no test/__init__
    # files in this synthetic repo, so the union is the full set).
    all_code = {p.file_info.path for p in parsed}
    assert selected | tail == all_code


def test_tail_floor_excludes_tests_and_init():
    parsed, pr, bet, comm = _build_synthetic_repo(30)
    # Inject a test file and an __init__.py that would otherwise land in the tail.
    for path in ("tests/test_thing.py", "pkg0/__init__.py", "src/tests/helper.py"):
        fi = FakeFileInfo(path=path)
        parsed.append(FakeParsedFile(file_info=fi, symbols=[FakeSymbol(name="x")]))
        pr[path] = 0.001
        bet[path] = 0.0
        comm[path] = 0
    cfg = GenerationConfig(coverage_pct=0.10)
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))

    tail = set(sel.deterministic_tail_paths)
    assert "tests/test_thing.py" not in tail
    assert "src/tests/helper.py" not in tail
    assert "pkg0/__init__.py" not in tail


def test_tail_disabled_yields_empty():
    parsed, pr, bet, comm = _build_synthetic_repo(100)
    cfg = GenerationConfig(coverage_pct=0.10, tier2_tail_enabled=False)
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))
    assert sel.deterministic_tail_paths == []


def test_tail_respects_cap_highest_signal_first():
    parsed, pr, bet, comm = _build_synthetic_repo(200)
    cfg = GenerationConfig(coverage_pct=0.10, tier2_tail_cap=25)
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))
    assert len(sel.deterministic_tail_paths) == 25
    # Capped tail should be the highest-PageRank dropped files (score-ordered).
    tail_prs = [pr[p] for p in sel.deterministic_tail_paths]
    assert tail_prs == sorted(tail_prs, reverse=True)


def test_tail_dir_restrict():
    parsed, pr, bet, comm = _build_synthetic_repo(100)
    cfg = GenerationConfig(coverage_pct=0.05, tier2_tail_dirs=("pkg1/",))
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))
    assert sel.deterministic_tail_paths  # non-empty
    assert all(p.startswith("pkg1/") for p in sel.deterministic_tail_paths)


def test_counts_excludes_tail():
    """counts() stays budget-only so cost estimation isn't inflated."""
    parsed, pr, bet, comm = _build_synthetic_repo(200)
    cfg = GenerationConfig(coverage_pct=0.10)
    sel = select_pages(_inputs(parsed, pr, bet, comm, cfg))
    assert "deterministic_tail" not in sel.counts()
    assert sel.deterministic_tail_paths  # but the tail is populated
