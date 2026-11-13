"""Tests for ``repowise doctor --workspace`` (Phase C2).

Covers workspace-level validation: drift detection between
``WorkspaceConfig.last_commit_at_index`` and per-repo ``state.json``,
missing directory entries, and ``--repair`` behavior (drift sync + dead
entry removal).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

from repowise.cli.commands.doctor_cmd import (
    _check_mcp_registered,
    _run_workspace_checks,
    doctor_command,
)
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig


def _git_init(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=str(p), capture_output=True)


def _write_state(repo: Path, commit: str) -> None:
    rdir = repo / ".repowise"
    rdir.mkdir(exist_ok=True)
    (rdir / "state.json").write_text(
        json.dumps({"last_sync_commit": commit}), encoding="utf-8"
    )


def test_workspace_checks_detects_state_drift(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    _git_init(backend)
    _write_state(backend, "real-sha-from-state")

    cfg = WorkspaceConfig(
        repos=[RepoEntry(
            path="backend",
            alias="backend",
            last_commit_at_index="stale-sha-in-config",
        )],
    )
    cfg.save(tmp_path)

    issues = _run_workspace_checks(tmp_path, cfg, repair=False)
    assert any("drift" in i for i in issues), issues


def test_workspace_checks_detects_missing_dir(tmp_path: Path) -> None:
    cfg = WorkspaceConfig(
        repos=[RepoEntry(path="ghost", alias="ghost")],
    )
    cfg.save(tmp_path)
    issues = _run_workspace_checks(tmp_path, cfg, repair=False)
    assert any("missing directory" in i for i in issues)


def test_workspace_checks_repair_syncs_drift(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    _git_init(backend)
    real_sha = "abc" * 14  # 42 chars (close enough)
    _write_state(backend, real_sha)

    cfg = WorkspaceConfig(
        repos=[RepoEntry(
            path="backend",
            alias="backend",
            last_commit_at_index="OLD",
        )],
    )
    cfg.save(tmp_path)

    _run_workspace_checks(tmp_path, cfg, repair=True)
    assert cfg.get_repo("backend").last_commit_at_index == real_sha


def test_workspace_checks_repair_drops_dead_entries(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    _git_init(backend)
    _write_state(backend, "deadbeef")
    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(
                path="backend", alias="backend", last_commit_at_index="deadbeef",
            ),
            RepoEntry(path="ghost", alias="ghost"),
        ],
        default_repo="backend",
    )
    cfg.save(tmp_path)

    _run_workspace_checks(tmp_path, cfg, repair=True)
    aliases = cfg.repo_aliases()
    assert "backend" in aliases
    assert "ghost" not in aliases


def test_workspace_checks_clean_workspace_has_no_issues(tmp_path: Path) -> None:
    backend = tmp_path / "backend"
    _git_init(backend)
    sha = "f" * 40
    _write_state(backend, sha)
    cfg = WorkspaceConfig(
        repos=[RepoEntry(
            path="backend", alias="backend", last_commit_at_index=sha,
        )],
    )
    cfg.save(tmp_path)
    issues = _run_workspace_checks(tmp_path, cfg, repair=False)
    assert issues == []


def test_doctor_command_workspace_flag_runs(tmp_path: Path) -> None:
    """CliRunner smoke test — `repowise doctor --workspace` should not
    crash on a workspace with one indexed repo."""
    backend = tmp_path / "backend"
    _git_init(backend)
    sha = "0" * 40
    _write_state(backend, sha)
    # Create empty wiki.db so the per-repo db check has something to bite on.
    (backend / ".repowise" / "wiki.db").write_bytes(b"")

    cfg = WorkspaceConfig(
        repos=[RepoEntry(
            path="backend", alias="backend",
            is_primary=True, last_commit_at_index=sha,
        )],
        default_repo="backend",
    )
    cfg.save(tmp_path)

    runner = CliRunner()
    result = runner.invoke(
        doctor_command, [str(tmp_path), "--workspace"],
    )
    # The per-repo db check may fail because wiki.db is an empty file,
    # but the workspace doctor wiring must not crash.
    assert result.exit_code == 0, result.output
    assert "backend" in result.output


def test_check_mcp_registered_does_not_crash(tmp_path: Path) -> None:
    """Advisory check — never throws even when no claude_desktop_config exists."""
    _check_mcp_registered(tmp_path)


# ---------------------------------------------------------------------------
# --repair: a missing member state.json is flagged (not silently rebuilt),
# a dead entry that was the primary reports the default_repo move, and the
# per-repo summary no longer claims "Nothing to repair." on an unhealthy repo.
# (issue: doctor --repair contradicts itself on a workspace with drift)
# ---------------------------------------------------------------------------

def _capture_console_print(monkeypatch):
    """Record every ``console.print`` call (pre-render, markup intact)."""
    from repowise.cli.commands.doctor_cmd import workspace_checks as wc

    printed: list[str] = []
    monkeypatch.setattr(
        wc.console, "print", lambda *a, **_k: printed.append(" ".join(str(x) for x in a))
    )
    return printed


def test_workspace_repair_reports_default_repo_change(tmp_path, monkeypatch):
    backend = tmp_path / "backend"
    _git_init(backend)
    _write_state(backend, "f" * 40)
    cfg = WorkspaceConfig(
        repos=[
            RepoEntry(path="ghost", alias="ghost"),  # missing dir -> dead
            RepoEntry(path="backend", alias="backend", last_commit_at_index="f" * 40),
        ],
        default_repo="ghost",
    )
    cfg.save(tmp_path)

    printed = _capture_console_print(monkeypatch)
    _run_workspace_checks(tmp_path, cfg, repair=True)

    # The dead primary was dropped and the default reassigned...
    assert cfg.default_repo == "backend"
    # ...and the move was reported, not silent.
    assert any("Primary repo changed" in m for m in printed), printed


def test_workspace_repair_flags_missing_member_state(tmp_path, monkeypatch):
    # Member exists and is a git repo, has a .repowise/ dir, but no state.json.
    member = tmp_path / "member"
    _git_init(member)
    (member / ".repowise").mkdir()
    cfg = WorkspaceConfig(
        repos=[RepoEntry(path="member", alias="member")],
    )
    cfg.save(tmp_path)

    printed = _capture_console_print(monkeypatch)
    issues = _run_workspace_checks(tmp_path, cfg, repair=True)

    assert any("missing state.json" in i for i in issues), issues
    # A fix is surfaced for the row rather than a silent (never-happening) rebuild.
    fix_lines = [m for m in printed if "state.json missing" in m and "repowise update" in m]
    assert fix_lines, printed
    # And it is not reported as "no repairs needed".
    assert not any("No workspace-level repairs needed" in m for m in printed), printed


def test_repair_summary_not_clean_when_repo_unhealthy(tmp_path, monkeypatch):
    """Change 4: the per-repo repair summary must not print 'Nothing to repair.'
    for a repo that still has failing checks (e.g. a missing state.json)."""
    from repowise.cli.commands.doctor_cmd import _repair as rp
    from repowise.cli.commands.doctor_cmd._checks.context import CheckContext

    rwdir = tmp_path / ".repowise"
    rwdir.mkdir()
    ctx = CheckContext(repo_path=tmp_path, repowise_dir=rwdir)

    printed: list[str] = []
    monkeypatch.setattr(
        rp.console, "print", lambda *a, **_k: printed.append(" ".join(str(x) for x in a))
    )

    # Healthy repo: unchanged behaviour.
    rp.run_repair(ctx, repo_ok=True)
    assert any("Nothing to repair." in m for m in printed), printed

    # Unhealthy repo: no misleading "all clean" line.
    printed.clear()
    rp.run_repair(ctx, repo_ok=False)
    assert not any("Nothing to repair." in m for m in printed), printed
    assert any("Nothing here for --repair to fix" in m for m in printed), printed
