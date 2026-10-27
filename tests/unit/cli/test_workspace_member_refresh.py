"""A no-change workspace update refreshes only the members that moved."""

from __future__ import annotations

import os
import types
from pathlib import Path

import pytest

from repowise.cli.commands.update_cmd import member_refresh
from repowise.cli.commands.update_cmd import workspace as ws_mod


def _ws(tmp_path: Path, *aliases: str) -> types.SimpleNamespace:
    for alias in aliases:
        dot = tmp_path / alias / ".repowise"
        dot.mkdir(parents=True)
        (dot / "wiki.db").write_bytes(b"db")
        (dot / "state.json").write_text("{}", encoding="utf-8")
    return types.SimpleNamespace(
        repos=[types.SimpleNamespace(alias=a, path=a) for a in aliases]
    )


@pytest.fixture
def refreshed(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files",
        lambda console, repo_path, *, options=None: calls.append(Path(repo_path).name),
    )
    monkeypatch.setattr(
        "repowise.cli.commands._index_outcome.refresh_read_snapshots", lambda _path: True
    )
    return calls


def _refresh(tmp_path: Path, ws_config: types.SimpleNamespace, agents_md: bool | None = None):
    ws_mod._refresh_workspace_editor_project_files(
        ws_root=tmp_path, ws_config=ws_config, repo_filter=None, agents_md=agents_md
    )


def _touch(path: Path) -> None:
    path.write_bytes(path.read_bytes() + b"+")


def test_second_refresh_skips_every_unchanged_member(tmp_path, refreshed):
    ws_config = _ws(tmp_path, "api", "web")
    _refresh(tmp_path, ws_config)
    assert sorted(refreshed) == ["api", "web"]

    refreshed.clear()
    _refresh(tmp_path, ws_config)
    assert refreshed == []


def test_a_write_to_one_store_refreshes_only_that_member(tmp_path, refreshed):
    ws_config = _ws(tmp_path, "api", "web")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    _touch(tmp_path / "web" / ".repowise" / "wiki.db")
    _refresh(tmp_path, ws_config)
    assert refreshed == ["web"]


def test_an_empty_wal_left_by_a_read_only_reader_is_not_a_write(tmp_path, refreshed):
    """A read-only probe of a WAL store leaves an empty ``-wal`` behind; the
    member is unchanged. A WAL holding writes still refreshes it."""
    ws_config = _ws(tmp_path, "api", "web")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    for alias in ("api", "web"):
        (tmp_path / alias / ".repowise" / "wiki.db-wal").write_bytes(b"")
    _refresh(tmp_path, ws_config)
    assert refreshed == []

    (tmp_path / "web" / ".repowise" / "wiki.db-wal").write_bytes(b"frame")
    _refresh(tmp_path, ws_config)
    assert refreshed == ["web"]


def test_a_deleted_editor_file_is_written_again(tmp_path, refreshed):
    ws_config = _ws(tmp_path, "api")
    claude_md = tmp_path / "api" / ".claude" / "CLAUDE.md"
    claude_md.parent.mkdir()
    claude_md.write_text("x", encoding="utf-8")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    claude_md.unlink()
    _refresh(tmp_path, ws_config)
    assert refreshed == ["api"]


def test_a_template_or_version_change_refreshes_every_member(tmp_path, refreshed, monkeypatch):
    ws_config = _ws(tmp_path, "api", "web")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    monkeypatch.setattr(member_refresh, "_code_stamp", lambda: "another release")
    _refresh(tmp_path, ws_config)
    assert sorted(refreshed) == ["api", "web"]


def test_a_new_day_refreshes_so_the_indexed_at_stamp_advances(tmp_path, refreshed, monkeypatch):
    ws_config = _ws(tmp_path, "api")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    real = member_refresh.datetime

    class _Tomorrow:
        @staticmethod
        def now(tz):
            from datetime import timedelta

            return real.now(tz) + timedelta(days=1)

    monkeypatch.setattr(member_refresh, "datetime", _Tomorrow)
    _refresh(tmp_path, ws_config)
    assert refreshed == ["api"]


def test_a_failed_refresh_is_retried(tmp_path, refreshed, monkeypatch):
    ws_config = _ws(tmp_path, "api")
    monkeypatch.setattr(
        "repowise.cli.commands._index_outcome.refresh_read_snapshots", lambda _path: False
    )
    _refresh(tmp_path, ws_config)
    _refresh(tmp_path, ws_config)
    assert refreshed == ["api", "api"]


def test_an_explicit_agents_md_refreshes_every_member(tmp_path, refreshed):
    ws_config = _ws(tmp_path, "api")
    _refresh(tmp_path, ws_config)
    refreshed.clear()

    _refresh(tmp_path, ws_config, agents_md=True)
    assert refreshed == ["api"]


def test_a_configured_database_always_refreshes(tmp_path, refreshed, monkeypatch):
    ws_config = _ws(tmp_path, "api")
    monkeypatch.setenv("REPOWISE_DB_URL", "sqlite:///" + os.fspath(tmp_path / "shared.db"))
    _refresh(tmp_path, ws_config)
    _refresh(tmp_path, ws_config)
    assert refreshed == ["api", "api"]


def test_up_to_date_restamp_opens_only_moved_members(tmp_path, refreshed, monkeypatch):
    ws_config = _ws(tmp_path, "api", "web")
    _refresh(tmp_path, ws_config)
    _touch(tmp_path / "web" / ".repowise" / "state.json")

    stamped: list[str] = []

    async def _reconcile(repo_path: Path, head: str | None) -> None:
        stamped.append(repo_path.name)

    monkeypatch.setattr("repowise.core.workspace.reconcile_repo_head_commit", _reconcile)
    ws_mod._reconcile_up_to_date(
        tmp_path,
        [((tmp_path / a).resolve(), "abc") for a in ("api", "web")],
    )
    assert stamped == ["web"]


def test_a_member_whose_store_cannot_be_opened_is_not_stamped(tmp_path, monkeypatch):
    """No store or no repository row writes no views, so the member is retried."""
    from contextlib import asynccontextmanager

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    calls: list[str] = []
    monkeypatch.setattr(
        "repowise.cli.editor_setup.refresh_editor_project_files",
        lambda console, repo_path, *, options=None: calls.append(Path(repo_path).name),
    )

    @asynccontextmanager
    async def _unopenable(_root):
        yield None

    monkeypatch.setattr("repowise.cli.helpers.repo_index_session", _unopenable)
    ws_config = _ws(tmp_path, "api")
    _refresh(tmp_path, ws_config)
    assert member_refresh.load_member_stamps(tmp_path) == {}

    _refresh(tmp_path, ws_config)
    assert calls == ["api", "api"]
