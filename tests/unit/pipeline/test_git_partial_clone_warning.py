"""Regression coverage for degraded partial-clone git indexing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from repowise.core.pipeline.incremental import rebuild_graph_and_git
from repowise.core.pipeline.phases.git import _run_git_indexing


@pytest.mark.asyncio
async def test_partial_clone_warning_reaches_progress_channel(tmp_path) -> None:
    """A bounded git degradation must remain visible in normal CLI output."""
    progress = MagicMock()
    summary = MagicMock()

    async def index_repo(_repo_id: str, **callbacks):
        callbacks["on_warning"]("Git history skipped for partial clone (filter: blob:none).")
        return summary, []

    with patch("repowise.core.ingestion.git_indexer.GitIndexer") as indexer_type:
        indexer_type.return_value.index_repo.side_effect = index_repo
        result, metadata, metadata_map = await _run_git_indexing(
            tmp_path,
            commit_depth=500,
            follow_renames=False,
            progress=progress,
        )

    assert result is summary
    assert metadata == []
    assert metadata_map == {}
    progress.on_message.assert_any_call(
        "warning",
        "Git history skipped for partial clone (filter: blob:none).",
    )


@pytest.mark.asyncio
async def test_partial_clone_warning_reaches_incremental_log(tmp_path) -> None:
    """The update path must surface the same visible degraded-state warning."""
    graph_builder = MagicMock()
    graph_builder.compute_metrics_parallel = AsyncMock()
    messages: list[str] = []

    async def index_changed_files(_paths, **callbacks):
        callbacks["on_warning"]("Git history skipped for partial clone (filter: blob:none).")
        return []

    with (
        patch(
            "repowise.core.pipeline.incremental.build_repo_graph",
            return_value=([], {}, graph_builder, MagicMock(), 0),
        ),
        patch("repowise.core.ingestion.git_indexer.GitIndexer") as indexer_type,
    ):
        indexer_type.return_value.index_changed_files.side_effect = index_changed_files
        await rebuild_graph_and_git(
            tmp_path,
            [],
            {},
            [],
            log=messages.append,
        )

    assert messages == ["Git history skipped for partial clone (filter: blob:none)."]
