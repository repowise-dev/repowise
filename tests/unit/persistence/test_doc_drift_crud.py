"""Persistence for documentation drift findings.

The exit criterion for this phase is that re-running the pass changes no rows,
so idempotency is the thing these tests exist to pin.
"""

from __future__ import annotations

from sqlalchemy import select

from repowise.core.analysis.doc_drift.models import DocDriftFindingData, DriftKind, SymbolScope
from repowise.core.persistence.crud import (
    doc_drift_last_written,
    get_doc_drift_findings,
    is_new_doc_drift_row,
    replace_doc_drift_findings,
    save_health_snapshot,
)
from repowise.core.persistence.crud.analysis.doc_drift import summarize_confidence_rows
from repowise.core.persistence.models import DocDriftFinding
from tests.unit.persistence.helpers import insert_repo


def _finding(
    file_path: str = "docs/a.md",
    line: int = 10,
    target: str = "src/gone.py",
    kind: DriftKind = DriftKind.PATH,
    confidence: float = 0.9,
) -> DocDriftFindingData:
    return DocDriftFindingData(
        kind=kind,
        file_path=file_path,
        line_number=line,
        target=target,
        confidence=confidence,
        reason="Document names src/gone.py, which no longer exists.",
        origin="path_no_candidate",
        evidence=[f"{file_path}:{line} states `{target}`"],
        raw=target,
        context=f"See `{target}`.",
    )


async def _rows(session, repo_id: str) -> list[DocDriftFinding]:
    stmt = select(DocDriftFinding).where(DocDriftFinding.repository_id == repo_id)
    return list((await session.execute(stmt)).scalars().all())


async def test_findings_round_trip(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding()])
    await async_session.commit()

    rows = await get_doc_drift_findings(async_session, repo.id)
    assert len(rows) == 1
    assert rows[0].file_path == "docs/a.md"
    assert rows[0].kind == "path"
    assert rows[0].line_number == 10
    assert rows[0].origin == "path_no_candidate"
    assert rows[0].evidence_json.startswith("[")


async def test_a_suggestion_persists_and_its_absence_is_null(async_session):
    repo = await insert_repo(async_session)
    suggested = _finding(line=1)
    suggested.suggestion = "src/gone/"
    suggested.suggestion_basis = "package_split"
    await replace_doc_drift_findings(
        async_session, repo.id, [suggested, _finding(line=2)]
    )
    await async_session.commit()

    rows = {r.line_number: r for r in await _rows(async_session, repo.id)}
    assert (rows[1].suggestion, rows[1].suggestion_basis) == ("src/gone/", "package_split")
    assert (rows[2].suggestion, rows[2].suggestion_basis) == (None, None)


async def test_rerunning_the_same_pass_changes_no_rows(async_session):
    """The phase exit criterion."""
    repo = await insert_repo(async_session)
    findings = [_finding(), _finding(line=20, target="src/other.py")]

    await replace_doc_drift_findings(async_session, repo.id, findings)
    await async_session.commit()
    first = {(r.file_path, r.kind, r.line_number, r.target) for r in await _rows(async_session, repo.id)}

    await replace_doc_drift_findings(async_session, repo.id, findings)
    await async_session.commit()
    rows = await _rows(async_session, repo.id)

    assert len(rows) == 2
    assert {(r.file_path, r.kind, r.line_number, r.target) for r in rows} == first


async def test_a_fixed_document_loses_its_rows(async_session):
    """A run that fixed the last drifted reference must clear the rows."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding()])
    await async_session.commit()

    await replace_doc_drift_findings(async_session, repo.id, [])
    await async_session.commit()
    assert await _rows(async_session, repo.id) == []


async def test_two_references_on_one_line_are_separate_rows(async_session):
    """A markdown link with a fragment yields both a link row and an anchor
    row at the same line. A key without ``target`` would collapse them."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [
            _finding(kind=DriftKind.LINK, target="docs/gone.md"),
            _finding(kind=DriftKind.ANCHOR, target="docs/gone.md#frag"),
        ],
    )
    await async_session.commit()
    assert len(await _rows(async_session, repo.id)) == 2


async def test_duplicate_findings_are_deduplicated_before_insert(async_session):
    """Two identical findings must not abort the batch after the DELETE has
    already run --- the hazard ``security_scan.replace_findings`` guards."""
    repo = await insert_repo(async_session)
    inserted = await replace_doc_drift_findings(
        async_session, repo.id, [_finding(), _finding()]
    )
    await async_session.commit()
    assert inserted == 1
    assert len(await _rows(async_session, repo.id)) == 1


async def test_scoped_replace_leaves_other_documents_alone(async_session):
    """An incremental re-check that read four documents must not delete
    findings for the three hundred it never looked at."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(file_path="docs/a.md"), _finding(file_path="docs/b.md")],
    )
    await async_session.commit()

    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(file_path="docs/a.md", line=99)],
        scope={"docs/a.md"},
    )
    await async_session.commit()

    rows = await _rows(async_session, repo.id)
    by_doc = {(r.file_path, r.line_number) for r in rows}
    assert ("docs/b.md", 10) in by_doc
    assert ("docs/a.md", 99) in by_doc
    assert ("docs/a.md", 10) not in by_doc


async def test_scoped_replace_drops_findings_outside_its_scope(async_session):
    """A row outside the scope would never be deleted by the next scoped run,
    so it would outlive its own evidence."""
    repo = await insert_repo(async_session)
    inserted = await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(file_path="docs/a.md"), _finding(file_path="docs/elsewhere.md")],
        scope={"docs/a.md"},
    )
    await async_session.commit()
    assert inserted == 1
    assert {r.file_path for r in await _rows(async_session, repo.id)} == {"docs/a.md"}


async def test_an_unreadable_document_keeps_the_findings_it_earned(async_session):
    """The incremental scope is the documents the pass actually read.

    A document that exists but missed ``source_map`` this run (transient read
    failure, or markdown over MAX_DOC_BYTES) is absent from the scope, so its
    rows survive. A repo-wide write would delete them and nothing would put
    them back: ``prune_deleted_file_rows`` correctly judges the file live.
    """
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(file_path="docs/read.md"), _finding(file_path="docs/unreadable.md")],
    )
    await async_session.commit()

    # Second run could not open docs/unreadable.md, so it is not in scope.
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(file_path="docs/read.md")],
        scope={"docs/read.md"},
    )
    await async_session.commit()

    assert {r.file_path for r in await _rows(async_session, repo.id)} == {
        "docs/read.md",
        "docs/unreadable.md",
    }


async def test_min_confidence_filter_on_read(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding(confidence=0.95), _finding(line=11, confidence=0.5)],
    )
    await async_session.commit()

    assert len(await get_doc_drift_findings(async_session, repo.id, min_confidence=0.9)) == 1
    assert len(await get_doc_drift_findings(async_session, repo.id)) == 2


async def test_read_side_buckets_match_the_analyzer(async_session):
    """The CRUD layer re-derives the buckets on read; a drift between the two
    is what ``test_confidence_parity`` in dead code exists to prevent."""
    from repowise.core.analysis.doc_drift import summarize_confidence

    repo = await insert_repo(async_session)
    findings = [
        _finding(line=1, confidence=0.95),
        _finding(line=2, confidence=0.9),
        _finding(line=3, confidence=0.5),
    ]
    await replace_doc_drift_findings(async_session, repo.id, findings)
    await async_session.commit()

    rows = await get_doc_drift_findings(async_session, repo.id)
    assert summarize_confidence_rows(rows) == summarize_confidence(findings)


async def test_one_serializer_serves_both_surfaces(async_session):
    """The CLI and ``get_health`` read the same rows, so they serialize them
    through one function. Two hand-maintained dicts is how they would come to
    disagree, which is what ``test_confidence_parity`` exists to remember."""
    from repowise.core.persistence.crud import serialize_doc_drift_row

    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding(line=1)])
    await async_session.commit()

    (row,) = await get_doc_drift_findings(async_session, repo.id)
    full = serialize_doc_drift_row(row)
    budgeted = serialize_doc_drift_row(row, evidence=False)

    # The document is what a reader edits, so it is what the finding names.
    assert full["file_path"] == row.file_path
    # ``evidence`` is the only difference; everything else must match, or the
    # terminal and the agent are describing the same row differently.
    assert set(full) - set(budgeted) == {"evidence"}
    assert all(budgeted[k] == full[k] for k in budgeted)


async def test_unreadable_evidence_degrades_to_none_rather_than_raising():
    """An index written by an older version is still worth reading."""
    import types

    from repowise.core.persistence.crud import serialize_doc_drift_row

    def _row(blob):
        return types.SimpleNamespace(
            file_path="docs/a.md", line_number=1, kind="path", target="src/gone.py",
            confidence=0.9, origin="path_no_candidate", reason="r", raw="x", context="c",
            evidence_json=blob, suggestion=None, suggestion_basis=None,
        )

    for blob in ("{not json", '{"a": 1}', "", None):
        assert serialize_doc_drift_row(_row(blob))["evidence"] == []


async def test_the_first_check_marks_nothing_new(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding()])
    await async_session.commit()

    (row,) = await _rows(async_session, repo.id)
    assert row.first_seen_at is None
    last = await doc_drift_last_written(async_session, repo.id)
    assert not is_new_doc_drift_row(row, last)


async def test_a_finding_keeps_its_age_when_its_line_moves(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding(target="src/x.py")])
    await replace_doc_drift_findings(
        async_session, repo.id, [_finding(target="src/x.py"), _finding(line=20)]
    )
    (first,) = [r for r in await _rows(async_session, repo.id) if r.line_number == 20]
    seen = first.first_seen_at
    assert seen is not None

    # The same finding, pushed down by an edit above it: same age, no longer new.
    await replace_doc_drift_findings(
        async_session, repo.id, [_finding(target="src/x.py"), _finding(line=25)]
    )
    await async_session.commit()
    rows = {r.line_number: r for r in await _rows(async_session, repo.id)}
    assert rows[25].first_seen_at == seen
    last = await doc_drift_last_written(async_session, repo.id)
    assert not is_new_doc_drift_row(rows[25], last)
    assert rows[10].first_seen_at is None


async def test_only_the_latest_writes_arrivals_read_as_new(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(async_session, repo.id, [_finding(target="src/a.py")])
    await replace_doc_drift_findings(
        async_session, repo.id, [_finding(target="src/a.py"), _finding(target="src/b.py")]
    )
    await async_session.commit()

    last = await doc_drift_last_written(async_session, repo.id)
    new = {r.target: is_new_doc_drift_row(r, last) for r in await _rows(async_session, repo.id)}
    assert new == {"src/a.py": False, "src/b.py": True}


async def test_a_snapshot_records_the_stored_drift_count(async_session):
    repo = await insert_repo(async_session)
    kwargs = dict(
        hotspot_health=8.0,
        average_health=8.0,
        worst_performer_path=None,
        worst_performer_score=None,
    )
    never = await save_health_snapshot(async_session, repo.id, **kwargs)
    assert never.doc_drift_count is None

    await replace_doc_drift_findings(async_session, repo.id, [_finding(), _finding(line=2)])
    counted = await save_health_snapshot(async_session, repo.id, **kwargs)
    assert counted.doc_drift_count == 2


async def test_symbol_findings_of_untouched_documents_carry_forward(async_session):
    """An update re-resolves symbols in some documents; the rest keep theirs."""
    repo = await insert_repo(async_session)
    kept = _finding("docs/a.md", 3, "old_helper", DriftKind.SYMBOL)
    fixed = _finding("docs/b.md", 4, "gone_helper", DriftKind.SYMBOL)
    await replace_doc_drift_findings(
        async_session, repo.id, [kept, fixed, _finding("docs/a.md", 9)]
    )
    await async_session.commit()

    # Both documents re-read (cheap kinds re-derived); symbols only in b.md.
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding("docs/a.md", 9)],
        scope={"docs/a.md", "docs/b.md"},
        symbol_scope=SymbolScope(documents=frozenset({"docs/b.md"})),
    )
    await async_session.commit()
    rows = {(r.file_path, r.kind, r.target) for r in await _rows(async_session, repo.id)}
    assert rows == {("docs/a.md", "symbol", "old_helper"), ("docs/a.md", "path", "src/gone.py")}


async def test_a_rechecked_reference_is_replaced_and_its_neighbours_kept(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [
            _finding("docs/a.md", 3, "old_helper", DriftKind.SYMBOL),
            _finding("docs/a.md", 5, "other_helper", DriftKind.SYMBOL),
        ],
    )
    await async_session.commit()

    # Only ``old_helper`` was re-resolved, and it now resolves.
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [],
        scope={"docs/a.md"},
        symbol_scope=SymbolScope(references=frozenset({("docs/a.md", "old_helper")})),
    )
    await async_session.commit()
    rows = {r.target for r in await _rows(async_session, repo.id)}
    assert rows == {"other_helper"}


async def test_a_symbol_finding_outside_the_symbol_scope_is_not_inserted(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_findings(
        async_session,
        repo.id,
        [_finding("docs/a.md", 3, "old_helper", DriftKind.SYMBOL)],
        symbol_scope=SymbolScope(),
    )
    await async_session.commit()
    assert await _rows(async_session, repo.id) == []
