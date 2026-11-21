"""Every Codex user-level path follows ``$CODEX_HOME``, the directory Codex reads."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.cli.agent_targets.targets import codex as codex_target
from repowise.core.sessions.adapters.codex import CodexAdapter, codex_home


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
