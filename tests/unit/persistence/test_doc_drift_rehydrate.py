"""Stored drift products retain the report type and support a guarded write-back."""

from dataclasses import replace

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.analysis.doc_drift.models import DocDriftReport
from repowise.core.analysis.doc_drift.serialize import serialize_finding, serialize_report
from repowise.core.persistence.crud import replace_doc_drift_guarded
from repowise.core.pipeline.resume.rehydrate import rehydrate_doc_drift_report
from tests.unit.persistence.helpers import insert_repo


async def test_stored_report_round_trips_without_inventing_scan_metadata(async_session):
    repo = await insert_repo(async_session)
    original = DocDriftAnalyzer(
        source_map={"README.md": b"Read `src/missing.py` and `src/live.py`.\n"},
        tracked_paths={"README.md", "src/live.py"},
    ).analyze()
    assert original.findings and original.resolved_references
    original.findings[0] = replace(
        original.findings[0], suggestion="src/live.py", suggestion_basis="fixture"
    )
    await replace_doc_drift_guarded(async_session, repo.id, original)
    await async_session.commit()

    stored = await rehydrate_doc_drift_report(async_session, repo.id)
    assert isinstance(stored, DocDriftReport)
    assert stored.repo_id == repo.id
    assert stored.total_findings == original.total_findings
    assert stored.confidence_summary == original.confidence_summary
    assert [serialize_finding(f) for f in stored.findings] == [
        serialize_finding(f) for f in original.findings
    ]
    assert stored.resolved_references == original.resolved_references
    assert stored.authoritative_paths is None
    assert stored.symbol_scope is None
    assert stored.documents == frozenset()
    assert stored.analyzed_at is None
    assert stored.documents_scanned is None
    assert stored.references_checked is None
    assert stored.verdict_summary == {}
    assert stored.anchor_renderer is None
    assert stored.suppressed is None
    assert stored.hidden_below_threshold is None
    assert serialize_report(stored)["documents_scanned"] is None

    adjusted = replace(stored)
    await replace_doc_drift_guarded(async_session, repo.id, adjusted)
    await async_session.commit()
    after = await rehydrate_doc_drift_report(async_session, repo.id)
    assert after == stored


async def test_empty_store_returns_an_empty_complete_report(async_session):
    repo = await insert_repo(async_session)
    stored = await rehydrate_doc_drift_report(async_session, repo.id)
    assert isinstance(stored, DocDriftReport)
    assert stored.total_findings == 0
    assert stored.findings == []
    assert stored.resolved_references == []
    assert stored.authoritative_paths is None
    assert stored.symbol_scope is None
    assert stored.documents_scanned is None
