"""Regression coverage for ``update --full --dry-run`` (issue #1996)."""

from __future__ import annotations

import json
import sqlite3
from io import StringIO
from pathlib import Path
from typing import Any

import click
import pytest
from rich.console import Console

from repowise.cli.commands.update_cmd import command as update_cmd
from repowise.cli.helpers import CommandTarget


def _prepare_repo(repo: Path) -> Path:
    repowise_dir = repo / ".repowise"
    repowise_dir.mkdir(parents=True)
    state_path = repowise_dir / "state.json"
    state_path.write_text(
        json.dumps(
            {
                "git_tier": "essential",
                "total_pages": 42,
                "provider": "state-provider",
                "model": "state-model",
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    (repowise_dir / "config.yaml").write_text(
        "provider: config-provider\nmodel: config-model\n",
        encoding="utf-8",
    )
    # Seed every mutable marker/store that the full-upgrade path could touch.
    # A dry-run must preserve existing state too, not merely avoid creating
    # these files when they are absent.
    (repowise_dir / ".update.lock").write_text("held-by-another-update\n", encoding="utf-8")
    (repowise_dir / ".update.pending").write_text("{\"head\": \"sentinel\"}\n", encoding="utf-8")
    with sqlite3.connect(repowise_dir / "wiki.db") as connection:
        connection.execute("CREATE TABLE sentinel (value TEXT NOT NULL)")
        connection.execute("INSERT INTO sentinel VALUES (?)", ("existing-index",))
    return state_path


def _snapshot_repowise_tree(repo: Path) -> dict[str, bytes]:
    """Capture all files in the persisted index, including hidden markers."""
    repowise_dir = repo / ".repowise"
    return {
        str(path.relative_to(repowise_dir)): path.read_bytes()
        for path in repowise_dir.rglob("*")
        if path.is_file()
    }


def _patch_boundary(monkeypatch: pytest.MonkeyPatch, repo: Path) -> None:
    monkeypatch.setattr(update_cmd, "configure_cli_logging", lambda **_kwargs: None)
    monkeypatch.setattr(
        update_cmd,
        "resolve_command_target",
        lambda **_kwargs: CommandTarget(mode="single", repo_path=repo),
    )
    monkeypatch.setattr(
        "repowise.cli.commands.workspace_cmd.inherit_workspace_distill_verdict",
        lambda _repo_path: None,
    )


def _run(repo: Path, *, dry_run: bool, progress: str = "rich") -> update_cmd.UpdateOutcome:
    return update_cmd.run_update(
        path=str(repo),
        provider_name=None,
        model=None,
        since=None,
        reasoning=None,
        cascade_budget=None,
        dry_run=dry_run,
        workspace=False,
        no_workspace=True,
        repo_alias=None,
        full=True,
        progress=progress,
    )


def test_full_dry_run_preserves_state_and_never_dispatches_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _prepare_repo(repo)
    persisted_before = _snapshot_repowise_tree(repo)
    _patch_boundary(monkeypatch, repo)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "repowise.cli.commands.upgrade_flow.upgrade_to_full",
        lambda *_args, **kwargs: calls.append(kwargs),
    )
    output = StringIO()
    monkeypatch.setattr(
        update_cmd,
        "console",
        Console(file=output, force_terminal=False, color_system=None),
    )

    outcome = _run(repo, dry_run=True)

    assert outcome is update_cmd.UpdateOutcome.DRY_RUN
    assert calls == []
    assert _snapshot_repowise_tree(repo) == persisted_before
    rendered = output.getvalue()
    assert "config-provider / config-model" in rendered
    assert "ESSENTIAL -> FULL" in rendered
    assert "Pages currently recorded: 42" in rendered
    assert "No changes made" in rendered


def test_full_dry_run_emits_dry_run_machine_outcome(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _prepare_repo(repo)
    _patch_boundary(monkeypatch, repo)
    monkeypatch.setattr(update_cmd, "silence_logs_for_machine_output", lambda: None)

    events: list[tuple[str, dict[str, Any]]] = []

    class _Emitter:
        def start(self, **kwargs: Any) -> None:
            events.append(("start", kwargs))

        def done(self, **kwargs: Any) -> None:
            events.append(("done", kwargs))

    monkeypatch.setattr(update_cmd, "JsonProgressEmitter", _Emitter)

    with click.Context(click.Command("test")):
        outcome = _run(repo, dry_run=True, progress="json")

    assert outcome is update_cmd.UpdateOutcome.DRY_RUN
    assert events[-1][0] == "done"
    assert events[-1][1]["outcome"] == update_cmd.UpdateOutcome.DRY_RUN.value
    assert events[-1][1]["pages_generated"] == 0


def test_full_without_dry_run_still_dispatches_upgrade(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _prepare_repo(repo)
    _patch_boundary(monkeypatch, repo)

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(
        "repowise.cli.commands.upgrade_flow.upgrade_to_full",
        lambda *_args, **kwargs: calls.append(kwargs),
    )

    outcome = _run(repo, dry_run=False)

    assert outcome is update_cmd.UpdateOutcome.REGENERATED
    assert calls == [
        {
            "provider_name": None,
            "model": None,
            "reasoning": None,
            "concurrency": 10,
            "yes": False,
        }
    ]
