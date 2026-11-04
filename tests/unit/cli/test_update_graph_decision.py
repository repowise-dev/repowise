from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import command


@pytest.mark.parametrize(
    "decision",
    [
        {
            "enabled": False,
            "mode": "full",
            "reason": "feature disabled",
            "files": [],
        },
        {
            "enabled": True,
            "mode": "full",
            "reason": "src/service.py: its dependency surface changed",
            "files": [],
        },
        {
            "enabled": True,
            "mode": "patch",
            "reason": "dependency surface unchanged",
            "files": ["src/service.py"],
        },
    ],
)
def test_rebuild_index_records_graph_decision_in_run_state(monkeypatch, tmp_path, decision):
    builder = SimpleNamespace(incremental_graph_decision=decision)

    def rebuild(*_args, **_kwargs):
        return [], {}, builder, None, 1, {}

    monkeypatch.setattr(command, "_rebuild_graph_and_git", rebuild)
    run = SimpleNamespace(
        cfg={},
        exclude_patterns=[],
        working_tree_diffs=[object()],
        head="abc",
        base_ref="abc",
        full_generation=False,
        wiki_rebuild=False,
        config=SimpleNamespace(git_changed=False, health_changed=False, generation_changed=False),
        renderer_changed=False,
        analyzer_changed=False,
        extraction_changed=False,
        history_stale=False,
        repo_path=Path(tmp_path),
        state={},
        file_diffs=[],
        git_decay_map={},
        full_git_summaries=[],
        timings=None,
        emitter=None,
    )

    command._rebuild_index(run)

    assert run.state["graph_update"] == decision
