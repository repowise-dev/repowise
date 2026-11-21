"""Every Codex user-level path follows ``$CODEX_HOME``, the directory Codex reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.agent_targets.targets import codex as codex_target
from repowise.core.codex_home import codex_home
from repowise.core.sessions.adapters.codex import CodexAdapter


def test_codex_home_defaults_to_dot_codex() -> None:
    assert codex_home() == Path.home() / ".codex"


def test_user_scope_writes_land_in_codex_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))

    assert codex_target.user_hooks_path() == home / "hooks.json"
    writes = codex_target.write_prompts()

    assert writes
    assert all(w.path.parent == home / "prompts" for w in writes)
    assert all(w.path.is_file() for w in writes)
    assert not (Path.home() / ".codex").exists()


def test_sessions_are_read_from_codex_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "codex-home"
    rollout = home / "sessions" / "2026" / "11" / "21" / "rollout.jsonl"
    rollout.parent.mkdir(parents=True)
    rollout.write_text("{}\n", encoding="utf-8")
    monkeypatch.setenv("CODEX_HOME", str(home))

    assert CodexAdapter().discover(tmp_path) == [rollout]


def test_detect_finds_codex_under_codex_home_alone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.agent_adapters.codex import CodexAdapter as CodexHookAdapter

    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))
    assert not CodexHookAdapter().detect()

    home.mkdir()
    assert not (Path.home() / ".codex").exists()
    assert CodexHookAdapter().detect()


def test_uninstall_also_cleans_files_an_earlier_release_left_in_dot_codex(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.cli.agent_targets.types import FileAction, Scope

    legacy = Path.home() / ".codex"
    # An earlier release wrote here, ignoring CODEX_HOME.
    written = codex_target.write_prompts()
    mine = legacy / "prompts" / "my-own.md"
    mine.write_text("keep\n", encoding="utf-8")
    home = tmp_path / "codex-home"
    monkeypatch.setenv("CODEX_HOME", str(home))

    described = codex_target.TARGET.describe_paths(Scope.USER)
    assert str(legacy / "prompts") in described
    assert str(home / "prompts") in described

    result = codex_target.TARGET.uninstall(Scope.USER)

    removed = {w.path for w in result.files if w.action is FileAction.REMOVED}
    assert {w.path for w in written} <= removed
    assert not any(w.path.exists() for w in written)
    assert mine.exists()


def test_without_codex_home_there_is_no_legacy_dir() -> None:
    from repowise.core.codex_home import legacy_codex_home

    assert legacy_codex_home() is None
