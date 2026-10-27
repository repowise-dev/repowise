"""Keeping machine-specific generated files out of git through ``.git/info/exclude``."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

from repowise.cli.git_exclude import HEADER, add_to_exclude, keep_out_candidates
from repowise.cli.ui.plan_screen import PlanState, render_plan
from repowise.cli.ui.repo_scanner import RepoScanInfo
from repowise.core.owned_paths import MACHINE_SPECIFIC_FILES, MANAGED_FILES


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "README.md").write_text("hi\n", encoding="utf-8")
    _git(root, "add", "README.md")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _write(root: Path, *paths: str) -> None:
    # At most three fixture files per test: one write each is the whole job.
    for rel in paths:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text("x\n", encoding="utf-8")


def _status(root: Path) -> str:
    return _git(root, "status", "--short", "--untracked-files=all")


# -- the registry -------------------------------------------------------------


def test_machine_specific_is_an_attribute_of_the_managed_list() -> None:
    assert set(MACHINE_SPECIFIC_FILES) <= set(MANAGED_FILES)
    for path in (
        ".claude/CLAUDE.md",
        ".github/copilot-instructions.md",
        ".vscode/mcp.json",
        ".vscode/extensions.json",
        ".claude/settings.local.json",
    ):
        assert path in MACHINE_SPECIFIC_FILES
    # Teams share the MCP config.
    assert ".mcp.json" not in MACHINE_SPECIFIC_FILES


def test_the_planned_files_cover_every_machine_specific_file_init_writes(tmp_path: Path) -> None:
    from repowise.cli.editor_setup import EditorSetupOptions, planned_project_files

    planned = planned_project_files(tmp_path, EditorSetupOptions())
    assert {".claude/CLAUDE.md", ".vscode/mcp.json", ".github/copilot-instructions.md"} <= set(
        planned
    )
    no_claude_md = EditorSetupOptions(disabled_project_files=frozenset({"claude_md"}))
    assert ".claude/CLAUDE.md" not in planned_project_files(tmp_path, no_claude_md)


# -- the writer ---------------------------------------------------------------


def test_exclude_hides_the_files_and_a_second_run_adds_nothing(repo: Path) -> None:
    _write(repo, ".claude/CLAUDE.md", ".vscode/mcp.json", ".mcp.json")
    exclude = repo / ".git" / "info" / "exclude"
    before = exclude.read_text(encoding="utf-8")

    candidates = keep_out_candidates(repo)
    assert candidates == (".claude/CLAUDE.md", ".vscode/mcp.json")
    assert add_to_exclude(repo, candidates) == ["/.claude/CLAUDE.md", "/.vscode/mcp.json"]

    after = exclude.read_text(encoding="utf-8")
    assert after.startswith(before)
    assert after.count(HEADER) == 1
    assert _status(repo).split() == ["??", ".mcp.json"]
    # Idempotent, and nothing left to offer.
    assert add_to_exclude(repo, candidates) == []
    assert exclude.read_text(encoding="utf-8") == after
    assert keep_out_candidates(repo) == ()
    assert not (repo / ".gitignore").exists()


def test_tracked_and_already_ignored_files_are_not_offered(repo: Path) -> None:
    _write(repo, ".claude/CLAUDE.md", ".vscode/mcp.json", ".github/copilot-instructions.md")
    _git(repo, "add", ".claude/CLAUDE.md")
    (repo / ".gitignore").write_text(".vscode/\n", encoding="utf-8")
    assert keep_out_candidates(repo) == (".github/copilot-instructions.md",)


def test_planned_files_are_offered_before_they_exist(repo: Path) -> None:
    assert keep_out_candidates(repo, [".vscode/mcp.json", ".mcp.json"]) == (".vscode/mcp.json",)


def test_a_linked_worktree_writes_the_common_exclude(repo: Path, tmp_path: Path) -> None:
    wt = tmp_path / "wt"
    _git(repo, "worktree", "add", "-q", str(wt))
    assert (wt / ".git").is_file()
    _write(wt, ".claude/CLAUDE.md")

    assert add_to_exclude(wt, keep_out_candidates(wt)) == ["/.claude/CLAUDE.md"]
    assert "/.claude/CLAUDE.md" in (repo / ".git" / "info" / "exclude").read_text("utf-8")
    assert ".claude" not in _status(wt)


def test_a_repo_in_a_subdirectory_anchors_its_prefix(repo: Path) -> None:
    sub = repo / "pkg"
    _write(sub, ".claude/CLAUDE.md")
    assert add_to_exclude(sub, keep_out_candidates(sub)) == ["/pkg/.claude/CLAUDE.md"]
    assert ".claude" not in _status(repo)


def test_outside_git_nothing_is_offered_or_written(tmp_path: Path) -> None:
    _write(tmp_path, ".claude/CLAUDE.md")
    assert keep_out_candidates(tmp_path) == ()
    assert add_to_exclude(tmp_path, [".claude/CLAUDE.md"]) == []


# -- the plan screen ----------------------------------------------------------


def _render(state: PlanState) -> str:
    console = Console(record=True, width=120, color_system=None)
    render_plan(console, state)
    return " ".join(console.export_text().split())


_SCAN = RepoScanInfo(total_files=50, language_counts={"Python": 30}, total_commits=40)
_FILES = (".claude/CLAUDE.md", ".vscode/mcp.json")


def test_the_plan_names_the_files_off_by_default() -> None:
    out = _render(PlanState(repo_name="demo", scan=_SCAN, keep_out=_FILES))
    assert (
        "Keep out of git: off for .claude/CLAUDE.md, .vscode/mcp.json "
        "(e adds them to .git/info/exclude)"
    ) in out


def test_the_plan_line_when_on_and_absent_with_nothing_to_offer() -> None:
    on = _render(PlanState(repo_name="demo", scan=_SCAN, keep_out=_FILES, git_exclude=True))
    assert "Keep out of git: .claude/CLAUDE.md, .vscode/mcp.json (adds to .git/info/exclude)" in on
    assert "Keep out of git:" not in _render(PlanState(repo_name="demo", scan=_SCAN))


def test_e_accepts_the_offer_and_enter_records_it(
    repo: Path, monkeypatch: Any
) -> None:
    from repowise.cli.commands.init_cmd import _finish, _plan
    from repowise.cli.ui import plan_screen

    _write(repo, ".claude/CLAUDE.md")
    run = _init_run(repo)
    monkeypatch.setattr(_plan, "agent_providers_set_up", lambda _r: ())
    monkeypatch.setattr(
        "repowise.cli.ui.provider_selection._detect_provider_status", lambda _r: {}
    )
    monkeypatch.setattr(_plan, "interactive_advanced_config", lambda *a, **k: None)
    monkeypatch.setattr("repowise.cli.commands.init_cmd._questions._apply_advanced_answers",
                        lambda *a: None)
    asked: list[str] = []

    def confirm(text: str, default: bool = False) -> bool:
        asked.append(text)
        return True

    monkeypatch.setattr(_plan.click, "confirm", confirm)
    keys = iter([plan_screen.EDIT, plan_screen.START])
    monkeypatch.setattr(_plan, "read_plan_key", lambda *_a: next(keys))
    _plan.run_plan_screen(run, _SCAN)

    assert asked == ["  Keep .claude/CLAUDE.md out of git (adds to .git/info/exclude)?"]
    assert run.git_exclude is True
    _finish._keep_out_of_git(run)
    assert "/.claude/CLAUDE.md" in (repo / ".git" / "info" / "exclude").read_text("utf-8")


def _init_run(repo: Path) -> Any:
    from repowise.cli.commands.init_cmd._options import InitOptions, InitRun

    fields = {name: None for name in InitOptions.__dataclass_fields__}
    fields.update(
        exclude=(), coverage_report=(), run_mode="standard", concurrency=10,
        onboarding=True, editor_setup=False, save_key=True, yes=False, index_only=False,
        follow_renames=False, skip_tests=False, skip_infra=False,
    )
    return InitRun(opts=InitOptions(**fields), repo_path=repo, start=0.0, is_interactive=True)
