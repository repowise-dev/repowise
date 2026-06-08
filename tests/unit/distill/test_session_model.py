"""Session-model detection for savings pricing.

Builds fixture transcript trees in the Claude Code and Codex shapes and asserts
the resolver picks the newest model across agents, normalizes the ``[1m]``
context-window tag, and falls back to the Sonnet default when nothing matches.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from repowise.core.distill.missed import transcript_dir_for
from repowise.core.distill.session_model import (
    DEFAULT_MODEL,
    normalize_model_id,
    resolve_session_model,
)


def _write_claude(projects_root: Path, repo_root: Path, model: str, *, mtime: float) -> Path:
    """Write a one-line Claude Code transcript naming *model* for *repo_root*."""
    directory = transcript_dir_for(repo_root, projects_root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{model}.jsonl".replace("[", "_").replace("]", "_")
    line = {"type": "assistant", "message": {"role": "assistant", "model": model}}
    path.write_text(json.dumps(line) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _write_codex(sessions_root: Path, repo_root: Path, model: str, *, mtime: float) -> Path:
    """Write a Codex rollout file naming *model* with cwd under *repo_root*."""
    day = sessions_root / "2026" / "06" / "08"
    day.mkdir(parents=True, exist_ok=True)
    path = day / f"rollout-{model}.jsonl"
    lines = [
        {"type": "session_meta", "payload": {"cwd": str(repo_root)}},
        {"type": "turn_context", "payload": {"model": model, "cwd": str(repo_root)}},
    ]
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_normalize_strips_context_window_tag() -> None:
    assert normalize_model_id("claude-opus-4-8[1m]") == "claude-opus-4-8"
    assert normalize_model_id("  claude-opus-4-8  ") == "claude-opus-4-8"
    # A legit date-suffixed key must survive untouched.
    assert normalize_model_id("claude-3-5-sonnet-20241022") == "claude-3-5-sonnet-20241022"


def test_no_transcripts_falls_back_to_sonnet(tmp_path: Path) -> None:
    resolved = resolve_session_model(
        tmp_path / "repo",
        projects_root=tmp_path / "projects",
        codex_sessions_root=tmp_path / "codex",
    )
    assert resolved.model == DEFAULT_MODEL
    assert resolved.agent == "unknown"
    assert resolved.source == "default"


def test_newest_claude_model_wins_and_normalizes(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    projects = tmp_path / "projects"
    _write_claude(projects, repo, "claude-sonnet-4-6", mtime=1000.0)
    _write_claude(projects, repo, "claude-opus-4-8[1m]", mtime=2000.0)

    resolved = resolve_session_model(
        repo, projects_root=projects, codex_sessions_root=tmp_path / "codex"
    )
    assert resolved.model == "claude-opus-4-8"
    assert resolved.raw == "claude-opus-4-8[1m]"
    assert resolved.agent == "claude_code"
    assert "Claude Code" in resolved.source


def test_most_recent_agent_wins_across_agents(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    projects = tmp_path / "projects"
    codex = tmp_path / "codex"
    _write_claude(projects, repo, "claude-opus-4-8", mtime=1000.0)
    _write_codex(codex, repo, "gpt-5-codex", mtime=3000.0)  # newer

    resolved = resolve_session_model(
        repo, projects_root=projects, codex_sessions_root=codex
    )
    assert resolved.model == "gpt-5-codex"
    assert resolved.agent == "codex"
    assert "Codex" in resolved.source


def test_codex_session_outside_repo_is_ignored(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "elsewhere"
    projects = tmp_path / "projects"
    codex = tmp_path / "codex"
    _write_claude(projects, repo, "claude-opus-4-8", mtime=1000.0)
    _write_codex(codex, other, "gpt-5-codex", mtime=3000.0)  # newer but wrong repo

    resolved = resolve_session_model(
        repo, projects_root=projects, codex_sessions_root=codex
    )
    assert resolved.model == "claude-opus-4-8"
    assert resolved.agent == "claude_code"
