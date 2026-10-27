"""CLI coverage for ``repowise security scan``."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest
from click.testing import CliRunner

from repowise.cli.commands.security_cmd import security_command
from repowise.cli.main import cli


def test_security_scan_without_history_prints_stub_hint() -> None:
    result = CliRunner().invoke(cli, ["security", "scan"])

    assert result.exit_code == 0, result.output
    assert "Working-tree scanning runs automatically" in result.output
    assert "--history" in result.output


def test_security_scan_help_lists_history_flags() -> None:
    result = CliRunner().invoke(cli, ["security", "scan", "--help"])

    assert result.exit_code == 0
    assert "--history" in result.output
    assert "--since" in result.output
    assert "--to" in result.output
    assert "--all-patterns" in result.output
    # ``--output`` still works but is hidden: the machine-readable axis is
    # spelled ``--format`` everywhere now, and --help documents one name.
    assert "--format" in result.output
    assert "--output" not in result.output


@dataclass
class _FakeSummary:
    commits_scanned: int = 2
    blobs_scanned: int = 3
    files_scanned: int = 4
    findings_inserted: int = 1
    by_severity: dict[str, int] = field(default_factory=lambda: {"high": 1})
    by_kind: dict[str, int] = field(default_factory=lambda: {"hardcoded_secret": 1})


class _FakeScanner:
    last_kwargs: ClassVar[dict[str, Any]] = {}

    def __init__(self, _session: object, _repo_id: object) -> None:
        pass

    async def scan_history(self, *_args: object, **kwargs: object) -> _FakeSummary:
        type(self).last_kwargs = dict(kwargs)
        return _FakeSummary()


def test_security_scan_history_json_wires_secrets_only_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()

    target = SimpleNamespace(
        is_workspace=False,
        repo_path=repo,
        notice=lambda *_a, **_k: None,
        primary_path=lambda: None,
    )

    async def _fake_get_repository_by_path(_session: object, _path: str) -> object:
        return SimpleNamespace(id=1)

    class _FakeSessionCtx:
        async def __aenter__(self) -> SimpleNamespace:
            return SimpleNamespace(commit=self._commit)

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def _commit(self) -> None:
            return None

    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.resolve_command_target",
        lambda path=None: target,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.ensure_repowise_dir",
        lambda _p: None,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.get_db_url_for_repo",
        lambda _p: "sqlite+aiosqlite:///:memory:",
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.run_async",
        lambda coro: __import__("asyncio").run(coro),
    )

    # Patch modules imported inside the command body.
    import repowise.core.analysis.history_scan as history_scan
    import repowise.core.persistence as persistence
    import repowise.core.persistence.crud as crud

    monkeypatch.setattr(history_scan, "HistorySecurityScanner", _FakeScanner)
    monkeypatch.setattr(persistence, "create_engine", lambda _url: object())
    monkeypatch.setattr(persistence, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(persistence, "get_session", lambda _sf: _FakeSessionCtx())
    monkeypatch.setattr(crud, "get_repository_by_path", _fake_get_repository_by_path)

    _FakeScanner.last_kwargs = {}
    result = CliRunner().invoke(
        security_command,
        ["scan", "--history", "--path", str(repo), "--output", "json"],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["commits_scanned"] == 2
    assert payload["findings_inserted"] == 1
    assert _FakeScanner.last_kwargs.get("secrets_only") is True
    assert _FakeScanner.last_kwargs.get("since") is None
    assert _FakeScanner.last_kwargs.get("to") is None


def test_security_scan_history_all_patterns_disables_secrets_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()

    target = SimpleNamespace(
        is_workspace=False,
        repo_path=repo,
        notice=lambda *_a, **_k: None,
        primary_path=lambda: None,
    )

    async def _fake_get_repository_by_path(_session: object, _path: str) -> object:
        return SimpleNamespace(id=1)

    class _FakeSessionCtx:
        async def __aenter__(self) -> SimpleNamespace:
            return SimpleNamespace(commit=self._commit)

        async def __aexit__(self, *_exc: object) -> None:
            return None

        async def _commit(self) -> None:
            return None

    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.resolve_command_target",
        lambda path=None: target,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.ensure_repowise_dir",
        lambda _p: None,
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.get_db_url_for_repo",
        lambda _p: "sqlite+aiosqlite:///:memory:",
    )
    monkeypatch.setattr(
        "repowise.cli.commands.security_cmd.run_async",
        lambda coro: __import__("asyncio").run(coro),
    )

    import repowise.core.analysis.history_scan as history_scan
    import repowise.core.persistence as persistence
    import repowise.core.persistence.crud as crud

    monkeypatch.setattr(history_scan, "HistorySecurityScanner", _FakeScanner)
    monkeypatch.setattr(persistence, "create_engine", lambda _url: object())
    monkeypatch.setattr(persistence, "create_session_factory", lambda _engine: object())
    monkeypatch.setattr(persistence, "get_session", lambda _sf: _FakeSessionCtx())
    monkeypatch.setattr(crud, "get_repository_by_path", _fake_get_repository_by_path)

    _FakeScanner.last_kwargs = {}
    result = CliRunner().invoke(
        security_command,
        [
            "scan",
            "--history",
            "--path",
            str(repo),
            "--all-patterns",
            "--since",
            "v1.0.0",
            "--to",
            "HEAD",
            "--output",
            "json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert _FakeScanner.last_kwargs.get("secrets_only") is False
    assert _FakeScanner.last_kwargs.get("since") == "v1.0.0"
    assert _FakeScanner.last_kwargs.get("to") == "HEAD"
