"""Init stamps the decision mining ledger it ran, so status reads "ran" at once.

Issue #6031: a keyless init mined the deterministic sources but wrote nothing
to the per-source ledger in ``state.json``, so ``decision status`` showed
Last run "-" for every source until the first update ran. A later failure
was then indistinguishable from never having run.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from click.testing import CliRunner

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


def _git_repo(root: Path) -> str:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=root, check=True)
    docs = root / "docs" / "adr"
    docs.mkdir(parents=True)
    (docs / "0001-use-x.md").write_text(
        "# 1. Use X\n\nDate: 2026-11-20\n\n"
        "## Status\n\nAccepted\n\n"
        "## Context\n\nWe need X.\n\n"
        "## Decision\n\nUse X.\n",
        encoding="utf-8",
    )
    app = root / "app"
    app.mkdir()
    (app / "loader.py").write_text(
        "# WHY: loaded here so imports stay lazy\n\ndef load():\n    return 1\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=root, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return head


def _init(repo: Path, *flags: str):
    from repowise.cli.main import cli

    return CliRunner(mix_stderr=False).invoke(
        cli, ["init", str(repo), "--no-workspace", "--yes", "--no-prose", *flags]
    )


def _mining_state(repo: Path) -> dict:
    state = json.loads((repo / ".repowise" / "state.json").read_text(encoding="utf-8"))
    return state.get("decision_mining", {})


def test_init_records_the_sources_it_ran(tmp_path, monkeypatch):
    for var in _KEYS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("REPOWISE_OFFLINE", "1")
    head = _git_repo(tmp_path)

    result = _init(tmp_path, "--no-editor-setup")

    assert result.exit_code == 0, result.output
    mining = _mining_state(tmp_path)
    # Every source the init extraction ran reads as a run through the init
    # head commit, adr among them (the source the issue reported).
    assert mining["adr"]["last_run_at"]
    assert mining["adr"]["last_status"] == "ok"
    assert mining["adr"]["last_success_commit"] == head
    assert mining["inline_marker"]["last_run_at"]
    assert mining["inline_marker"]["last_status"] == "ok"
    assert mining["inline_marker"]["last_success_commit"] == head
    # Session lanes are not mining sources; the ledger never names them.
    assert "session" not in mining
    assert "session_discovery" not in mining


def test_a_failed_source_records_the_failure_and_keeps_its_success():
    from repowise.core.analysis.decisions import mining_state
    from repowise.core.analysis.decisions.records import DecisionExtractionReport

    state: dict = {}
    report = DecisionExtractionReport(
        total_found=2,
        decisions=[],
        by_source={"adr": 0, "inline_marker": 2, "session": 1},
        failures={"adr": "ValueError: bad record"},
    )

    mining_state.record_extraction_report(state, report, "abc123")

    failed = state["decision_mining"]["adr"]
    assert failed["last_status"] == "failed"
    assert failed["last_failure"] == "ValueError: bad record"
    assert failed["last_run_commit"] == "abc123"
    # Same semantics as the update passes: a failure leaves the last
    # successful commit alone so a retry knows where to start.
    assert "last_success_commit" not in failed
    ok = state["decision_mining"]["inline_marker"]
    assert ok["last_status"] == "ok"
    assert ok["last_success_commit"] == "abc123"
    # The session lanes are not mining sources; update never records them
    # either, so a row here would freeze one run's date forever.
    assert "session" not in state["decision_mining"]


def test_no_report_writes_nothing():
    from repowise.core.analysis.decisions import mining_state

    state = {"decision_mining": {"adr": {"last_status": "ok"}}}

    # Extraction skipped (returned None) must not read as a run.
    mining_state.record_extraction_report(state, None, "abc123")

    assert state["decision_mining"] == {"adr": {"last_status": "ok"}}
