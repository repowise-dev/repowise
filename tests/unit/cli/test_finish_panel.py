"""The init finish panel: serve first in every mode, with the real port and Node note."""

from __future__ import annotations

from pathlib import Path

import pytest
from rich.console import Console

from repowise.cli.commands.serve_cmd._ports import DEFAULT_UI_PORT
from repowise.cli.commands.serve_cmd._web_assets import _NODE_MIN_MAJOR
from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui import build_contextual_next_steps
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.finish_panel import FinishFacts, FirstLook, StoodOut, build_finish_panel
from repowise.cli.ui.mode_names import completion_title, scope_line
from repowise.cli.ui.result_panels import serve_step

_SCOPES = {
    "fast": {"run_mode": "fast", "content_provenance": "none", "git_tier": "essential"},
    "built_from_code": {"content_provenance": "template", "git_tier": "full"},
    "written_by_model": {
        "content_provenance": "model",
        "git_tier": "full",
        "git_commit_cap": 500,
        "provider": {"model": "gemini-2.5-flash"},
        "search": {"semantic": "available"},
    },
}


_PROMPT = (
    "Before I change src/serializer.py, use Repowise to tell me what depends on it, "
    "who knows it best, and what tends to break with it."
)


def _render(mode: str, setup=None, files_written=None) -> str:
    scope = {
        "search": {"semantic": "unavailable", "next_command": "repowise reindex"},
        "provider": {},
        **_SCOPES[mode],
    }
    facts = FinishFacts(
        scope=scope,
        files=50,
        symbols=312,
        languages="Python 100%",
        elapsed="12.3s",
        first_prompt=_PROMPT,
        pages=None if mode == "fast" else "42 pages",
        spend="$0.12 (40,000 tokens)" if mode == "written_by_model" else None,
    )
    steps = build_contextual_next_steps(fast_mode=mode == "fast", setup=setup)
    console = Console(record=True, width=100, theme=THEME)
    console.print(
        build_finish_panel(
            facts, steps=steps, setup=setup, repo_path=Path("/r"), files_written=files_written
        )
    )
    return console.export_text()


def _step_commands(text: str) -> list[str]:
    after = text.split("What's next:", 1)[1]
    rows = [line.strip("│ ").split("  ")[0] for line in after.splitlines()]
    return [row for row in rows if row.startswith("repowise ")]


@pytest.mark.parametrize("mode", list(_SCOPES))
@pytest.mark.parametrize(
    "setup",
    [
        None,
        EditorSetupOutcome(editor_setup_disabled=True),
        EditorSetupOutcome(claude_code_connected=True, first_index=True),
    ],
    ids=["no-setup", "headless", "connected"],
)
def test_serve_leads_and_only_what_blocks_use_follows(mode, setup):
    text = _render(mode, setup)
    commands = _step_commands(text)

    assert commands[0] == "repowise serve"
    blocking = []
    if setup is not None and setup.editor_setup_disabled:
        blocking.append("repowise agents add")
    if mode == "fast":
        blocking.append("repowise update --full")
    assert [c.split("  ")[0] for c in commands[1:]] == blocking
    assert "keeps running; Ctrl+C stops it" in text
    assert f"http://localhost:{DEFAULT_UI_PORT} or the next free port" in text
    assert f"needs Node {_NODE_MIN_MAJOR}+; the first run downloads the UI" in text
    assert "—" not in text


def test_serve_step_reads_the_port_serve_tries_first():
    from repowise.cli.commands.serve_cmd.command import serve_command

    ui_port = next(p for p in serve_command.params if p.name == "ui_port")
    assert ui_port.default == DEFAULT_UI_PORT
    assert f"localhost:{DEFAULT_UI_PORT}" in serve_step()[1]


@pytest.mark.parametrize(
    ("mode", "title"),
    [
        ("fast", "Index only ready"),
        ("built_from_code", "Index ready, documentation built from code"),
        ("written_by_model", "Repowise is ready"),
    ],
)
def test_title_and_scope_use_the_design_vocabulary(mode, title):
    text = _render(mode)
    assert title in text
    assert "wiki" not in text.lower()


def test_title_and_scope_line_come_from_the_naming_table():
    text = _render("written_by_model")
    scope = {**_SCOPES["written_by_model"]}
    assert scope_line(scope) in text
    assert completion_title(scope) in text
    assert "Written by gemini-2.5-flash" in text


def test_restart_names_every_host_this_run_wrote():
    setup = EditorSetupOutcome(
        claude_code_connected=True,
        restart_hosts=(
            {"id": "claude-code", "name": "Claude Code"},
            {"id": "vscode", "name": "VS Code"},
        ),
    )
    text = _render("built_from_code", setup)
    panel_lines = [line for line in text.splitlines() if line.startswith("│")]
    assert any(
        "Restart Claude Code and VS Code to load the repowise tools." in line
        for line in panel_lines
    )
    assert "In Claude Code, /mcp also works." in text


def test_restart_says_both_when_it_names_every_connected_host():
    hosts = ({"id": "claude-code", "name": "Claude Code"}, {"id": "vscode", "name": "VS Code"})
    surfaces = tuple({"id": h["id"], "name": h["name"]} for h in hosts)
    setup = EditorSetupOutcome(
        claude_code_connected=True, restart_hosts=hosts, tool_surfaces=surfaces
    )
    text = _render("built_from_code", setup)
    assert "Restart both to load the repowise tools. In Claude Code, /mcp also works." in text


def test_restart_without_claude_code_skips_the_mcp_tip():
    setup = EditorSetupOutcome(
        claude_code_connected=True, restart_hosts=({"id": "codex", "name": "Codex"},)
    )
    text = _render("built_from_code", setup)
    assert "Restart Codex to load the repowise tools." in text
    assert "/mcp" not in text


def test_rerun_does_not_shout_restart():
    setup = EditorSetupOutcome(claude_code_connected=True, first_index=False)
    text = _render("built_from_code", setup)
    assert "Restart" not in text
    assert "Already connected" in text


def test_an_unconnected_agent_gets_its_connect_command_never_mcp_dot():
    setup = EditorSetupOutcome(
        claude_code_connected=True, unconnected=({"id": "cursor", "name": "Cursor"},)
    )
    text = " ".join(_render("built_from_code", setup).replace("│", " ").split())
    assert "Also installed Cursor repowise agents add --target=cursor --yes" in text
    assert "repowise mcp ." not in text


def test_panel_asks_one_first_question_and_names_files_added():
    setup = EditorSetupOutcome(claude_code_connected=True, autosync_hook_installed=False)
    text = _render("built_from_code", setup, [Path("/r/.mcp.json")])
    assert "Then ask your agent" in text
    flat = " ".join(text.replace("│", " ").split())
    assert _PROMPT in flat
    assert "Files added: .mcp.json" in text
    # Sync, the model, decisions and settings wait in `repowise next`.
    for gone in ("Auto-sync", "Add a model", "decision discover", "--suggested", "Graph"):
        assert gone not in text, gone


def test_semantic_search_line_only_when_off():
    assert "Semantic search is off: repowise reindex turns it on" in _render("built_from_code")
    assert "Semantic search is off" not in _render("written_by_model")
    assert "Semantic search is off" not in _render("fast")




@pytest.mark.parametrize(
    ("setup", "server"),
    [
        (EditorSetupOutcome(editor_setup_disabled=True), True),
        (EditorSetupOutcome(claude_code_connected=True), False),
    ],
    ids=["server", "agent"],
)
def test_a_server_run_prints_no_laptop_next_steps(setup, server):
    import io

    from repowise.cli.ui.finish_panel import print_finish_panel

    scope = {
        "content_provenance": "template",
        "search": {
            "semantic": "unavailable",
            "next_command": "repowise reindex --embedder ollama",
        },
    }
    facts = FinishFacts(
        scope=scope, files=1, symbols=1, languages="Python 100%",
        elapsed="1.0s", first_prompt=_PROMPT, pages="1 page",
    )
    out = io.StringIO()
    console = Console(file=out, width=100, theme=THEME)  # a pipe, as a worker's log is
    steps = build_contextual_next_steps(setup=setup)
    print_finish_panel(console, facts, steps=steps, setup=setup, repo_path=Path("/r"))
    text = out.getvalue()

    assert "Indexed" in text
    for laptop in ("What's next:", "repowise serve", "Then ask your agent", "reindex"):
        assert (laptop in text) is not server, laptop


def test_panel_leaves_tool_profiles_to_the_json_and_dashboard():
    surface = {
        "id": "claude-code",
        "name": "Claude Code",
        "profile": "standard",
        "adjustments": [],
        "tool_count": 10,
        "tools": [],
        "change_command": "repowise config set mcp.tools <core|lean|standard|all>",
        "always_load": {"enabled": False, "tools": ["get_answer"], "cost": "x", "command": "y"},
    }
    setup = EditorSetupOutcome(
        claude_code_connected=True,
        tool_surfaces=(surface,),
        restart_hosts=({"id": "claude-code", "name": "Claude Code"},),
        unconnected=({"id": "codex", "name": "Codex CLI"}, {"id": "cursor", "name": "Cursor"}),
    )
    text = _render("built_from_code", setup)
    assert "Restart Claude Code to load the repowise tools." in text
    assert "MCP tools" not in text
    assert "always-load" not in text
    assert "mcp.tools" not in text
    also = next(line for line in text.splitlines() if "Also installed" in line)
    assert "Codex CLI, Cursor" in also
    assert "repowise agents add --target=codex,cursor --yes" in text


def test_bracketed_file_names_render_literally():
    setup = EditorSetupOutcome(claude_code_connected=True)
    scope = {"content_provenance": "template", "search": {"semantic": "available"}}
    facts = FinishFacts(
        scope=scope, files=1, symbols=1, languages="TypeScript 100%", elapsed="1.0s",
        first_prompt="Before I change app/[id].tsx, use Repowise to tell me what depends on it.",
        pages="1 page", stood_out=StoodOut(hotspot_file="app/[id].tsx", hotspot_commits=3),
    )
    steps = build_contextual_next_steps(setup=setup)
    console = Console(record=True, width=100, theme=THEME)
    console.print(
        build_finish_panel(
            facts, steps=steps, setup=setup, repo_path=Path("/r"),
            files_written=[Path("/r/app/[slug]/page.md")],
        )
    )
    text = console.export_text()
    assert "Before I change app/[id].tsx" in text
    assert "Hotspot       app/[id].tsx" in text
    assert "app/[slug]/page.md" in text


@pytest.mark.parametrize(
    ("command", "tail", "absent"),
    [
        ("repowise reindex --embedder openai", "(uses your OpenAI key)", "Ollama"),
        ("repowise reindex --embedder ollama", "(runs locally with Ollama, no key)", "your"),
        ("repowise reindex", "(Ollama needs no key)", "your"),
    ],
)
def test_semantic_search_line_names_who_pays_for_the_embedder(command, tail, absent):
    scope = {
        "content_provenance": "template",
        "search": {"semantic": "unavailable", "next_command": command},
    }
    facts = FinishFacts(
        scope=scope, files=1, symbols=1, languages="Python 100%", elapsed="1.0s", pages="1 page",
    )
    console = Console(record=True, width=120, theme=THEME)
    console.print(build_finish_panel(facts, steps=[serve_step()]))
    line = next(x for x in console.export_text().splitlines() if "Semantic search is off" in x)
    assert f"{command} turns it on {tail}" in line
    assert absent not in line



# -- What stood out ---------------------------------------------------------------

_FOUND = StoodOut(
    health_score=7.43,
    health_band="good",
    lowest_file="scheduler/planner.py",
    lowest_score=3.12,
    dead_unreachable=38,
    dead_safe=12,
    hotspot_file="auth/session.py",
    hotspot_commits=48,
    decision_candidates=14,
)


def _panel(stood_out: StoodOut | None = None, mode="built_from_code") -> str:
    scope = {"search": {"semantic": "available"}, **_SCOPES[mode]}
    facts = FinishFacts(
        scope=scope, files=1284, symbols=9812, languages="Python 70%, TypeScript 30%",
        elapsed="1.0s", pages="412 pages", stood_out=stood_out or StoodOut(),
    )
    console = Console(record=True, width=100, theme=THEME)
    console.print(build_finish_panel(facts, steps=build_contextual_next_steps()))
    return console.export_text()


def test_stood_out_leads_with_the_hotspot_and_names_the_rest_once():
    text = _panel(_FOUND)
    assert "What stood out" in text
    assert "Hotspot       auth/session.py, changed in 48 commits" in text
    assert "Also in the dashboard: health, dead code and 14 background records." in text
    # One headline row: the other findings are not rows of their own.
    assert "7.4/10" not in text and "nothing reaches" not in text
    assert "Indexed" in text and "Documentation" in text and "Elapsed" in text


def test_without_a_hotspot_the_next_finding_leads():
    text = _panel(StoodOut(health_score=7.43, health_band="good", dead_unreachable=2))
    assert "7.4/10 Good" in text
    assert "Also in the dashboard: dead code." in text


@pytest.mark.parametrize(
    ("score", "word", "style"),
    [
        (8.5, "Excellent", "green"),
        (7.7, "Good", "green"),
        (6.9, "Fair", "yellow"),
        (5.4, "Needs work", "dark_orange"),
        (3.99, "At risk", "red"),
    ],
)
def test_health_word_and_colour_follow_the_five_bands(score, word, style):
    from repowise.core.analysis.health.grading import band_for

    stood = StoodOut(health_score=score, health_band=band_for(score))
    console = Console(record=True, width=100, theme=THEME, color_system="truecolor")
    facts = FinishFacts(
        scope={}, files=1, symbols=1, languages="Python", elapsed="1s", stood_out=stood,
    )
    console.print(build_finish_panel(facts, steps=[serve_step()]))
    assert f"/10 {word}" in console.export_text(clear=False)
    from rich.color import Color

    html = console.export_html(inline_styles=True)
    assert f"color: {Color.parse(style).get_truecolor().hex}" in html


def test_unmeasured_findings_are_left_out():
    text = _panel(StoodOut(dead_unreachable=0, decision_candidates=1))
    assert "Health" not in text and "Hotspot" not in text
    assert "no unreachable files" in text
    assert "Also in the dashboard: 1 background record." in text
    assert "What stood out" not in _panel(StoodOut())


def test_ungraded_health_shows_the_score_without_a_word():
    text = _panel(StoodOut(health_score=9.0))
    assert "9.0/10" in text
    assert "Healthy" not in text


def test_new_lines_stay_ascii_for_a_cp1252_console(monkeypatch):
    from repowise.cli import _stdio

    monkeypatch.setattr(_stdio, "original_stdout_encoding", lambda: "cp1252")
    text = _panel(_FOUND)
    start = text.index("What stood out")
    block = text[start : text.index("What's next:")]
    body = "\n".join(line.strip("│ ") for line in block.splitlines())
    body.encode("ascii")
    assert "Index ready" in text and "open the dashboard (keeps running" in text


@pytest.mark.parametrize(
    ("first_look", "line"),
    [
        (
            FirstLook(".repowise/reports/repo-report.html"),
            "Repo report  .repowise/reports/repo-report.html  (template, free)",
        ),
        (
            FirstLook("http://127.0.0.1:3000/repos/r1/report", "claude-haiku-5-5", 0.003),
            "Repo report  http://127.0.0.1:3000/repos/r1/report  "
            "(written by claude-haiku-5-5, $0.003)",
        ),
        (
            FirstLook(".repowise/reports/repo-report.html", "template", 0.002),
            "Repo report  .repowise/reports/repo-report.html  "
            "(template: the model's reply was unusable, $0.002)",
        ),
        (
            FirstLook(".repowise/reports/repo-report.html", agent=True, open_items=4),
            "Repo report: tell the user what it found (repowise report --format md); "
            "they decide what to fix next.",
        ),
    ],
    ids=["keyless", "model", "unusable-reply", "agent"],
)
def test_one_footer_line_names_the_repo_report(first_look, line):
    import io

    from repowise.cli.ui.finish_panel import print_finish_panel

    facts = FinishFacts(
        scope={"content_provenance": "template", "search": {}}, files=1, symbols=1,
        languages="Python 100%", elapsed="1.0s", first_look=first_look,
    )
    assert _render_line(facts, EditorSetupOutcome(claude_code_connected=True)) == line

    out = io.StringIO()  # a server run: no line for someone at the machine
    console = Console(file=out, width=120, theme=THEME)
    setup = EditorSetupOutcome(editor_setup_disabled=True)
    print_finish_panel(console, facts, steps=[], setup=setup, repo_path=Path("/r"))
    assert "Repo report" not in out.getvalue()


def _render_line(facts, setup) -> str:
    console = Console(record=True, width=160, theme=THEME)
    console.print(build_finish_panel(facts, steps=[], setup=setup, repo_path=Path("/r")))
    found = [ln.strip("│ ") for ln in console.export_text().splitlines() if "Repo report" in ln]
    assert len(found) == 1
    return found[0]
