"""The workspace finish panel: same shape as the single-repo one, repos inside it."""

from __future__ import annotations

import io

from rich.console import Console

from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.mode_names import model_name, provider_model
from repowise.cli.ui.workspace_finish import (
    MAX_REPO_ROWS,
    RepoOutcome,
    WorkspaceFacts,
    build_workspace_finish_panel,
    print_workspace_finish_panel,
)

_WRITTEN = {
    "content_provenance": "model",
    "git_tier": "full",
    "git_commit_cap": 5000,
    "provider": {"name": "claude_cli", "model": "claude_cli/claude-haiku-5-5"},
}
_FROM_CODE = {"content_provenance": "template", "git_tier": "full", "git_commit_cap": 5000}
_INDEX_ONLY = {"content_provenance": "none", "git_tier": "full", "git_commit_cap": 5000}


def _render(facts: WorkspaceFacts, setup=None) -> str:
    console = Console(record=True, width=100, theme=THEME)
    console.print(build_workspace_finish_panel(facts, setup=setup))
    return console.export_text()


def _facts(repos, provider=None, cross_repo=None) -> WorkspaceFacts:
    return WorkspaceFacts(
        repos=repos, files=553, symbols=8528, primary=repos[0].alias, elapsed="6m 50s",
        provider=provider, cross_repo=cross_repo,
    )


def test_one_repo_reads_like_the_single_repo_panel():
    setup = EditorSetupOutcome(
        claude_code_connected=True, restart_hosts=({"id": "claude-code", "name": "Claude Code"},)
    )
    facts = _facts(
        [RepoOutcome("beast", _WRITTEN, pages=937)],
        provider=provider_model("claude_cli", "claude_cli/claude-haiku-5-5"),
    )
    text = _render(facts, setup)

    assert "Workspace ready" in text
    assert "Written by claude-haiku-5-5 · git full · last 5,000 commits" in text
    assert "claude_cli / claude-haiku-5-5" in text
    assert "claude_cli/claude" not in text
    assert "553 files · 8,528 symbols" in text
    assert "beast" in text and "937 pages" in text
    assert "Restart Claude Code to load the repowise tools." in text
    assert "Then ask your agent" in text
    assert "Use Repowise to give me a tour of beast" in text
    assert "—" not in text
    steps = text[text.index("What's next:") :]
    assert "open the workspace dashboard (keeps running" in steps
    # serve is the only step; search, status and generate wait in `repowise next`.
    assert len([ln for ln in steps.splitlines() if ln.strip("│ ").startswith("repowise")]) == 1
    assert "repowise mcp" not in text and "Auto-sync" not in text


def test_mixed_modes_count_each_and_label_each_repo():
    facts = _facts(
        [
            RepoOutcome("api", _FROM_CODE, pages=12),
            RepoOutcome("web", _FROM_CODE, pages=8),
            RepoOutcome("infra", _INDEX_ONLY),
            RepoOutcome("broken", error="parse failed"),
        ]
    )
    text = _render(facts)

    assert "Mixed (2 repos Built from code, 1 repo Index only) · git full" in text
    assert "3 indexed · 1 failed" in text
    assert "12 pages · Built from code" in text
    assert "no documentation (Index only)" in text
    # Problems lead the list.
    assert text.index("✗ broken") < text.index("✓ api")
    assert "failed (parse failed)" in text
    assert "repowise generate <repo>" not in text
    flat = " ".join(text.replace("│", " ").split())
    # The cross-repo question names the repos that indexed, not the failed one.
    assert "Use Repowise to show me how api, web and infra depend on each other" in flat


def test_the_cross_repo_finding_stands_out():
    facts = _facts(
        [RepoOutcome("api", _FROM_CODE, pages=12), RepoOutcome("web", _FROM_CODE, pages=8)],
        cross_repo="api and web changed together in 30 work sessions",
    )
    text = _render(facts)
    assert "What stood out" in text
    assert "Across repos    api and web changed together in 30 work sessions" in text
    assert "What stood out" not in _render(_facts([RepoOutcome("api", _FROM_CODE, pages=1)]))


def test_many_repos_are_cut_after_ten_with_the_rest_counted():
    repos = [RepoOutcome(f"svc-{i:02d}", _FROM_CODE, pages=3) for i in range(25)]
    repos[20] = RepoOutcome("svc-20", _FROM_CODE, skipped="no source files")
    text = _render(_facts(repos))

    listed = [f"svc-{i:02d}" for i in range(25) if f"svc-{i:02d}" in text]
    assert len(listed) == MAX_REPO_ROWS
    assert "svc-20" in listed  # the skipped one is never cut
    assert f"and {25 - MAX_REPO_ROWS} more repos" in text
    assert "repowise update --repo svc-20 --full" in text


def test_dry_run_names_no_mode_and_suggests_no_rebuild():
    repos = [RepoOutcome("api", {}, skipped="dry run")]
    facts = WorkspaceFacts(
        repos=repos, files=0, symbols=0, primary="api", elapsed="1s", dry_run=True
    )
    text = _render(facts)

    assert "Dry run, nothing written" in text
    assert "--full" not in text
    assert "Mode unknown" not in text


def test_model_name_drops_a_repeated_provider_prefix():
    assert model_name("claude_cli", "claude_cli/claude-haiku-5-5") == "claude-haiku-5-5"
    assert model_name("openrouter", "anthropic/claude-sonnet") == "anthropic/claude-sonnet"
    assert model_name(None, "gemini-2.5-flash") == "gemini-2.5-flash"
    assert provider_model("claude_cli", "claude_cli/claude-haiku-5-5") == (
        "claude_cli / claude-haiku-5-5"
    )
    assert provider_model("gemini", "gemini-2.5-flash") == "gemini / gemini-2.5-flash"


def test_off_a_terminal_the_workspace_panel_is_plain_lines():
    console = Console(file=io.StringIO(), width=100, force_terminal=False, theme=THEME)
    print_workspace_finish_panel(console, _facts([RepoOutcome("api", _FROM_CODE, pages=3)]))
    out = console.file.getvalue()

    assert "╭" not in out and "│" not in out
    assert "api" in out and "repowise serve" in out


def test_the_cross_repo_finding_reads_what_analysis_wrote(tmp_path):
    from repowise.cli.commands.init_cmd.workspace import cross_repo_finding
    from repowise.core.workspace.cross_repo import (
        CrossRepoCoChange,
        CrossRepoOverlay,
        save_overlay,
    )

    assert cross_repo_finding(tmp_path) is None

    def edge(a, b, times):
        return CrossRepoCoChange(a, "x.py", b, "y.py", strength=0.5, frequency=times, last_date="")

    overlay = CrossRepoOverlay(
        co_changes=[edge("web", "api", 30), edge("api", "web", 11), edge("api", "worker", 20)]
    )
    save_overlay(overlay, tmp_path)
    # The file pair with the most shared sessions names the repo pair; sessions
    # are not summed across file pairs, which would count one session twice.
    assert cross_repo_finding(tmp_path) == "api and web changed together in 30 work sessions"


def test_a_failing_cross_repo_read_costs_the_line_and_is_logged(tmp_path, monkeypatch):
    from structlog.testing import capture_logs

    from repowise.cli.commands.init_cmd import workspace

    def broken(_root):
        raise ValueError("bad overlay")

    monkeypatch.setattr(workspace, "_busiest_repo_pair", broken)
    with capture_logs() as logs:
        assert workspace.cross_repo_finding(tmp_path) is None
    assert any(e["event"] == "workspace_cross_repo_finding_failed" for e in logs)
