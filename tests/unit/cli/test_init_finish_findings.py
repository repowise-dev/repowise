"""What the init finish panel measured, the question it asks first, and the JSON summary."""

from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace

import pytest
from rich.console import Console
from sqlalchemy.exc import OperationalError

from repowise.cli.commands.init_cmd import reporting
from repowise.cli.commands.init_cmd._agent_summary import run_summary
from repowise.cli.editor_setup import EditorSetupOutcome
from repowise.cli.ui.brand import THEME
from repowise.cli.ui.finish_panel import StoodOut
from repowise.core.analysis.dead_code.models import DeadCodeKind


def _dead(path: str, safe: bool, kind: DeadCodeKind = DeadCodeKind.UNREACHABLE_FILE):
    return SimpleNamespace(kind=kind, file_path=path, confidence=0.99, safe_to_delete=safe)


def _result(**overrides) -> SimpleNamespace:
    fields = dict(
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
    """A store that reports 14 candidates."""
    monkeypatch.setattr(reporting, "_decision_counts", lambda _repo: {"decision_candidates": 14})


# -- the findings ------------------------------------------------------------------


def test_findings_come_from_the_run(no_probe):
    from repowise.core.analysis.dead_code.serving import is_safe

    result = _result()
    stood = reporting._stood_out(result, Path("/r"), reporting._top_hotspot(result))

    assert stood.health_score == 7.43
    assert stood.health_band == "good"
    assert (stood.lowest_file, stood.lowest_score) == ("scheduler/planner.py", 3.12)
    assert stood.dead_unreachable == 2
    # A whole file is review-only; the safe count is over every finding.
    assert stood.dead_safe == sum(1 for f in result.dead_code_report.findings if is_safe(f)) == 1
    assert (stood.hotspot_file, stood.hotspot_commits) == ("auth/session.py", 48)
    assert stood.decision_candidates == 14


def test_docs_and_examples_stay_out_of_the_unreachable_headline(no_probe):
    """Listed for review, never counted: the stored origin decides, the path backs it up."""
    findings = [
        _dead("src/legacy/old_report.py", False),
        _dead("examples/demo/run.py", False),
        _dead("website/snippets/sample.py", False),
    ]
    metrics = [SimpleNamespace(file_path="website/snippets/sample.py", code_origin="docs_example")]
    result = _result(
        dead_code_report=SimpleNamespace(findings=findings),
        health_report=SimpleNamespace(kpis={}, metrics=metrics, findings=[]),
    )
    stood = reporting._stood_out(result, Path("/r"), reporting._top_hotspot(result))
    assert stood.dead_unreachable == 1


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
    assert finding["health_band"] is None


def test_candidates_are_none_without_a_store(tmp_path):
    assert reporting._decision_counts(tmp_path) == {}


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


def test_completion_leads_with_the_hotspot_and_asks_about_it(monkeypatch, tmp_path, no_probe):
    completion, out, _ = _complete(monkeypatch, tmp_path, index_only=True)
    assert "What stood out" in out
    assert "auth/session.py, changed in 48 commits" in out
    assert "health, dead code and 14 background records" in out
    assert completion.first_prompt == (
        "Before I change auth/session.py, use Repowise to tell me what depends on it, "
        "who knows it best, and what tends to break with it."
    )
    assert "Then ask your agent" in out
    assert [c for c, _ in completion.steps] == ["repowise serve"]
    assert completion.steps[0][1].startswith("open the dashboard (keeps running")
    assert "Add a model" not in out


def test_without_a_hotspot_the_first_question_is_the_tour(monkeypatch, tmp_path, no_probe):
    buf = io.StringIO()
    monkeypatch.setattr(reporting, "console", Console(file=buf, width=120, theme=THEME))
    monkeypatch.setattr("repowise.cli.hints.maybe_hint", lambda h, **_kw: None)
    completion = reporting.show_completion(
        repo_path=tmp_path,
        result=_result(git_summary=SimpleNamespace(files_indexed=3, hotspots=0)),
        start=0.0,
        effective_index_only=True,
        run_mode="standard",
        provider=None,
        setup=EditorSetupOutcome(),
    )
    assert completion.first_prompt.startswith(f"Use Repowise to give me a tour of {tmp_path.name}:")


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


def test_summary_carries_the_findings_restart_and_first_prompt(tmp_path):
    run = SimpleNamespace(
        repo_path=tmp_path,
        setup_outcome=EditorSetupOutcome(
            restart_hosts=(
                {"id": "claude-code", "name": "Claude Code"},
                {"id": "vscode", "name": "VS Code"},
            ),
            unconnected=({"id": "cursor", "name": "Cursor"},),
        ),
        mcp_repointed=False,
        result=_result(),
        run_warnings=[],
        files_written=[],
        next_steps=[("repowise serve", "dashboard"), ("repowise update --full", "x")],
        stood_out=StoodOut(
            health_score=7.43,
            health_band="good",
            lowest_file="scheduler/planner.py",
            lowest_score=3.12,
            dead_unreachable=38,
            dead_safe=12,
            hotspot_file="auth/session.py",
            hotspot_commits=48,
            decision_candidates=14,
        ),
        first_prompt="Before I change auth/session.py, use Repowise to tell me ...",
    )
    summary = run_summary(run, [])
    assert summary["findings"] == {
        "health": {
            "score": 7.43,
            "band": "good",
            "lowest_file": "scheduler/planner.py",
            "lowest_score": 3.12,
        },
        "dead_code": {"unreachable_files": 38, "findings_safe_to_delete": 12},
        "hotspot": {"file": "auth/session.py", "commits": 48},
        "decision_candidates": 14,
    }
    # Every host this run wrote, not only Claude Code.
    assert summary["restart_required"] is True
    assert [h["name"] for h in summary["restart_hosts"]] == ["Claude Code", "VS Code"]
    assert summary["connect"] == [
        {"id": "cursor", "name": "Cursor", "command": "repowise agents add --target=cursor --yes"}
    ]
    # serve does not return: it goes last, and long_running names it.
    assert summary["next"] == ["repowise update --full", "repowise serve"]
    assert summary["long_running"] == ["repowise serve"]
    assert summary["first_prompt"] == run.first_prompt
    assert summary["report"] is None  # init wrote no repo report


def test_summary_names_the_repo_report_for_an_agent(tmp_path):
    from repowise.cli.ui.finish_panel import FirstLook

    run = SimpleNamespace(
        repo_path=tmp_path, setup_outcome=None, mcp_repointed=False, result=_result(),
        run_warnings=[], files_written=[], next_steps=[], stood_out=None, first_prompt=None,
        first_look=FirstLook(".repowise/reports/repo-report.html", agent=True, open_items=7),
    )
    assert run_summary(run, [])["report"] == {
        "open_items": 7,
        "summary_command": "repowise report --format md",
        "json_command": "repowise report --format json",
    }


def test_no_host_to_restart_means_no_restart(tmp_path):
    run = SimpleNamespace(
        repo_path=tmp_path, setup_outcome=EditorSetupOutcome(), result=_result(),
        run_warnings=[], files_written=[], next_steps=[], stood_out=None, first_prompt=None,
    )
    summary = run_summary(run, [])
    assert summary["restart_required"] is False and summary["restart_hosts"] == []
    assert summary["next"] == ["repowise serve"] and summary["first_prompt"] is None


def test_summary_findings_are_null_when_nothing_was_measured(tmp_path):
    run = SimpleNamespace(
        repo_path=tmp_path, setup_outcome=None, mcp_repointed=False, result=_result(),
        run_warnings=[], files_written=[], next_steps=[], stood_out=StoodOut(), first_prompt=None,
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

    monkeypatch.setattr(reporting, "_count_decisions", _raise)
    logged: list[str] = []
    monkeypatch.setattr(reporting.logger, "exception", lambda event, **_kw: logged.append(event))

    completion, out, _ = _complete(monkeypatch, tmp_path, index_only=True)

    assert completion.stood_out.decision_candidates is None
    assert "background record" not in out and "Decisions" not in out
    assert completion.stood_out.as_summary()["decision_candidates"] is None
    # Only an error a store cannot explain is logged as a bug.
    assert logged == (["decision_candidate_count_failed"] if logged_as_bug else [])


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        ("'off'", {}),
        ("read_only", {"decision_candidates": 3}),
        ("full", {"decision_candidates": 3}),
    ],
)
def test_the_mode_decides_the_decisions_finding(
    monkeypatch, tmp_path, mode, expected
):
    (tmp_path / ".repowise").mkdir()
    (tmp_path / ".repowise" / "config.yaml").write_text(
        f"decisions:\n  mode: {mode}\n", encoding="utf-8"
    )

    async def counts(_repo):
        return {"decision_candidates": 3}

    monkeypatch.setattr(reporting, "_count_decisions", counts)

    assert reporting._decision_counts(tmp_path) == expected
