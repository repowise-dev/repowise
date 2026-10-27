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
from repowise.cli.ui.finish_panel import FinishFacts, ModelUpgrade, StoodOut, build_finish_panel
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
        top_hotspot="serializer.py",
        pages=None if mode == "fast" else "42 pages",
        spend="$0.12 (40,000 tokens)" if mode == "written_by_model" else None,
    )
    steps = build_contextual_next_steps(
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
        graph_edges=0, elapsed="1.0s", top_hotspot="[id].tsx", pages="1 page",
    )
    steps = build_contextual_next_steps(hotspot_count=1, top_hotspot="[id].tsx", setup=setup)
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



# -- What stood out and the model block ------------------------------------------

_FOUND = StoodOut(
    health_score=7.43,
    health_mark="warning",
    lowest_file="scheduler/planner.py",
    lowest_score=3.12,
    dead_unreachable=38,
    dead_safe=12,
    hotspot_file="auth/session.py",
    hotspot_commits=48,
    decision_candidates=14,
)


def _panel(stood_out: StoodOut | None = None, model_upgrade=None, mode="built_from_code") -> str:
    scope = {"search": {"semantic": "available"}, **_SCOPES[mode]}
    facts = FinishFacts(
        scope=scope, files=1284, symbols=9812, languages="Python 70%, TypeScript 30%",
        graph_nodes=1, graph_edges=0, elapsed="1.0s", pages="412 pages",
        stood_out=stood_out or StoodOut(), model_upgrade=model_upgrade,
    )
    console = Console(record=True, width=100, theme=THEME)
    console.print(build_finish_panel(facts, steps=build_contextual_next_steps()))
    return console.export_text()


def test_stood_out_names_the_four_findings():
    text = _panel(_FOUND)
    assert "What stood out" in text
    assert "7.4/10 Warning  scheduler/planner.py is lowest at 3.1" in text
    assert "38 files nothing reaches, 12 findings safe to delete" in text
    assert "Hotspot       auth/session.py, changed in 48 commits" in text
    assert "14 candidates to review" in text
    assert "confirmed" not in text and "decisions found" not in text
    # The count rows they replace are gone; the rest stay.
    assert "Git history" not in text and "unused export" not in text
    assert text.count("Dead code") == 1 and text.count("Decisions") == 1
    assert "Indexed" in text and "Documentation" in text and "Elapsed" in text


@pytest.mark.parametrize(
    ("score", "word", "style"),
    [(8.0, "Healthy", "green"), (7.99, "Warning", "yellow"), (3.99, "Alert", "red")],
)
def test_health_word_and_colour_follow_the_three_bands(score, word, style):
    from repowise.core.analysis.health.grading import mark_for

    stood = StoodOut(health_score=score, health_mark=mark_for(score))
    console = Console(record=True, width=100, theme=THEME, color_system="truecolor")
    facts = FinishFacts(
        scope={}, files=1, symbols=1, languages="Python", graph_nodes=1, graph_edges=0,
        elapsed="1s", stood_out=stood,
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
    assert "1 candidate to review" in text
    assert "What stood out" not in _panel(StoodOut())


def test_ungraded_health_shows_the_score_without_a_word():
    text = _panel(StoodOut(health_score=9.0))
    assert "9.0/10" in text
    assert "Healthy" not in text


@pytest.mark.parametrize(
    ("upgrade", "line"),
    [
        (
            ModelUpgrade("repowise generate --provider claude_cli", "Uses your Claude Code sign-in"),
            "Uses your Claude Code sign-in: repowise generate --provider claude_cli",
        ),
        (
            ModelUpgrade("repowise generate --provider openai", "Uses your OpenAI key"),
            "Uses your OpenAI key: repowise generate --provider openai",
        ),
        (
            ModelUpgrade("repowise generate"),
            "repowise generate needs a key, or --provider ollama for a local model.",
        ),
    ],
)
def test_keyless_run_names_what_a_model_adds_and_the_detected_source(upgrade, line):
    text = _panel(model_upgrade=upgrade)
    assert "Add a model when you want more" in text
    assert "Docs that explain how and why, chat in the dashboard," in text
    assert line in text
    assert "Or press m next time on the plan screen." in text
    block = text[text.index("Add a model") : text.index("Try asking")]
    assert "free" not in block.lower()


def test_a_model_run_has_no_model_block():
    assert "Add a model" not in _panel(_FOUND, mode="written_by_model")


def test_new_lines_stay_ascii_for_a_cp1252_console(monkeypatch):
    from repowise.cli import _stdio

    monkeypatch.setattr(_stdio, "original_stdout_encoding", lambda: "cp1252")
    text = _panel(_FOUND, ModelUpgrade("repowise generate"))
    start = text.index("What stood out")
    block = text[start : text.index("What's next:")]
    body = "\n".join(line.strip("│ ") for line in block.splitlines())
    body.encode("ascii")
    assert "Index ready" in text and "a guided tour of all this in your browser" in text
