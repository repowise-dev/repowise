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
from repowise.cli.ui.finish_panel import FinishFacts, build_finish_panel
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
        graph_nodes=400,
        graph_edges=900,
        elapsed="12.3s",
        dead_unused=4,
        git_files=50,
        hotspots=3,
        top_hotspot="serializer.py",
        pages=None if mode == "fast" else "42 pages",
        spend="$0.12 (40,000 tokens)" if mode == "written_by_model" else None,
    )
    steps = build_contextual_next_steps(
        index_only=mode != "written_by_model",
        fast_mode=mode == "fast",
        dead_unused=4,
        hotspot_count=3,
        top_hotspot="serializer.py",
        setup=setup,
    )
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
def test_serve_leads_with_port_and_node_note_then_at_most_two_steps(mode, setup):
    text = _render(mode, setup)
    commands = _step_commands(text)

    assert commands[0] == "repowise serve"
    assert 1 <= len(commands) - 1 <= 2
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


def test_restart_claude_code_is_inside_the_panel_on_a_first_index():
    text = _render("built_from_code", EditorSetupOutcome(claude_code_connected=True))
    panel_lines = [line for line in text.splitlines() if line.startswith("│")]
    assert any("Restart Claude Code" in line for line in panel_lines)
    assert any("Claude Code" in line and "Connected" in line for line in panel_lines)


def test_rerun_does_not_shout_restart():
    setup = EditorSetupOutcome(claude_code_connected=True, first_index=False)
    text = _render("built_from_code", setup)
    assert "Restart Claude Code" not in text
    assert "stays connected" in text


def test_panel_names_example_questions_files_and_auto_sync():
    setup = EditorSetupOutcome(claude_code_connected=True, autosync_hook_installed=True)
    text = _render("built_from_code", setup, [Path("/r/.mcp.json")])
    assert "Try asking your agent" in text
    assert "serializer.py" in text
    assert "Files added: .mcp.json" in text
    assert "Auto-sync: on" in text


def test_semantic_search_line_only_when_off():
    assert "Semantic search is off: repowise reindex turns it on" in _render("built_from_code")
    assert "Semantic search is off" not in _render("written_by_model")
    assert "Semantic search is off" not in _render("fast")




def test_bracketed_file_names_render_literally():
    setup = EditorSetupOutcome(claude_code_connected=True)
    scope = {"content_provenance": "template", "search": {"semantic": "available"}}
    facts = FinishFacts(
        scope=scope, files=1, symbols=1, languages="TypeScript 100%", graph_nodes=1,
        graph_edges=0, elapsed="1.0s", hotspots=1, top_hotspot="[id].tsx", pages="1 page",
    )
    steps = build_contextual_next_steps(
        index_only=False, hotspot_count=1, top_hotspot="[id].tsx", setup=setup
    )
    console = Console(record=True, width=100, theme=THEME)
    console.print(
        build_finish_panel(
            facts, steps=steps, setup=setup, repo_path=Path("/r"),
            files_written=[Path("/r/app/[slug]/page.md")],
        )
    )
    text = console.export_text()
    assert 'Why does [id].tsx change' in text
    assert "repowise risk [id].tsx" in text
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
        scope=scope, files=1, symbols=1, languages="Python 100%", graph_nodes=1,
        graph_edges=0, elapsed="1.0s", pages="1 page",
    )
    console = Console(record=True, width=120, theme=THEME)
    console.print(build_finish_panel(facts, steps=[serve_step()]))
    line = next(x for x in console.export_text().splitlines() if "Semantic search is off" in x)
    assert f"{command} turns it on {tail}" in line
    assert absent not in line

