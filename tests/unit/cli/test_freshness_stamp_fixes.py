"""Regression tests for the index-freshness stamp fixes.

Covers:
  * F1 - ``repowise update --workspace`` re-stamps editor files even without
    ``--agents-md`` (the early-return that froze CLAUDE.md for workspace users).
  * F4 - the doctor CLAUDE.md-stamp drift check.
"""

from __future__ import annotations

import types
from datetime import UTC
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
        doctor_cmd.advisories.console,
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
        doctor_cmd.advisories.console,
        "print",
        lambda *a, **k: printed.append(" ".join(str(x) for x in a)),
    )
    doctor_cmd._advise_claude_md_stamp(tmp_path, {"last_sync_commit": "d05bc6e8" + "0" * 32})
    assert printed == []


# ---------------------------------------------------------------------------
# Read-time freshness self-heal: prefer state.json's last_sync_commit over a
# DB repositories.head_commit that an older build left un-stamped, so the
# extension badge / MCP staleness signal is correct on the first read without
# requiring a `repowise update` first.
# ---------------------------------------------------------------------------


def _write_state(repo_path: Path, last_sync_commit: str | None) -> None:
    import json

    dot = repo_path / ".repowise"
    dot.mkdir(parents=True, exist_ok=True)
    payload = {} if last_sync_commit is None else {"last_sync_commit": last_sync_commit}
    (dot / "state.json").write_text(json.dumps(payload), encoding="utf-8")


def test_resolve_indexed_commit_prefers_state_json(tmp_path):
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    fresh = "8092ebf" + "0" * 33
    _write_state(tmp_path, fresh)
    # DB row lags (a pre-fix "no changed files" run bumped only state.json).
    assert resolve_indexed_commit("deadbeef" + "0" * 32, str(tmp_path)) == fresh


def test_resolve_indexed_commit_falls_back_to_db_without_state(tmp_path):
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    stale_db = "abc1234" + "0" * 33
    # No .repowise/state.json (hosted/ephemeral index) — keep the DB value.
    assert resolve_indexed_commit(stale_db, str(tmp_path)) == stale_db


def test_resolve_indexed_commit_none_when_nothing_known(tmp_path):
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    assert resolve_indexed_commit(None, str(tmp_path)) is None
    # An empty/malformed state.json must not shadow the DB value with "".
    _write_state(tmp_path, None)
    assert resolve_indexed_commit("abc1234" + "0" * 33, str(tmp_path)) == "abc1234" + "0" * 33


def test_freshness_self_heals_and_silences_false_stale(tmp_path):
    import types

    from repowise.server.mcp_server._meta import freshness_from_repo

    head = "8092ebf" + "0" * 33
    # Live checkout is at ``head``; state.json says the index synced to ``head``;
    # but the DB row still holds the last full-index commit.
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "HEAD").write_text(head + "\n", encoding="utf-8")
    _write_state(tmp_path, head)

    repo = types.SimpleNamespace(
        head_commit="deadbeef" + "0" * 32,
        local_path=str(tmp_path),
        updated_at=None,
    )
    out = freshness_from_repo(repo)

    assert out["indexed_commit"] == head[:12]
    # HEAD matches the (self-healed) indexed commit → no false "behind" warning.
    assert "stale_warning" not in out
    assert "live_head" not in out


# ---------------------------------------------------------------------------
# "Indexed at" tracks the last sync, not just the last health snapshot: a
# no-change update advances repositories.updated_at without a new snapshot, so
# the freshness time must not read hours-stale right after a refresh.
# ---------------------------------------------------------------------------


def test_last_indexed_at_prefers_newer_repo_update():
    from datetime import datetime

    from repowise.server.routers.code_health import _resolve_last_indexed_at

    snapshot = datetime(2026, 7, 2, 11, 48, tzinfo=UTC)
    refreshed = datetime(2026, 7, 2, 14, 25, tzinfo=UTC)
    # A no-change `repowise update` bumped updated_at past the last snapshot.
    assert _resolve_last_indexed_at(snapshot, refreshed) == refreshed.isoformat()


def test_last_indexed_at_keeps_snapshot_when_newer():
    from datetime import datetime

    from repowise.server.routers.code_health import _resolve_last_indexed_at

    snapshot = datetime(2026, 7, 2, 14, 25, tzinfo=UTC)
    older_update = datetime(2026, 7, 2, 11, 48, tzinfo=UTC)
    assert _resolve_last_indexed_at(snapshot, older_update) == snapshot.isoformat()


def test_last_indexed_at_handles_missing_values():
    from datetime import datetime

    from repowise.server.routers.code_health import _resolve_last_indexed_at

    only_update = datetime(2026, 7, 2, 14, 25, tzinfo=UTC)
    assert _resolve_last_indexed_at(None, only_update) == only_update.isoformat()
    assert _resolve_last_indexed_at(only_update, None) == only_update.isoformat()
    assert _resolve_last_indexed_at(None, None) is None
