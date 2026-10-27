"""What the init finish panel measured, the model command it names, and the JSON summary."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from rich.console import Console
from sqlalchemy.exc import OperationalError

from repowise.cli.commands.init_cmd import reporting
from repowise.cli.commands.init_cmd._agent_summary import run_summary
from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.finish_panel import ModelUpgrade, StoodOut
from repowise.cli.ui.subscription import CliAuth
from repowise.core.analysis.dead_code.models import DeadCodeKind


def _dead(path: str, safe: bool, kind: DeadCodeKind = DeadCodeKind.UNREACHABLE_FILE):
    return SimpleNamespace(kind=kind, file_path=path, confidence=0.99, safe_to_delete=safe)


def _result(**overrides) -> SimpleNamespace:
    graph = MagicMock()
    graph.number_of_nodes.return_value = 10
    graph.number_of_edges.return_value = 5
    fields = dict(
        graph_builder=MagicMock(graph=lambda: graph),
        dead_code_report=SimpleNamespace(
            findings=[
                _dead("src/legacy/old_report.py", True),
                _dead("src/legacy/old_export.py", False),
                _dead("src/api.py", True, DeadCodeKind.UNUSED_EXPORT),
            ]
        ),
        decision_report=None,
        git_summary=SimpleNamespace(files_indexed=3, hotspots=1),
        git_meta_map={"auth/session.py": {"commit_count": 48}, "a.py": {"commit_count": 2}},
        health_report=SimpleNamespace(
            kpis={
                "average_health": 7.43,
                "analysed_share": 1.0,
                "worst_performer_path": "scheduler/planner.py",
                "worst_performer_score": 3.12,
            },
            metrics=[],
            findings=[],
        ),
        repo_structure=SimpleNamespace(root_language_distribution={"Python": 1.0}),
        generated_pages=[],
        failed_page_ids=[],
        file_count=3,
        symbol_count=42,
    )
    return SimpleNamespace(**{**fields, **overrides})


@pytest.fixture
def no_probe(monkeypatch):
    """No agent CLI, no key, and a store that reports 14 candidates."""
    monkeypatch.setattr(reporting, "_signed_in_agent", lambda: None)
    monkeypatch.setattr(reporting, "_keyed_provider", lambda _repo: None)
    monkeypatch.setattr(reporting, "_decision_candidates", lambda _repo: 14)


# -- the findings ------------------------------------------------------------------


def test_findings_come_from_the_run(no_probe):
    from repowise.core.analysis.dead_code.serving import is_safe

    result = _result()
    stood = reporting._stood_out(result, Path("/r"), reporting._top_hotspot(result))

    assert stood.health_score == 7.43
    assert stood.health_mark == "warning"
    assert (stood.lowest_file, stood.lowest_score) == ("scheduler/planner.py", 3.12)
    assert stood.dead_unreachable == 2
    # A whole file is review-only; the safe count is over every finding.
    assert stood.dead_safe == sum(1 for f in result.dead_code_report.findings if is_safe(f)) == 1
    assert (stood.hotspot_file, stood.hotspot_commits) == ("auth/session.py", 48)
    assert stood.decision_candidates == 14


def test_unmeasured_findings_stay_none(no_probe):
    result = _result(health_report=None, dead_code_report=None, git_meta_map={})
    stood = reporting._stood_out(result, Path("/r"), reporting._top_hotspot(result))
    assert stood.health_score is None and stood.dead_unreachable is None
    assert stood.hotspot_file is None


def test_too_little_analysed_keeps_the_score_and_drops_the_word():
    result = _result(
        health_report=SimpleNamespace(kpis={"average_health": 9.1, "analysed_share": 0.2})
    )
    finding = reporting._health_finding(result)
    assert finding["health_score"] == 9.1
    assert finding["health_mark"] is None


def test_candidates_are_none_without_a_store(tmp_path):
    assert reporting._decision_candidates(tmp_path) is None


# -- the model command -----------------------------------------------------------


def test_a_signed_in_cli_comes_first(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "repowise.cli.ui.subscription.logged_in_agent_providers",
        lambda: {
            "codex_cli": CliAuth("signed in with ChatGPT", offer=False),
            "claude_cli": CliAuth("Claude max plan: uses your plan quota"),
        },
    )
    monkeypatch.setattr(reporting, "_keyed_provider", lambda _repo: ("openai", "x"))
    upgrade = reporting._model_upgrade(tmp_path)
    assert upgrade == ModelUpgrade(
        "repowise generate --provider claude_cli", "Uses your Claude Code sign-in"
    )


@pytest.mark.parametrize("var", ["REPOWISE_OFFLINE", "DO_NOT_TRACK"])
def test_no_cli_is_probed_with_the_network_off(monkeypatch, tmp_path, var):
    monkeypatch.setenv(var, "1")
    monkeypatch.setattr(
        "repowise.core.agents.identity.AgentIdentity.which", lambda self: self.executable
    )
    monkeypatch.setattr(
        "repowise.cli.ui.subscription.subprocess.run",
        lambda *a, **k: pytest.fail("probed an agent CLI"),
    )
    monkeypatch.setattr(reporting, "_keyed_provider", lambda _repo: None)
    assert reporting._model_upgrade(tmp_path) == ModelUpgrade("repowise generate")


def test_a_key_in_the_repo_env_is_named(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "_signed_in_agent", lambda: None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.resolve.credentialed_providers", lambda _repo: ("anthropic",)
    )
    upgrade = reporting._model_upgrade(tmp_path)
    assert upgrade == ModelUpgrade("repowise generate --provider anthropic", "Uses your Anthropic key")


def test_nothing_detected_names_the_bare_command(monkeypatch, tmp_path):
    monkeypatch.setattr(reporting, "_signed_in_agent", lambda: None)
    monkeypatch.setattr(
        "repowise.core.providers.llm.resolve.credentialed_providers", lambda _repo: ()
    )
    assert reporting._model_upgrade(tmp_path) == ModelUpgrade("repowise generate")


# -- show_completion: the panel, the steps and the hint ----------------------------


def _complete(monkeypatch, tmp_path, *, index_only: bool, first_index: bool = False):
    buf = io.StringIO()
    monkeypatch.setattr(
        reporting, "console", Console(file=buf, width=120, force_terminal=False, theme=THEME)
    )
    hints: list[str] = []
    monkeypatch.setattr("repowise.cli.hints.maybe_hint", lambda h, **_kw: hints.append(h))
    completion = reporting.show_completion(
        repo_path=tmp_path,
        result=_result(),
        start=0.0,
        effective_index_only=index_only,
        run_mode="standard",
        provider=None,
        setup=EditorSetupOutcome(first_index=first_index),
    )
    return completion, buf.getvalue(), hints


def test_keyless_completion_shows_findings_model_block_and_tour(monkeypatch, tmp_path, no_probe):
    completion, out, _ = _complete(monkeypatch, tmp_path, index_only=True)
    assert "What stood out" in out
    assert "7.4/10 Warning" in out and "14 candidates to review" in out
    assert "Add a model when you want more" in out
    assert completion.model_upgrade == ModelUpgrade("repowise generate")
    assert completion.steps[0][0] == "repowise serve"
    assert completion.steps[0][1].startswith("a guided tour of all this in your browser")
    assert not any(c.startswith("repowise generate") for c, _ in completion.steps)


def test_model_completion_has_no_model_block(monkeypatch, tmp_path, no_probe):
    completion, out, _ = _complete(monkeypatch, tmp_path, index_only=False)
    assert "What stood out" in out
    assert "Add a model" not in out
    assert completion.model_upgrade is None


def test_first_index_never_shows_the_init_success_hint(monkeypatch, tmp_path, no_probe):
    assert _complete(monkeypatch, tmp_path, index_only=True, first_index=True)[2] == []
    assert _complete(monkeypatch, tmp_path, index_only=True)[2] == ["init_success"]


def test_completion_hint_rule():
    assert reporting.completion_hint("standard", 10, first_index=True) is None
    assert reporting.completion_hint("standard", 10) == "init_success"
    # Big repos keep their freshness hint either way.
    assert reporting.completion_hint("standard", 5000, first_index=True) == "large_repo"
    assert reporting.completion_hint("fast", 10, first_index=True) == "large_repo"


# -- --progress json -------------------------------------------------------------------


def test_summary_carries_the_findings_and_the_model_command(tmp_path):
    run = SimpleNamespace(
        repo_path=tmp_path,
        setup_outcome=None,
        mcp_repointed=False,
        result=_result(),
        run_warnings=[],
        files_written=[],
        next_steps=[("repowise serve", "tour"), ("repowise dead-code", "x")],
        stood_out=StoodOut(
            health_score=7.43,
            health_mark="warning",
            lowest_file="scheduler/planner.py",
            lowest_score=3.12,
            dead_unreachable=38,
            dead_safe=12,
            hotspot_file="auth/session.py",
            hotspot_commits=48,
            decision_candidates=14,
        ),
        model_upgrade=ModelUpgrade("repowise generate --provider claude_cli", "x"),
    )
    summary = run_summary(run, [])
    assert summary["findings"] == {
        "health": {
            "score": 7.43,
            "band": "warning",
            "lowest_file": "scheduler/planner.py",
            "lowest_score": 3.12,
        },
        "dead_code": {"unreachable_files": 38, "findings_safe_to_delete": 12},
        "hotspot": {"file": "auth/session.py", "commits": 48},
        "decision_candidates": 14,
    }
    assert summary["next"] == [
        "repowise serve",
        "repowise generate --provider claude_cli",
        "repowise dead-code",
    ]


def test_summary_findings_are_null_when_nothing_was_measured(tmp_path):
    run = SimpleNamespace(
        repo_path=tmp_path, setup_outcome=None, mcp_repointed=False, result=_result(),
        run_warnings=[], files_written=[], next_steps=[], stood_out=StoodOut(), model_upgrade=None,
    )
    assert run_summary(run, [])["findings"] == {
        "health": None, "dead_code": None, "hotspot": None, "decision_candidates": None,
    }


@pytest.mark.parametrize(
    ("error", "logged_as_bug"),
    [
        (OperationalError("SELECT", {}, Exception("no such table")), False),
        (RuntimeError("a bug in the count"), True),
    ],
    ids=["unreadable-store", "unexpected"],
)
def test_a_failed_candidate_count_drops_the_row_and_is_null(
    monkeypatch, tmp_path, error, logged_as_bug
):
    async def _raise(_repo):
        raise error

    monkeypatch.setattr(reporting, "_count_candidates", _raise)
    monkeypatch.setattr(reporting, "_signed_in_agent", lambda: None)
    monkeypatch.setattr(reporting, "_keyed_provider", lambda _repo: None)
    logged: list[str] = []
    monkeypatch.setattr(reporting.logger, "exception", lambda event, **_kw: logged.append(event))

    completion, out, _ = _complete(monkeypatch, tmp_path, index_only=True)

    assert completion.stood_out.decision_candidates is None
    assert "candidates to review" not in out and "Decisions" not in out
    assert completion.stood_out.as_summary()["decision_candidates"] is None
    # Only an error a store cannot explain is logged as a bug.
    assert logged == (["decision_candidate_count_failed"] if logged_as_bug else [])
