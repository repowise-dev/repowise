"""Persistence for the resolved-reference store, the drift reverse index.

The claim this table makes is weaker than a finding's and easier to corrupt: a
row says a document names a file that exists. These tests pin the two ways it
could start lying --- a scoped rewrite deleting rows it did not recompute, and
an empty store being indistinguishable from a clean one.
"""

from __future__ import annotations

from types import SimpleNamespace

from sqlalchemy import select

from repowise.core.analysis.doc_drift.models import (
    DocDriftFindingData,
    DriftKind,
    ResolvedDocReference,
)
from repowise.core.persistence.crud import (
    doc_drift_references_stored,
    get_doc_drift_references,
    replace_doc_drift_guarded,
    replace_doc_drift_references,
    serialize_doc_drift_reference_row,
)
from repowise.core.persistence.models import DocDriftReference
from tests.unit.persistence.helpers import insert_repo


def _ref(
    doc_path: str = "docs/a.md",
    target_path: str = "src/auth.py",
    line: int = 10,
    kind: DriftKind = DriftKind.PATH,
    section: str = "Authentication > Tokens",
) -> ResolvedDocReference:
    return ResolvedDocReference(
        doc_path=doc_path,
        target_path=target_path,
        kind=kind,
        line=line,
        section=section,
    )


async def _rows(session, repo_id: str) -> list[DocDriftReference]:
    stmt = select(DocDriftReference).where(DocDriftReference.repository_id == repo_id)
    return list((await session.execute(stmt)).scalars().all())


async def test_references_round_trip(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(async_session, repo.id, [_ref()])
    await async_session.commit()

    (row,) = await get_doc_drift_references(async_session, repo.id)
    assert row.document_path == "docs/a.md"
    assert row.target_path == "src/auth.py"
    assert row.kind == "path"
    assert row.line_number == 10
    assert row.section == "Authentication > Tokens"


async def test_rerunning_the_pass_changes_no_rows(async_session):
    """Idempotency, the same exit criterion the findings table has."""
    repo = await insert_repo(async_session)
    refs = [_ref(), _ref(doc_path="docs/b.md", line=4)]
    await replace_doc_drift_references(async_session, repo.id, refs)
    await async_session.commit()
    first = {(r.document_path, r.target_path, r.line_number) for r in await _rows(async_session, repo.id)}

    await replace_doc_drift_references(async_session, repo.id, refs)
    await async_session.commit()
    second = await _rows(async_session, repo.id)

    assert len(second) == 2
    assert {(r.document_path, r.target_path, r.line_number) for r in second} == first


async def test_two_references_at_one_site_do_not_collide(async_session):
    """A markdown link with a fragment yields a link row and an anchor row."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session,
        repo.id,
        [
            _ref(kind=DriftKind.LINK, target_path="docs/guide.md"),
            _ref(kind=DriftKind.ANCHOR, target_path="docs/guide.md"),
        ],
    )
    await async_session.commit()

    assert len(await _rows(async_session, repo.id)) == 2


async def test_duplicate_references_in_one_batch_collapse(async_session):
    """A document naming the same file twice on one line is ordinary prose."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(async_session, repo.id, [_ref(), _ref()])
    await async_session.commit()

    assert len(await _rows(async_session, repo.id)) == 1


async def test_scoped_write_keeps_rows_for_documents_it_did_not_read(async_session):
    """The hazard: a partial run must not delete what it never recomputed."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session,
        repo.id,
        [_ref(doc_path="docs/a.md"), _ref(doc_path="docs/b.md")],
    )
    await async_session.commit()

    # A run that only re-read docs/a.md, and found it now names nothing.
    await replace_doc_drift_references(
        async_session, repo.id, [], scope=frozenset({"docs/a.md"})
    )
    await async_session.commit()

    remaining = await _rows(async_session, repo.id)
    assert [r.document_path for r in remaining] == ["docs/b.md"]


async def test_scoped_write_refuses_a_row_outside_its_scope(async_session):
    """Inserted but never deleted by the next scoped run, it would outlive its evidence."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session,
        repo.id,
        [_ref(doc_path="docs/a.md"), _ref(doc_path="docs/elsewhere.md")],
        scope=frozenset({"docs/a.md"}),
    )
    await async_session.commit()

    assert [r.document_path for r in await _rows(async_session, repo.id)] == ["docs/a.md"]


async def test_unscoped_write_replaces_the_repository(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session, repo.id, [_ref(doc_path="docs/a.md")]
    )
    await async_session.commit()

    await replace_doc_drift_references(
        async_session, repo.id, [_ref(doc_path="docs/b.md")]
    )
    await async_session.commit()

    assert [r.document_path for r in await _rows(async_session, repo.id)] == ["docs/b.md"]


async def test_reader_narrows_by_target(async_session):
    """The question this table exists for, asked about a code file."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session,
        repo.id,
        [
            _ref(doc_path="docs/a.md", target_path="src/auth.py"),
            _ref(doc_path="docs/b.md", target_path="src/auth.py", line=2),
            _ref(doc_path="docs/c.md", target_path="src/other.py"),
        ],
    )
    await async_session.commit()

    rows = await get_doc_drift_references(
        async_session, repo.id, target_paths=["src/auth.py"]
    )
    assert [r.document_path for r in rows] == ["docs/a.md", "docs/b.md"]


async def test_the_reader_answers_about_several_targets_at_once(async_session):
    """One query for a batch of targets, because they share a session."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(
        async_session,
        repo.id,
        [
            _ref(doc_path="docs/a.md", target_path="src/auth.py"),
            _ref(doc_path="docs/b.md", target_path="src/db.py"),
            _ref(doc_path="docs/c.md", target_path="src/other.py"),
        ],
    )
    await async_session.commit()

    rows = await get_doc_drift_references(
        async_session, repo.id, target_paths=["src/auth.py", "src/db.py"]
    )
    assert {r.target_path for r in rows} == {"src/auth.py", "src/db.py"}


async def test_asking_about_no_targets_answers_about_none(async_session):
    """An empty batch must not fall through to "every row in the repository"."""
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(async_session, repo.id, [_ref()])
    await async_session.commit()

    assert await get_doc_drift_references(async_session, repo.id, target_paths=[]) == []


async def test_stored_probe_separates_never_computed_from_genuinely_none(async_session):
    """An empty table is not a clean tree, and the probe is what tells them apart."""
    repo = await insert_repo(async_session)
    assert await doc_drift_references_stored(async_session, repo.id) is False

    await replace_doc_drift_references(async_session, repo.id, [_ref()])
    await async_session.commit()

    assert await doc_drift_references_stored(async_session, repo.id) is True
    # A file nothing names, in a repository that was analysed: a real zero.
    assert (
        await get_doc_drift_references(async_session, repo.id, target_paths=["src/x.py"])
        == []
    )


async def test_the_probe_is_scoped_to_its_own_repository(async_session):
    repo = await insert_repo(async_session)
    other = await insert_repo(
        async_session,
        name="other",
        local_path="/tmp/other-repo",
        url="https://github.com/example/other-repo",
    )
    await replace_doc_drift_references(async_session, other.id, [_ref()])
    await async_session.commit()

    assert await doc_drift_references_stored(async_session, other.id) is True
    assert await doc_drift_references_stored(async_session, repo.id) is False


async def test_one_writer_keeps_findings_and_references_on_one_run(async_session):
    """Written apart they could describe different runs; the reverse view would lie."""
    repo = await insert_repo(async_session)

    report = SimpleNamespace(
        findings=[
            DocDriftFindingData(
                kind=DriftKind.PATH,
                file_path="docs/a.md",
                line_number=3,
                target="src/gone.py",
                confidence=0.9,
                reason="gone",
                origin="path_no_candidate",
                evidence=[],
            )
        ],
        resolved_references=[_ref(doc_path="docs/a.md")],
        authoritative_paths=None,
    )

    written = await replace_doc_drift_guarded(async_session, repo.id, report)
    await async_session.commit()

    assert written == 1
    assert len(await _rows(async_session, repo.id)) == 1


async def test_serializer_omits_the_target_every_row_shares(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(async_session, repo.id, [_ref()])
    await async_session.commit()

    (row,) = await get_doc_drift_references(async_session, repo.id)
    out = serialize_doc_drift_reference_row(row)
    assert out == {
        "document": "docs/a.md",
        "line": 10,
        "kind": "path",
        "section": "Authentication > Tokens",
    }


async def test_serializer_drops_an_empty_section(async_session):
    repo = await insert_repo(async_session)
    await replace_doc_drift_references(async_session, repo.id, [_ref(section="")])
    await async_session.commit()

    (row,) = await get_doc_drift_references(async_session, repo.id)
    assert "section" not in serialize_doc_drift_reference_row(row)
