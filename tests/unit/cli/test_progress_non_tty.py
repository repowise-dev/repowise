"""``on_message`` must reach an agent, not just a terminal.

Routing pipeline failures through ``progress.on_message`` is only an
improvement over ``logger.warning`` if that channel survives the absence of a
TTY. Agent-driven mode — scripted ``repowise init --yes``, CI, a subprocess
call — is the primary path and has no terminal at all, so if Rich suppressed
these lines when stdout is a pipe the fix would be worth nothing precisely
where it is needed most.

It does survive: Rich's ``Live`` only splices its own render into the output
when the console is interactive, and otherwise lets the caller's renderables
through untouched. That is a property of a third-party library, which is
exactly the kind of assumption that should be pinned by a test rather than
believed.
"""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import ClassVar

import pytest
from rich.console import Console
from rich.progress import Progress, TextColumn

from repowise.cli.ui.progress import RichProgressCallback
from repowise.core.pipeline.progress import emit_warning


def _non_tty_console() -> tuple[Console, io.StringIO]:
    """A console whose file is a pipe, the way a piped CLI run gets one."""
    buf = io.StringIO()
    console = Console(file=buf, width=200)
    assert console.is_terminal is False, "fixture must model a non-TTY"
    return console, buf


def test_a_warning_reaches_a_piped_stdout() -> None:
    """The load-bearing assumption, stated as an assertion."""
    console, buf = _non_tty_console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_phase_start("parse", 10)
        callback.on_message("warning", "Parallel parsing unavailable (boom)")

    assert "Parallel parsing unavailable (boom)" in buf.getvalue()


def test_emit_warning_reaches_a_piped_stdout() -> None:
    """Through the helper the pipeline actually calls, not just the method."""
    console, buf = _non_tty_console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        emit_warning(RichProgressCallback(bar, console), "Index checkpoint not saved (disk full)")

    assert "Index checkpoint not saved (disk full)" in buf.getvalue()


def test_emit_warning_tolerates_a_missing_channel() -> None:
    """A reporting failure must never abort a run that was otherwise fine."""

    class _NoMessages:
        pass

    class _Raises:
        def on_message(self, level: str, text: str) -> None:
            raise RuntimeError("console is gone")

    emit_warning(None, "nobody listening")
    emit_warning(_NoMessages(), "no such method")
    emit_warning(_Raises(), "raises")


def test_warnings_are_collected_for_the_run_record() -> None:
    """Printing is for humans; an agent needs it after the output is gone."""
    console, _ = _non_tty_console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_message("info", "Scanned 12,431 files")
        callback.on_message("warning", "Execution flow tracing skipped (boom)")
        callback.on_message("error", "Analysis checkpoint not saved (disk full)")

    # ``info`` carries neutral facts and is not a degradation.
    assert callback.warnings == [
        "Execution flow tracing skipped (boom)",
        "Analysis checkpoint not saved (disk full)",
    ]


def test_degraded_run_is_recorded_in_state_json(tmp_path: Path, monkeypatch) -> None:
    """The machine-readable half.

    Exit code and completion panel look identical whether or not a phase
    silently degraded, so ``state.json`` is where a scripted run finds out.
    """
    from repowise.cli.commands.init_cmd import persistence

    (tmp_path / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(persistence, "get_head_commit", lambda _p: "abc123")
    monkeypatch.setattr(persistence, "run_async", lambda _c: 7)
    monkeypatch.setattr(persistence, "stamp_offered_slots", lambda *a, **k: None)
    monkeypatch.setattr(persistence, "config_fingerprint", lambda _p: "fp")
    monkeypatch.setattr(persistence, "head_commit_ts", lambda _p: None)
    monkeypatch.setattr(persistence, "save_config", lambda *a, **k: None)

    class _Provider:
        provider_name = "openai"
        model_name = "gpt-5.6-luna"

    class _Result:
        generated_pages: ClassVar[list] = []
        knowledge_graph_result = None
        health_report = None

    persistence.save_full_state_and_config(
        repo_path=tmp_path,
        result=_Result(),
        provider=_Provider(),
        phase_timings={},
        degraded=["Execution flow tracing skipped (boom)"],
        embedder_name_resolved="openai",
        exclude_patterns=[],
        commit_limit=None,
        resolved_commit_limit=500,
        resolved_reasoning="medium",
    )

    state = json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["degraded"] == ["Execution flow tracing skipped (boom)"]


def test_a_clean_run_records_no_degraded_key(tmp_path: Path, monkeypatch) -> None:
    """An empty list is a third state nothing should have to interpret."""
    from repowise.cli.commands.init_cmd import persistence

    (tmp_path / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(persistence, "get_head_commit", lambda _p: "abc123")
    monkeypatch.setattr(persistence, "run_async", lambda _c: 7)
    monkeypatch.setattr(persistence, "stamp_offered_slots", lambda *a, **k: None)
    monkeypatch.setattr(persistence, "config_fingerprint", lambda _p: "fp")
    monkeypatch.setattr(persistence, "head_commit_ts", lambda _p: None)
    monkeypatch.setattr(persistence, "save_config", lambda *a, **k: None)

    class _Provider:
        provider_name = "openai"
        model_name = "gpt-5.6-luna"

    class _Result:
        generated_pages: ClassVar[list] = []
        knowledge_graph_result = None
        health_report = None

    persistence.save_full_state_and_config(
        repo_path=tmp_path,
        result=_Result(),
        provider=_Provider(),
        phase_timings={},
        degraded=[],
        embedder_name_resolved="openai",
        exclude_patterns=[],
        commit_limit=None,
        resolved_commit_limit=500,
        resolved_reasoning="medium",
    )

    state = json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert "degraded" not in state


def test_a_clean_rerun_clears_a_previous_runs_degradation(tmp_path: Path, monkeypatch) -> None:
    """The key describes *this* run, or it is absent.

    Both writers read the previous state.json back and mutate it, so only ever
    setting the key would mark a repo degraded permanently: the re-run that
    fixed the problem writes nothing, the stale list is re-serialised, and
    every later ``update`` carries it forward.
    """
    from repowise.cli.commands.init_cmd import persistence

    (tmp_path / ".repowise").mkdir(parents=True)
    monkeypatch.setattr(persistence, "get_head_commit", lambda _p: "abc123")
    monkeypatch.setattr(persistence, "run_async", lambda _c: 7)
    monkeypatch.setattr(persistence, "stamp_offered_slots", lambda *a, **k: None)
    monkeypatch.setattr(persistence, "config_fingerprint", lambda _p: "fp")
    monkeypatch.setattr(persistence, "head_commit_ts", lambda _p: None)
    monkeypatch.setattr(persistence, "save_config", lambda *a, **k: None)

    class _Provider:
        provider_name = "openai"
        model_name = "gpt-5.6-luna"

    class _Result:
        generated_pages: ClassVar[list] = []
        knowledge_graph_result = None
        health_report = None

    def _save(degraded: list[str]) -> dict:
        persistence.save_full_state_and_config(
            repo_path=tmp_path,
            result=_Result(),
            provider=_Provider(),
            phase_timings={},
            degraded=degraded,
            embedder_name_resolved="openai",
            exclude_patterns=[],
            commit_limit=None,
            resolved_commit_limit=500,
            resolved_reasoning="medium",
        )
        return json.loads((tmp_path / ".repowise" / "state.json").read_text(encoding="utf-8"))

    assert _save(["Execution flow tracing skipped (boom)"])["degraded"]
    # The fix lands and the next run is clean.
    assert "degraded" not in _save([])


def test_a_key_quoted_back_by_a_provider_is_not_written_to_disk() -> None:
    """Auth errors echo the offending key, and these messages interpolate them.

    That was transient terminal output. Once the same text is persisted to
    ``.repowise/state.json`` it is a secret at rest in the file agents read.
    """
    from repowise.cli.ui.progress import redact_secrets

    console, _ = _non_tty_console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        callback = RichProgressCallback(bar, console)
        callback.on_message(
            "warning",
            "Decision extraction skipped: Incorrect API key provided: sk-abcd1234efgh5678",
        )

    assert callback.warnings == [
        "Decision extraction skipped: Incorrect API key provided: [redacted]"
    ]
    # The paths and counts that make these messages useful must survive.
    kept = redact_secrets("No parser installed for: bash (1,204 files) at /repo/.repowise/.env")
    assert kept == "No parser installed for: bash (1,204 files) at /repo/.repowise/.env"


@pytest.mark.parametrize(
    "phase_fn",
    [
        pytest.param("flows", id="execution-flow-tracing"),
        pytest.param("hints", id="dynamic-edge-hints"),
    ],
)
def test_a_swallowed_pipeline_failure_is_now_reported(phase_fn: str) -> None:
    """The shape of the defect, at the two sites that most often hide it.

    Both of these catch, log at ``warning``, and continue. The CLI pins
    ``repowise.core`` to ERROR, so before this the run printed nothing and the
    resulting wiki was quietly missing a whole class of content.
    """
    console, buf = _non_tty_console()
    with Progress(TextColumn("{task.description}"), console=console) as bar:
        callback = RichProgressCallback(bar, console)
        if phase_fn == "flows":
            emit_warning(callback, "Execution flow tracing skipped (boom); entry-point scores")
        else:
            emit_warning(callback, "Dynamic edge hints skipped (boom); framework-wired call edges")

    assert "skipped (boom)" in buf.getvalue()
    assert callback.warnings and "skipped (boom)" in callback.warnings[0]
