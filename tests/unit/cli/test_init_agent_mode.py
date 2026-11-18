"""`repowise init` for agents: short lines off a terminal, NDJSON under --progress json."""

from __future__ import annotations

import contextlib
import io
import json

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from repowise.cli import agent_events
from repowise.cli.commands.init_cmd import _ingest
from repowise.cli.commands.init_cmd._agent_summary import init_output
from repowise.cli.main import cli
from repowise.cli.output import LINE_OUTPUT_WIDTH, line_output_width
from repowise.cli.ui.brand import THEME, print_phase_header
from repowise.cli.ui.mode_names import TITLE_BUILT_FROM_CODE
from repowise.cli.ui.progress_json import JsonProgressCallback
from repowise.cli.ui.result_panels import print_completion_panel

_SUMMARY_KEYS = {
    "v",
    "event",
    "status",
    "mode",
    "restart_required",
    "spend",
    "warnings",
    "files_written",
    "next",
    "findings",
}

_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "OPENROUTER_API_KEY",
    "DEEPSEEK_API_KEY",
    "KIMI_API_KEY",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "OLLAMA_BASE_URL",
    "LITELLM_API_KEY",
    "REPOWISE_PROVIDER",
    "REPOWISE_EMBEDDER",
)


def _lines(text: str) -> list[dict]:
    return [json.loads(line) for line in text.splitlines() if line.strip()]


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for var in _KEYS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("COLUMNS", raising=False)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def _init(repo, *flags):
    return CliRunner(mix_stderr=False).invoke(
        cli, ["init", str(repo), "--no-workspace", "--yes", "--no-prose", *flags]
    )


def test_every_event_is_one_versioned_json_line():
    buf = io.StringIO()
    events = agent_events.EventStream(buf)
    callback = JsonProgressCallback(events, stage="persistence")
    callback.on_phase_start("parse", 2)
    callback.on_item_done("parse")
    callback.on_item_label("parse", "app.py")
    callback.on_phase_done("parse")
    callback.on_phase_done("parse")
    with pytest.raises(RuntimeError), callback.step("state"):
        raise RuntimeError("disk full")
    callback.on_message("info", "Scanned 2 files")
    callback.on_message("warning", "Git history skipped")

    lines = _lines(buf.getvalue())
    assert all(line["v"] == agent_events.SCHEMA_VERSION for line in lines)
    assert [(line["event"], line.get("state")) for line in lines] == [
        ("stage", None),
        ("phase", "start"),
        ("phase", "done"),
        ("phase", "start"),
        ("phase", "failed"),
        ("warning", None),
    ]
    done = lines[2]
    assert done["phase"] == "parse"
    assert done["label"] == "Parsing files"
    assert (done["completed"], done["total"], done["unit"]) == (2, 2, "files")
    assert isinstance(done["elapsed_s"], float)
    assert lines[1]["elapsed_s"] is None
    assert lines[5] == {"v": 1, "event": "warning", "level": "warning", "text": "Git history skipped"}
    assert callback.warnings == ["Git history skipped"]


@pytest.mark.parametrize(
    ("raised", "status", "next_commands"),
    [
        (None, "ready", []),
        (SystemExit(0), "ready", []),
        (SystemExit(2), "degraded", []),
        (SystemExit(130), "interrupted", ["repowise init --resume"]),
        (click.ClickException("Not a directory"), "failed", []),
        (SystemExit(1), "failed", []),
    ],
)
def test_every_exit_ends_with_a_summary(tmp_path, capsys, raised, status, next_commands):
    expect = pytest.raises(type(raised)) if raised else contextlib.nullcontext()
    with expect, init_output("json", tmp_path):
        if raised:
            raise raised

    lines = _lines(capsys.readouterr().out)
    assert len(lines) == 1
    summary = lines[0]
    assert summary["event"] == "summary"
    assert summary["status"] == status
    assert set(summary) >= _SUMMARY_KEYS
    assert summary["next"] == next_commands
    assert summary["spend"] == {"tokens": 0, "usd": 0.0}
    if status == "failed" and isinstance(raised, click.ClickException):
        assert summary["error"] == "Not a directory"


def test_only_the_first_summary_is_written():
    buf = io.StringIO()
    events = agent_events.EventStream(buf)
    events.summary(agent_events.summary_fields("degraded", warnings=["x"]))
    events.summary(agent_events.summary_fields("ready"))
    assert [line["status"] for line in _lines(buf.getvalue())] == ["degraded"]


def test_status_follows_the_exit_code():
    assert agent_events.status_for_exit(None) == "ready"
    assert agent_events.status_for_exit(0) == "ready"
    assert agent_events.status_for_exit(2) == "degraded"
    assert agent_events.status_for_exit(130) == "interrupted"
    assert agent_events.status_for_exit(1) == "failed"


def test_off_a_terminal_lines_are_capped_and_panels_are_plain(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    buf = io.StringIO()
    console = Console(file=buf, width=400, theme=THEME)
    with line_output_width(console, buf):
        assert console.width == LINE_OUTPUT_WIDTH
        print_phase_header(console, 1, 4, "Ingestion", "Walking the tree")
        print_completion_panel(
            console,
            "repowise index complete",
            [("Files indexed", "1")],
            [("repowise serve", "open the dashboard")],
        )
    assert console.width == 400
    out = buf.getvalue()
    assert max(len(line) for line in out.splitlines()) <= LINE_OUTPUT_WIDTH
    assert "╭" not in out and "│" not in out
    assert "repowise serve" in out


def test_a_terminal_keeps_its_width(monkeypatch):
    monkeypatch.delenv("COLUMNS", raising=False)
    console = Console(file=io.StringIO(), width=180, force_terminal=True)
    stream = io.StringIO()
    stream.isatty = lambda: True  # type: ignore[method-assign]
    with line_output_width(console, stream):
        assert console.width == 180


def test_a_piped_init_stays_within_the_line_width(repo):
    result = _init(repo, "--no-editor-setup")

    assert result.exit_code == 0, result.output
    assert max(len(line) for line in result.stdout.splitlines()) <= LINE_OUTPUT_WIDTH
    assert "╭" not in result.stdout


def test_json_init_writes_only_events_and_ends_with_a_ready_summary(repo, monkeypatch):
    # No agent CLI probe: the model command is then the same on every machine.
    monkeypatch.setenv("REPOWISE_OFFLINE", "1")
    result = _init(repo, "--no-editor-setup", "--progress", "json")

    assert result.exit_code == 0, result.stderr
    lines = _lines(result.stdout)
    assert {line["event"] for line in lines} >= {"stage", "phase", "summary"}
    summary = lines[-1]
    assert summary["event"] == "summary"
    assert summary["status"] == "ready"
    assert summary["mode"] == {"key": "template", "label": "Built from code", "run_mode": "standard"}
    assert summary["restart_required"] is False
    assert summary["spend"] == {"tokens": 0, "usd": 0.0}
    assert summary["next"][:2] == ["repowise serve", "repowise generate"]
    findings = summary["findings"]
    assert set(findings) == {"health", "dead_code", "hotspot", "decision_candidates"}
    assert isinstance(findings["decision_candidates"], int)
    assert findings["dead_code"]["unreachable_files"] >= 0
    # The human screen still happens, on stderr.
    assert TITLE_BUILT_FROM_CODE in result.stderr


def test_json_init_degraded_summary_names_the_gap(repo, monkeypatch):
    from repowise.cli.commands.init_cmd import command

    real = _ingest.index_repository

    def _with_warning(run):
        real(run)
        run.run_warnings.append("Git history skipped: repository is shallow")

    monkeypatch.setattr(command, "index_repository", _with_warning)

    result = _init(repo, "--no-editor-setup", "--progress", "json")

    assert result.exit_code == 2, result.stderr
    summary = _lines(result.stdout)[-1]
    assert summary["status"] == "degraded"
    assert "Git history skipped: repository is shallow" in summary["warnings"]
    assert summary["mode"]["key"] == "template"


def test_json_init_interrupted_summary(repo, monkeypatch):
    async def _stop(*_a, **_k):
        raise KeyboardInterrupt

    monkeypatch.setattr(_ingest, "_index_with_resume", _stop)

    result = _init(repo, "--progress", "json")

    assert result.exit_code == 130, result.stderr
    summary = _lines(result.stdout)[-1]
    assert summary["status"] == "interrupted"
    assert summary["next"] == ["repowise init --resume"]
    assert "Interrupted." in result.stderr
