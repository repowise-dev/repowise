"""The update's health writer must carry ``repo_path`` through to core.

``persist_partial_health`` re-scores the git-derived markers on files the run
did not walk, and it only does that when it is given a repo path. The CLI
update called it without one, so on that path every history marker kept
whatever the last full index said: a file could sit at "bug-fixed 9 times
recently" long after the fixes stopped, and a scoring change would not reach
any file the update happened not to touch.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.cli.commands.update_cmd.persistence import _persist_partial_health


@pytest.mark.asyncio
async def test_partial_health_persist_forwards_repo_path(monkeypatch) -> None:
    seen: dict[str, Any] = {}

    async def _fake(session, repo_id, report, repo_path=None):
        seen["repo_path"] = repo_path

    import repowise.core.pipeline.incremental as incremental

    monkeypatch.setattr(incremental, "persist_partial_health", _fake)

    await _persist_partial_health(object(), "repo-1", object(), "/tmp/some-repo")

    assert seen["repo_path"] == "/tmp/some-repo"
