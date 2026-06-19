"""Regression tests for the index-freshness stamp fixes.

Covers:
  * F1 - ``repowise update --workspace`` re-stamps editor files even without
    ``--agents-md`` (the early-return that froze CLAUDE.md for workspace users).
  * F4 - the doctor CLAUDE.md-stamp drift check.
"""

from __future__ import annotations

import types
from pathlib import Path

from repowise.cli.commands.doctor_cmd import _claude_md_stamp_status

# ---------------------------------------------------------------------------
# F1 - workspace editor refresh runs regardless of the agents_md flag
# ---------------------------------------------------------------------------


def _fake_ws_config(*aliases: str):
    repos = [types.SimpleNamespace(alias=a, path=a) for a in aliases]
    return types.SimpleNamespace(repos=repos)


def test_workspace_refresh_runs_when_agents_md_none(tmp_path, monkeypatch):
    from repowise.cli.commands.update_cmd import workspace as ws_mod

    # Two indexed repos + one un-indexed (no .repowise) which must be skipped.
    for alias in ("api", "web"):
        (tmp_path / alias / ".repowise").mkdir(parents=True)
    (tmp_path / "ghost").mkdir()

    refreshed: list[str] = []
    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files",
        lambda console, repo_path, *, options=None: refreshed.append(Path(repo_path).name),
    )

    ws_mod._refresh_workspace_editor_project_files(
        ws_root=tmp_path,
        ws_config=_fake_ws_config("api", "web", "ghost"),
        repo_filter=None,
        agents_md=None,  # the default path that used to early-return and skip all
    )

    assert sorted(refreshed) == ["api", "web"]


def test_workspace_refresh_respects_repo_filter(tmp_path, monkeypatch):
    from repowise.cli.commands.update_cmd import workspace as ws_mod

    for alias in ("api", "web"):
        (tmp_path / alias / ".repowise").mkdir(parents=True)

    refreshed: list[str] = []
    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files",
        lambda console, repo_path, *, options=None: refreshed.append(Path(repo_path).name),
    )

    ws_mod._refresh_workspace_editor_project_files(
        ws_root=tmp_path,
        ws_config=_fake_ws_config("api", "web"),
        repo_filter="web",
        agents_md=None,
    )

    assert refreshed == ["web"]


# ---------------------------------------------------------------------------
# F4 - doctor CLAUDE.md stamp drift check
# ---------------------------------------------------------------------------


def _write_claude_md(repo_path: Path, commit: str) -> None:
    dot = repo_path / ".claude"
    dot.mkdir(parents=True, exist_ok=True)
    (dot / "CLAUDE.md").write_text(
        f"# CLAUDE.md\n\nLast indexed: 2026-06-07 (commit {commit}). Confidence: 100%.\n",
        encoding="utf-8",
    )


def test_stamp_status_in_sync(tmp_path):
    _write_claude_md(tmp_path, "abc1234")
    ok, detail = _claude_md_stamp_status(tmp_path, {"last_sync_commit": "abc1234def567"})
    assert ok is True
    assert "in sync" in detail


def test_stamp_status_drift(tmp_path):
    _write_claude_md(tmp_path, "1c67e0a9")
    ok, detail = _claude_md_stamp_status(tmp_path, {"last_sync_commit": "d05bc6e8" + "0" * 32})
    assert ok is False
    assert "repowise update" in detail


def test_stamp_status_skips_without_claude_md(tmp_path):
    assert _claude_md_stamp_status(tmp_path, {"last_sync_commit": "abc1234"}) is None


def test_stamp_status_skips_without_synced_commit(tmp_path):
    _write_claude_md(tmp_path, "abc1234")
    assert _claude_md_stamp_status(tmp_path, {}) is None


def test_stamp_status_ignores_abbreviated_stamp(tmp_path):
    # A too-short (<7) sha can't be compared safely, so skip rather than guess.
    _write_claude_md(tmp_path, "abc")
    assert _claude_md_stamp_status(tmp_path, {"last_sync_commit": "abcdef0123456"}) is None


def test_advise_stamp_prints_on_drift(tmp_path, monkeypatch):
    from repowise.cli.commands import doctor_cmd

    _write_claude_md(tmp_path, "1c67e0a9")
    printed: list[str] = []
    monkeypatch.setattr(
        doctor_cmd.console,
        "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    doctor_cmd._advise_claude_md_stamp(tmp_path, {"last_sync_commit": "d05bc6e8" + "0" * 32})
    assert any("drift" in p for p in printed)


def test_advise_stamp_skips_when_claude_md_disabled(tmp_path, monkeypatch):
    from repowise.cli.commands import doctor_cmd

    _write_claude_md(tmp_path, "1c67e0a9")
    (tmp_path / ".repowise").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".repowise" / "config.yaml").write_text(
        "editor_files:\n  claude_md: false\n", encoding="utf-8"
    )
    printed: list[str] = []
    monkeypatch.setattr(
        doctor_cmd.console,
        "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    doctor_cmd._advise_claude_md_stamp(tmp_path, {"last_sync_commit": "d05bc6e8" + "0" * 32})
    assert printed == []
