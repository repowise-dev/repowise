"""`repowise init` exit codes: 0 ready, 2 finished with gaps, 130 interrupted."""

from __future__ import annotations

import json
import signal
import sys
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from repowise.cli.commands.init_cmd import _generate, _ingest, command
from repowise.cli.commands.init_cmd._exit import exit_on_gaps, run_gaps
from repowise.cli.exit_codes import EXIT_DEGRADED, EXIT_INTERRUPTED
from repowise.cli.main import cli
from repowise.core.cancellation import (
    CancellationToken,
    PipelineCancelled,
    set_active_token,
)

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


@pytest.fixture
def repo(tmp_path, monkeypatch):
    for var in _KEYS:
        monkeypatch.delenv(var, raising=False)
    (tmp_path / "app.py").write_text("def main():\n    return 1\n", encoding="utf-8")
    return tmp_path


def _init(repo, *flags):
    return CliRunner().invoke(cli, ["init", str(repo), "--no-workspace", *flags])


@pytest.mark.parametrize("signal", [KeyboardInterrupt, PipelineCancelled])
def test_interrupt_while_indexing_exits_130(repo, monkeypatch, signal):
    async def _stop(*_a, **_k):
        raise signal

    monkeypatch.setattr(_ingest, "_index_with_resume", _stop)

    result = _init(repo, "--no-prose", "--yes")

    assert result.exit_code == EXIT_INTERRUPTED, result.output
    assert "Interrupted." in result.output
    assert "repowise init --resume" in result.output


def test_interrupt_after_indexing_exits_130(repo, monkeypatch):
    """Ctrl-C outside the pipeline's own handler, e.g. during generation."""

    def _stop(_run):
        raise KeyboardInterrupt

    monkeypatch.setattr(command, "_generate", _stop)

    result = _init(repo, "--no-prose", "--yes")

    assert result.exit_code == EXIT_INTERRUPTED, result.output
    assert "repowise init --resume" in result.output


def test_a_run_with_warnings_exits_degraded(repo, monkeypatch):
    """A degraded result: the index is saved and the last line says why."""
    real = _ingest.index_repository

    def _with_warning(run):
        real(run)
        run.run_warnings.append("Git history skipped: repository is shallow")

    monkeypatch.setattr(command, "index_repository", _with_warning)

    result = _init(repo, "--no-prose", "--yes")

    assert result.exit_code == EXIT_DEGRADED, result.output
    assert "Finished with gaps: 1 warning(s) while indexing (see above)." in " ".join(result.output.split())
    assert (repo / ".repowise" / "state.json").exists()


def test_unconfirmed_cost_builds_from_code_and_exits_degraded(repo, monkeypatch):
    """No terminal and no --yes over the gate: built from code, exit 2, names --yes."""
    est = SimpleNamespace(estimated_cost_usd=9.0, cost_range=None, is_calibrated=False)
    monkeypatch.setattr(_generate, "estimate_generation", lambda **_k: ([], est))
    monkeypatch.setattr(_generate, "_print_generation_plan", lambda *_a, **_k: None)

    result = _init(repo, "--provider", "mock")

    assert result.exit_code == EXIT_DEGRADED, result.output
    out = " ".join(result.output.split())
    assert "Finished with gaps: documentation built from code" in out
    assert "Re-run with --yes to accept the cost" in out
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    assert state["docs_mode"] == "deterministic"


def test_overwrite_guard_names_no_prose(repo):
    (repo / ".repowise").mkdir()
    (repo / ".repowise" / "state.json").write_text(
        json.dumps({"docs_mode": "llm", "last_sync_commit": "abc"}), encoding="utf-8"
    )

    result = _init(repo, "--no-prose")

    assert result.exit_code == 1, result.output
    out = " ".join(result.output.split())
    assert "drop --no-prose" in out
    assert "--index-only" not in out


def test_help_names_the_exit_codes():
    result = CliRunner().invoke(cli, ["init", "--help"])
    assert "Exit codes: 0 ready, 2 finished with gaps" in " ".join(result.output.split())


def test_no_gaps_returns_and_any_gap_exits_degraded():
    assert run_gaps(failed_pages=0, embed_error=None) == []
    exit_on_gaps([])
    gaps = run_gaps(failed_pages=3, embed_error="Embedding failed for 1 page(s).")
    assert gaps == ["3 page(s) failed to generate", "Embedding failed for 1 page(s)"]
    with pytest.raises(SystemExit) as exc:
        exit_on_gaps(gaps)
    assert exc.value.code == EXIT_DEGRADED


def test_a_first_interrupt_stops_at_the_next_step_start_but_lets_a_checkpoint_run():
    started = []
    progress = _ingest._StopAtStepStart(
        SimpleNamespace(on_phase_start=lambda phase, total: started.append(phase))
    )
    token = CancellationToken()
    set_active_token(token)
    try:
        progress.on_phase_start("parse", 3)
        token.cancel()
        progress.on_phase_start("checkpoint.index", None)
        with pytest.raises(PipelineCancelled):
            progress.on_phase_start("graph.metrics", 4)
    finally:
        set_active_token(None)

    assert started == ["parse", "checkpoint.index"]


@pytest.mark.skipif(sys.platform == "win32", reason="raises SIGINT in this process")
def test_a_first_ctrl_c_is_acknowledged_once_and_exits_130(repo, monkeypatch):
    from repowise.core.pipeline.phases import ingestion

    real = ingestion._build_graph

    async def _ctrl_c_then_build(*args, **kwargs):
        signal.raise_signal(signal.SIGINT)
        await real(*args, **kwargs)

    monkeypatch.setattr(ingestion, "_build_graph", _ctrl_c_then_build)

    result = _init(repo, "--no-prose", "--yes")

    assert result.exit_code == EXIT_INTERRUPTED, result.output
    out = " ".join(result.output.split())
    assert out.count(_ingest.STOPPING_NOTICE) == 1
    assert "repowise init --resume" in out
