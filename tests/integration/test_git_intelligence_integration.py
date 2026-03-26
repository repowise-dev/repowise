"""Integration tests for Phase 5.5 git intelligence features.

Tests:
  1. GitIndexer gracefully handles a non-git sample repo (returns empty summary).
  2. GraphBuilder.add_co_change_edges adds co_changes edges from git metadata.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.ingestion import FileTraverser, ASTParser, GraphBuilder
from repowise.core.ingestion.git_indexer import GitIndexer, GitIndexSummary
from repowise.core.ingestion.models import (
    FileInfo,
    Import,
    ParsedFile,
    Symbol,
)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_git_indexer_on_non_git_dir(tmp_path: Path) -> None:
    """GitIndexer on a non-git directory should return an empty summary
    without raising an exception."""
    # Create a temp dir with a file so GitIndexer has something to look at,
    # but no .git directory so it should bail out gracefully.
    (tmp_path / "example.py").write_text("x = 1\n")
    indexer = GitIndexer(tmp_path)
    summary, metadata = await indexer.index_repo("test-repo")

    assert isinstance(summary, GitIndexSummary)
    assert summary.files_indexed == 0
    assert summary.hotspots == 0
    assert summary.stable_files == 0
    assert metadata == []


@pytest.mark.asyncio
async def test_co_change_edges_in_graph() -> None:
    """add_co_change_edges should insert co_changes edges into the graph
    when git metadata contains co_change_partners above the threshold."""

    # -- Build a small graph with three files and no import edges ----------
    builder = GraphBuilder()

    def _make_parsed(path: str) -> ParsedFile:
        return ParsedFile(
            file_info=FileInfo(
                path=path,
                abs_path=f"/fake/{path}",
                language="python",
                size_bytes=100,
                git_hash="",
                last_modified=__import__("datetime").datetime.now(),
                is_test=False,
                is_config=False,
                is_api_contract=False,
                is_entry_point=False,
            ),
            symbols=[
                Symbol(
                    id=f"{path}::func",
                    name="func",
                    qualified_name=f"{path}::func",
                    kind="function",
                    signature="def func():",
                    start_line=1,
                    end_line=3,
                    docstring=None,
                    visibility="public",
                ),
            ],
            imports=[],
            exports=["func"],
            docstring=None,
            parse_errors=[],
        )

    builder.add_file(_make_parsed("pkg/alpha.py"))
    builder.add_file(_make_parsed("pkg/beta.py"))
    builder.add_file(_make_parsed("pkg/gamma.py"))
    builder.build()

    graph = builder.graph()
    edges_before = graph.number_of_edges()

    # -- Mock git metadata with co-change partners -------------------------
    git_meta_map = {
        "pkg/alpha.py": {
            "co_change_partners_json": json.dumps([
                {"file_path": "pkg/beta.py", "co_change_count": 5},
            ]),
        },
        "pkg/beta.py": {
            "co_change_partners_json": json.dumps([
                {"file_path": "pkg/alpha.py", "co_change_count": 5},
                {"file_path": "pkg/gamma.py", "co_change_count": 4},
            ]),
        },
        "pkg/gamma.py": {
            "co_change_partners_json": json.dumps([
                {"file_path": "pkg/beta.py", "co_change_count": 4},
            ]),
        },
    }

    added = builder.add_co_change_edges(git_meta_map, min_count=3)

    # Two unique pairs above threshold: (alpha, beta) and (beta, gamma)
    assert added == 2
    assert graph.number_of_edges() == edges_before + 2

    # Verify edge attributes
    co_edges = [
        (u, v, d)
        for u, v, d in graph.edges(data=True)
        if d.get("edge_type") == "co_changes"
    ]
    assert len(co_edges) == 2

    # Each co_changes edge should carry a weight
    for _u, _v, data in co_edges:
        assert data["weight"] >= 3
        assert data["imported_names"] == []
