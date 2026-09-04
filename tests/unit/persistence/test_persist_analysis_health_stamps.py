"""What the analysis phase must have written by the time it returns.

Two orderings inside :func:`persist_analysis` are load-bearing. The commit the
scores were computed against is read off disk, because the pipeline result does
not carry one. And the governance findings land *after* the writer that folds
findings into the refactoring queue, so that queue has to be composed a second
time or it describes a repository whose governance causes do not exist.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.analysis.health.governance import HealthFindingData, Severity
from repowise.core.persistence.models import (
    HealthFileMetric,
    RefactoringOpportunity,
)
from repowise.core.pipeline.persist import persist_analysis
from tests.unit.persistence.helpers import insert_repo

_PATH = "src/app.py"


def _metric(path: str = _PATH) -> dict:
    return {
        "file_path": path,
        "score": 5.0,
        "max_ccn": 3,
        "max_nesting": 1,
        "nloc": 40,
        "duplication_pct": 0.0,
        "has_test_file": False,
        "line_coverage_pct": None,
        "branch_coverage_pct": None,
        "module": "src",
    }


def _finding(path: str = _PATH, *, impact: float = 1.0) -> dict:
    return {
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": "high",
        "function_name": "f",
        "line_start": 10,
        "line_end": 30,
        "details": {},
        "health_impact": impact,
        "reason": "seeded",
        "dimension": "defect",
    }


def _plan(path: str = _PATH) -> dict:
    return {
        "refactoring_type": "extract_method",
        "file_path": path,
        "target_symbol": "f",
        "line_start": 10,
        "line_end": 30,
        "plan": {"extracted_name": "_f_part", "local_scope": True},
        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }


def _result(**over) -> SimpleNamespace:
    """A pipeline result carrying only what the analysis phase reads.

    Deliberately without ``head_commit`` or ``commit_sha``: that is the shape
    the real init path hands over, and the reason the sha has to come from disk.
    """
    base = dict(
        dead_code_report=None,
        decision_report=None,
        health_report=SimpleNamespace(
            metrics=[_metric()],
            findings=[_finding()],
            refactoring_suggestions=[_plan()],
            performance_plan_policy=None,
            kpis={},
            coverage_files=None,
            function_blame_rows=None,
        ),
    )
    base.update(over)
    return SimpleNamespace(**base)


async def _metric_commits(session, repo_id: str) -> set[str | None]:
    rows = (
        (
            await session.execute(
                select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
            )
        )
        .scalars()
        .all()
    )
    assert rows, "the analysis phase must have written metric rows"
    return {row.analyzed_commit for row in rows}


async def _leads(session, repo_id: str) -> dict[str, str | None]:
    rows = (
        (
            await session.execute(
                select(RefactoringOpportunity).where(
                    RefactoringOpportunity.repository_id == repo_id
                )
            )
        )
        .scalars()
        .all()
    )
    return {row.file_path: row.lead_biomarker for row in rows}


class TestAnalyzedCommit:
    async def test_it_stamps_the_metric_rows_with_head_read_off_disk(
        self, async_session, monkeypatch
    ):
        """The defect this exists for: every row of a fresh index was NULL.

        The sha was taken off the pipeline result, which carries neither
        ``head_commit`` nor ``commit_sha``, so a full index stamped nothing
        while the update path stamped correctly.
        """
        import repowise.core.workspace.update as update_mod

        monkeypatch.setattr(update_mod, "get_head_commit", lambda _path: "d" * 40)
        repo = await insert_repo(async_session)
        await persist_analysis(_result(), async_session, repo.id)
        assert await _metric_commits(async_session, repo.id) == {"d" * 40}

    async def test_an_unreadable_head_leaves_the_column_null(
        self, async_session, monkeypatch
    ):
        """``None`` reads as "not recorded", which is honest; a guess is not."""
        import repowise.core.workspace.update as update_mod

        monkeypatch.setattr(update_mod, "get_head_commit", lambda _path: None)
        repo = await insert_repo(async_session)
        await persist_analysis(_result(), async_session, repo.id)
        assert await _metric_commits(async_session, repo.id) == {None}


class TestGovernanceReachesTheComposer:
    async def test_the_queue_leads_with_a_governance_cause(
        self, async_session, monkeypatch
    ):
        """The defect this exists for: governance lands after the composer.

        ``replace_governance_findings`` runs well after the writer that folds
        findings into the refactoring queue, so without a second composition a
        freshly indexed repository ranks every opportunity against a finding
        set holding none of its governance causes.
        """
        import repowise.core.pipeline.persist as persist_mod

        monkeypatch.setattr(
            persist_mod,
            "_analyzed_commit",
            _stub_commit("e" * 40),
        )
        _stub_governance(
            monkeypatch,
            [
                HealthFindingData(
                    biomarker_type="ungoverned_hotspot",
                    severity=Severity.MEDIUM,
                    file_path=_PATH,
                    function_name=None,
                    line_start=None,
                    line_end=None,
                    details={"is_hotspot": True},
                    health_impact=9.0,
                    reason="Churn hotspot with no governing architectural decision.",
                )
            ],
        )
        repo = await insert_repo(async_session)
        await persist_analysis(_result(), async_session, repo.id)
        assert (await _leads(async_session, repo.id))[_PATH] == "ungoverned_hotspot"

    async def test_a_run_with_no_governance_causes_keeps_its_structural_lead(
        self, async_session, monkeypatch
    ):
        """The recomposition must not disturb a repository that has none."""
        import repowise.core.pipeline.persist as persist_mod

        monkeypatch.setattr(persist_mod, "_analyzed_commit", _stub_commit("e" * 40))
        _stub_governance(monkeypatch, [])
        repo = await insert_repo(async_session)
        await persist_analysis(_result(), async_session, repo.id)
        assert (await _leads(async_session, repo.id))[_PATH] == "complex_method"


def _stub_commit(sha: str):
    async def _fake(_session, _repo_id):
        return sha

    return _fake


def _stub_governance(monkeypatch, findings: list) -> None:
    """Pin the governance pass's output without standing up a decision graph.

    ``persist_analysis`` imports it inside the function body, so the module
    attribute is what the call resolves to.
    """
    import repowise.core.analysis.health.governance as gov_mod

    monkeypatch.setattr(
        gov_mod, "build_governance_findings", lambda **_kwargs: findings
    )
