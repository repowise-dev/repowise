"""`repowise health --refactoring-targets` reads the queue the index stored.

The recompute parsed and analyzed the whole tree before printing, which took
minutes on a large repository, and composed its own order. The stored read
lists the same opportunities in the same order as MCP and the web UI, and
touches no parser. ``--recompute`` keeps the in-process path.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from repowise.cli.commands.health_cmd import health_command
from repowise.cli.helpers import run_async


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _plan(path: str, symbol: str, impact: float) -> dict[str, Any]:
    return {
        "refactoring_type": "extract_method",
        "file_path": path,
        "target_symbol": symbol,
        "line_start": 10,
        "line_end": 30,
        "plan": {"span": {"start": 12, "end": 28}, "params": ["a"], "returns": []},
        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
        "impact_delta": impact,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }


def _finding(path: str, symbol: str) -> dict[str, Any]:
    return {
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": "high",
        "function_name": symbol,
        "line_start": 10,
        "line_end": 30,
        # Past Fix first's size floor, so each opportunity is in the default scope.
        "details": {"ccn": 16},
        "health_impact": 3.0,
        "reason": "seeded",
        "dimension": "defect",
    }


_PATHS = [f"pkg{i % 3}/mod{i}.py" for i in range(6)]


async def _store(repo_path: Path, paths: list[str] = _PATHS) -> list[str]:
    """Seed one opportunity per file; return the stored queue order."""
    from repowise.core.analysis.health.refactoring.serving import parse_query
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        crud,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.server.services.refactoring_health import RefactoringHealthService

    db_path = repo_path / ".repowise" / "wiki.db"
    engine = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            await crud.save_health_findings(
                session, repo.id, [_finding(p, f"sym{i}") for i, p in enumerate(paths)]
            )
            await crud.save_refactoring_suggestions(
                session,
                repo.id,
                [_plan(p, f"sym{i}", float(len(paths) - i)) for i, p in enumerate(paths)],
            )
            await crud.finalize_refactoring_opportunities(
                session, repo.id, analyzed_commit="c" * 40
            )
            await session.commit()
            page = await RefactoringHealthService(session, repo.id, "repo").page(
                parse_query(limit=20)[0]
            )
            return [item["opportunity_id"] for item in page.items]
    finally:
        await engine.dispose()


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    root = tmp_path.resolve()
    _git(root, "init", "-q")
    (root / "a.py").write_text("def f():\n    return 1\n")
    (root / ".repowise").mkdir()
    return root


def _no_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if the command walks the tree."""
    import repowise.core.ingestion as ingestion

    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the stored read must not traverse the repository")

    monkeypatch.setattr(ingestion, "FileTraverser", boom)


def test_json_lists_the_stored_queue_in_the_served_order(repo, monkeypatch):
    served = run_async(_store(repo))
    _no_parse(monkeypatch)

    result = CliRunner().invoke(
        health_command,
        [str(repo), "--refactoring-targets", "--format", "json", "--no-workspace"],
    )

    assert result.exit_code == 0, result.output
    out = json.loads(result.output[result.output.index("{") :])
    assert out["source"] == "index"
    assert out["analyzed_commit"] == "c" * 40
    assert out["opportunities_total"] == len(_PATHS)
    assert [o["opportunity_id"] for o in out["refactoring_opportunities"]] == served
    # Every step's plan is carried, so the type-specific detail can render.
    step_ids = {s["plan_id"] for o in out["refactoring_opportunities"] for s in o["steps"]}
    assert step_ids <= {p["id"] for p in out["refactoring_plans"]}
    assert {t["file_path"] for t in out["targets"]} == set(_PATHS)


def test_markdown_renders_steps_and_their_plan_detail(repo, monkeypatch):
    run_async(_store(repo))
    _no_parse(monkeypatch)

    result = CliRunner().invoke(
        health_command,
        [str(repo), "--refactoring-targets", "--format", "md", "--no-workspace",
         "--module", "pkg1"],
    )

    assert result.exit_code == 0, result.output
    assert "## Refactoring opportunities" in result.output
    assert "extract_method **sym1**" in result.output
    assert "extract lines 12-28" in result.output
    # The module filter is a prefix over the stored rows.
    assert "pkg0/" not in result.output and "pkg2/" not in result.output


def test_no_stored_analysis_names_both_ways_forward(repo, monkeypatch):
    _no_parse(monkeypatch)

    result = CliRunner().invoke(
        health_command, [str(repo), "--refactoring-targets", "--no-workspace"]
    )

    assert result.exit_code != 0
    assert "--recompute" in result.output


def test_a_module_larger_than_the_page_reports_its_exact_total(repo, monkeypatch):
    """The prefix is matched in the store, so the total is counted, not capped."""
    wide = [f"big/mod{i}.py" for i in range(25)] + [f"other/mod{i}.py" for i in range(5)]
    run_async(_store(repo, wide))
    _no_parse(monkeypatch)

    result = CliRunner().invoke(
        health_command,
        [str(repo), "--refactoring-targets", "--format", "json", "--no-workspace",
         "--module", "big/"],
    )

    assert result.exit_code == 0, result.output
    out = json.loads(result.output[result.output.index("{") :])
    assert out["opportunities_total"] == 25
    assert len(out["refactoring_opportunities"]) == 20
    assert all(o["file_path"].startswith("big/") for o in out["refactoring_opportunities"])


def test_both_sources_emit_one_row_schema(repo, monkeypatch):
    from repowise.cli.commands.health_cmd.refactoring_targets import (
        _compose,
        _opportunity_row,
    )
    from repowise.core.analysis.health.refactoring.recommendations import (
        rehydrate_suggestion,
    )

    run_async(_store(repo))
    _no_parse(monkeypatch)
    result = CliRunner().invoke(
        health_command,
        [str(repo), "--refactoring-targets", "--format", "json", "--no-workspace"],
    )
    stored = json.loads(result.output[result.output.index("{") :])["refactoring_opportunities"][0]

    suggestions = [rehydrate_suggestion(_plan("pkg0/mod0.py", "sym0", 1.0))]
    recomputed = _opportunity_row(_compose([], suggestions)[0][0])

    assert set(recomputed) == set(stored)
    assert set(recomputed["steps"][0]) == set(stored["steps"][0])


def test_unreadable_store_yields_clean_message_without_traceback(repo, monkeypatch):
    run_async(_store(repo))
    _no_parse(monkeypatch)

    db_file = repo / ".repowise" / "wiki.db"
    subprocess.run(
        ["sqlite3", str(db_file), "DROP TABLE health_file_metrics; DROP TABLE refactoring_opportunities;"],
        check=True,
    )

    result = CliRunner().invoke(
        health_command, [str(repo), "--refactoring-targets", "--no-workspace"]
    )

    assert result.exit_code != 0
    assert "Traceback" not in result.output
    assert "No stored refactoring analysis" in result.output
    assert "repowise update" in result.output

