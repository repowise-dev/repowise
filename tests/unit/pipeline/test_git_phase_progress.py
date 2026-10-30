"""The git phase shows a row from its first second, not only once files are counted."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from repowise.core.pipeline.phases.git import _run_git_indexing


class _Recorder:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def on_phase_start(self, phase: str, total: int | None) -> None:
        self.events.append(("start", phase, total))

    def on_item_done(self, phase: str) -> None:
        self.events.append(("item", phase))

    def on_phase_done(self, phase: str) -> None:
        self.events.append(("done", phase))

    def on_phase_failed(self, phase: str) -> None:
        self.events.append(("failed", phase))

    def on_message(self, level: str, text: str) -> None:
        self.events.append(("message", level))


async def _run(index_repo, tmp_path) -> list[tuple]:
    progress = _Recorder()
    with patch("repowise.core.ingestion.git_indexer.GitIndexer") as indexer_type:
        indexer_type.return_value.index_repo.side_effect = index_repo
        await _run_git_indexing(tmp_path, commit_depth=500, follow_renames=False, progress=progress)
    return [e for e in progress.events if e[1] == "git"]


async def test_git_row_opens_before_the_commit_walk(tmp_path) -> None:
    async def index_repo(_repo_id: str, **callbacks):
        callbacks["on_start"](2)
        callbacks["on_file_done"]()
        callbacks["on_file_done"]()
        return MagicMock(), [{"file_path": "a.py"}, {"file_path": "b.py"}]

    events = await _run(index_repo, tmp_path)
    assert events[0] == ("start", "git", None)
    assert events[1] == ("start", "git", 2)
    assert events[-1] == ("done", "git")


async def test_nothing_indexed_closes_the_row_without_a_done_line(tmp_path) -> None:
    """No repository: the early row must not end as "file history indexed"."""

    async def index_repo(_repo_id: str, **_callbacks):
        return MagicMock(), []

    events = await _run(index_repo, tmp_path)
    assert events == [("start", "git", None), ("failed", "git")]


async def test_a_crash_closes_the_row_without_a_done_line(tmp_path) -> None:
    async def index_repo(_repo_id: str, **_callbacks):
        raise RuntimeError("boom")

    events = await _run(index_repo, tmp_path)
    assert events == [("start", "git", None), ("failed", "git")]
