"""Regression coverage for degraded partial-clone git indexing."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

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
