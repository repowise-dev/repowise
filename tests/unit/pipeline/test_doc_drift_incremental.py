"""The drift pass on the incremental update path.

Scoped writes must preserve findings outside the rechecked documents and
produce the same stored result as a full pass.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.pipeline import PhaseTimings
from repowise.core.pipeline.incremental import DocDriftUpdate, run_doc_drift_partial


def _partial(*args: Any, **kwargs: Any) -> Any:
    """The pass with the index's symbol names given, as the stub has no graph."""
    return run_doc_drift_partial(
        *args, update=DocDriftUpdate(symbol_names=frozenset()), **kwargs
    )


class _Builder:
    """Stands in for the graph builder, carrying only what the pass reads."""

    def __init__(self, tracked: set[str] | None) -> None:
        if tracked is not None:
            self.traversed_file_paths = tracked


def _sources(doc: bytes) -> dict[str, bytes]:
    return {"docs/guide.md": doc, "src/real.py": b"x = 1\n"}


def test_a_document_naming_a_live_file_is_not_a_finding() -> None:
    report = _partial(
        _Builder({"docs/guide.md", "src/real.py"}),
        _sources(b"# Guide\n\nSee `src/real.py` for details.\n"),
    )
    assert report is not None
    assert report.total_findings == 0


def test_a_document_naming_a_missing_file_is_a_finding() -> None:
    report = _partial(
        _Builder({"docs/guide.md", "src/real.py"}),
        _sources(b"# Guide\n\nSee `src/gone.py` for details.\n"),
    )
    assert report is not None
    assert [f.target for f in report.findings] == ["src/gone.py"]


def test_the_write_is_scoped_to_the_documents_actually_read() -> None:
    """``authoritative_paths`` is the scanned set, never None.

    A repo-wide scope would delete the findings of a document this run could
    not open, and nothing would write them back: ``prune_deleted_file_rows``
    correctly judges the file live and leaves it alone.
    """
    report = _partial(
        _Builder({"docs/guide.md", "docs/unread.md", "src/real.py"}),
        _sources(b"# Guide\n\nSee `src/gone.py`.\n"),
    )
    assert report is not None
    assert report.authoritative_paths == frozenset({"docs/guide.md"})
    assert "docs/unread.md" not in report.authoritative_paths


def test_tracked_paths_come_from_the_traversal_not_the_source_map() -> None:
    """A file that failed to parse is still part of the tree.

    ``source_map`` omits it; resolving against that narrower set would report a
    live path as missing, which is a fabricated finding rather than drift.
    """
    report = _partial(
        # ``src/unparsed.py`` is tracked but absent from source_map.
        _Builder({"docs/guide.md", "src/real.py", "src/unparsed.py"}),
        _sources(b"# Guide\n\nSee `src/unparsed.py`.\n"),
    )
    assert report is not None
    assert report.total_findings == 0


def test_a_builder_without_a_traversal_skips_rather_than_falling_back() -> None:
    """No tracked set means write nothing, never "use the source_map keys".

    The fallback would narrow the tree and fabricate findings, and the report
    would look perfectly valid on the way to a scoped delete.
    """
    assert _partial(_Builder(None), _sources(b"See `src/gone.py`.\n")) is None
    assert _partial(_Builder(set()), _sources(b"See `src/gone.py`.\n")) is None


@pytest.mark.parametrize("empty", [None, {}])
def test_an_empty_source_map_returns_none_rather_than_an_empty_report(empty: Any) -> None:
    """``analyze()`` does not raise on an empty source_map.

    It returns a valid zero-finding report, so without this guard a run whose
    ingestion degraded would look like a clean pass. Returning ``None`` keeps
    "the pass did not run" distinguishable from "the pass found nothing".
    """
    assert _partial(_Builder({"docs/guide.md"}), empty) is None


def test_a_clean_run_still_returns_a_report_so_stale_rows_are_cleared() -> None:
    """Zero findings is a result, not a reason to skip the write."""
    report = _partial(
        _Builder({"docs/guide.md", "src/real.py"}),
        _sources(b"# Guide\n\nNothing to see.\n"),
    )
    assert report is not None
    assert report.findings == []
    assert report.authoritative_paths == frozenset({"docs/guide.md"})


def test_an_analyzer_failure_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pass is best-effort; it must never take an update down."""
    import repowise.core.analysis.doc_drift as dd

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("analyzer exploded")

    monkeypatch.setattr(dd, "DocDriftAnalyzer", _boom)
    messages: list[str] = []
    assert (
        _partial(
            _Builder({"docs/guide.md"}),
            _sources(b"See `src/gone.py`.\n"),
            log=messages.append,
        )
        is None
    )
    assert any("Doc drift" in m for m in messages)


def test_the_pass_records_its_own_timing_row() -> None:
    """Visible as ``analysis.doc_drift`` rather than absorbed into rebuild."""
    timings = PhaseTimings()
    _partial(
        _Builder({"docs/guide.md", "src/real.py"}),
        _sources(b"# Guide\n"),
        timings=timings,
    )
    assert "analysis.doc_drift" in timings.totals


def test_the_incremental_pass_agrees_with_the_full_one() -> None:
    """Same analyzer, same inputs, same findings.

    This is the whole argument for recomputing instead of caching: there is no
    second code path that can disagree with the first.
    """
    sources = _sources(b"# Guide\n\nSee `src/gone.py` and `src/real.py`.\n")
    tracked = {"docs/guide.md", "src/real.py"}

    full = DocDriftAnalyzer(source_map=sources, tracked_paths=tracked).analyze()
    partial = _partial(_Builder(tracked), sources)

    assert partial is not None
    assert [(f.file_path, f.target) for f in partial.findings] == [
        (f.file_path, f.target) for f in full.findings
    ]


def test_documents_is_the_set_behind_documents_scanned() -> None:
    report = DocDriftAnalyzer(
        source_map=_sources(b"# Guide\n"),
        tracked_paths={"docs/guide.md", "src/real.py"},
    ).analyze()
    assert report.documents == frozenset({"docs/guide.md"})
    # src/real.py is in source_map but is not a document.
    assert "src/real.py" not in report.documents


@pytest.fixture
async def drift_store(tmp_path):
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    (tmp_path / ".repowise").mkdir()
    engine = create_engine(f"sqlite+aiosqlite:///{tmp_path}/.repowise/wiki.db")
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await upsert_repository(session, name="sample", local_path=str(tmp_path))
            await session.commit()
            yield tmp_path, session, repo.id
    finally:
        await engine.dispose()


def _finding_keys(findings):
    import json

    from repowise.core.analysis.doc_drift.serialize import serialize_finding

    return sorted(json.dumps(serialize_finding(f), sort_keys=True) for f in findings)


_BEFORE = {
    "docs/api.md": b"See `src/api.py`.\n",
    "docs/missing.md": b"See `src/gone.py`.\n",
    "docs/anchor.md": b"Read [target](target.md#missing).\n",
    "docs/target.md": b"# Existing\n",
    "docs/commands.md": b"Run `npm run missing`.\n",
    "docs/quiet.md": b"See `src/stable.py`.\n",
    "docs/new-directory.md": b"See `future/absent.py`.\n",
    "docs/new-language.md": b"See `src/unknown.rb`.\n",
    "src/api.py": b"x = 1\n",
    "src/stable.py": b"x = 2\n",
    "package.json": b'{"scripts": {"test": "pytest"}}',
}


@pytest.mark.parametrize(
    ("path", "content", "expected_documents"),
    [
        ("src/api.py", None, {"docs/api.md"}),
        ("src/gone.py", b"x = 3\n", {"docs/missing.md"}),
        ("src/gone/__init__.py", b"x = 3\n", {"docs/missing.md"}),
        ("docs/target.md", b"# Missing\n", {"docs/target.md", "docs/anchor.md"}),
        ("docs/api.md", b"See `src/absent.py`.\n", {"docs/api.md"}),
        ("package.json", b'{"scripts": {"missing": "pytest"}}', None),
        ("future/real.py", b"x = 3\n", None),
        ("src/real.rb", b"puts 'hello'\n", None),
        ("mkdocs.yml", b"site_name: Example\n", None),
    ],
)
async def test_scoped_update_has_the_same_stored_result_as_a_full_run(
    drift_store, path, content, expected_documents,
):
    from repowise.core.persistence.crud import replace_doc_drift_guarded
    from repowise.core.pipeline.incremental.doc_drift import load_doc_drift_documents
    from repowise.core.pipeline.resume.rehydrate import rehydrate_doc_drift_report

    root, session, repo_id = drift_store
    before = DocDriftAnalyzer(source_map=_BEFORE).analyze()
    await replace_doc_drift_guarded(session, repo_id, before)
    await session.commit()
    sources = dict(_BEFORE)
    status = "added" if path not in sources else "modified"
    if content is None:
        del sources[path]
        status = "deleted"
    else:
        sources[path] = content
    update = DocDriftUpdate.from_file_diffs(
        "base", [SimpleNamespace(path=path, old_path=None, status=status)]
    )
    documents = await load_doc_drift_documents(root, update, sources, set(sources))
    assert documents == (frozenset(expected_documents) if expected_documents is not None else None)
    partial = run_doc_drift_partial(
        _Builder(set(sources)), sources, document_paths=documents,
    )
    assert partial.authoritative_paths == partial.documents
    await replace_doc_drift_guarded(session, repo_id, partial)
    await session.commit()

    stored = await rehydrate_doc_drift_report(session, repo_id)
    full = DocDriftAnalyzer(source_map=sources).analyze()
    assert _finding_keys(stored.findings) == _finding_keys(full.findings)
    assert stored.total_findings == full.total_findings
    assert stored.confidence_summary == full.confidence_summary
    assert set(stored.resolved_references) == set(full.resolved_references)
    assert stored.documents_scanned is None and stored.references_checked is None


async def test_tree_changes_recheck_references_without_stored_rows(drift_store):
    from repowise.core.persistence.crud import replace_doc_drift_guarded
    from repowise.core.pipeline.incremental.doc_drift import load_doc_drift_documents

    root, session, repo_id = drift_store
    sources = {**_BEFORE, "other/gone.py": b"x = 3", "other/keep.py": b"x = 4"}
    before = DocDriftAnalyzer(source_map=sources).analyze()
    # The only match is a basename in another directory: ambiguous, not stored.
    assert not any(f.file_path == "docs/missing.md" for f in before.findings)
    assert not any(r.doc_path == "docs/missing.md" for r in before.resolved_references)
    await replace_doc_drift_guarded(session, repo_id, before)
    await session.commit()
    del sources["other/gone.py"]
    update = DocDriftUpdate.from_file_diffs(
        "base", [SimpleNamespace(path="other/gone.py", old_path=None, status="deleted")]
    )
    documents = await load_doc_drift_documents(root, update, sources, set(sources))
    assert documents == frozenset({"docs/missing.md"})
    partial = run_doc_drift_partial(_Builder(set(sources)), sources, document_paths=documents)
    assert [f.target for f in partial.findings] == ["src/gone.py"]


async def test_removing_a_suggested_replacement_rechecks_its_document(drift_store):
    from repowise.core.persistence.crud import replace_doc_drift_guarded
    from repowise.core.pipeline.incremental.doc_drift import load_doc_drift_documents
    from repowise.core.pipeline.resume.rehydrate import rehydrate_doc_drift_report

    root, session, repo_id = drift_store
    sources = {**_BEFORE, "src/moved.py": b"x = 3\n"}
    def renames(_paths):
        return {"src/gone.py": "src/moved.py"}

    before = DocDriftAnalyzer(source_map=sources, rename_lookup=renames).analyze()
    finding = next(f for f in before.findings if f.file_path == "docs/missing.md")
    assert finding.suggestion == "src/moved.py"
    await replace_doc_drift_guarded(session, repo_id, before)
    await session.commit()
    del sources["src/moved.py"]
    update = DocDriftUpdate.from_file_diffs(
        "base", [SimpleNamespace(path="src/moved.py", old_path=None, status="deleted")]
    )
    documents = await load_doc_drift_documents(root, update, sources, set(sources))
    assert documents == frozenset({"docs/missing.md"})
    partial = run_doc_drift_partial(_Builder(set(sources)), sources, document_paths=documents)
    await replace_doc_drift_guarded(session, repo_id, partial)
    stored = await rehydrate_doc_drift_report(session, repo_id)
    full = DocDriftAnalyzer(source_map=sources, rename_lookup=renames).analyze()
    assert _finding_keys(stored.findings) == _finding_keys(full.findings)


def test_a_scoped_document_keeps_unchanged_anchor_targets():
    sources = {
        "docs/changed.md": b"Read [target](target.md#missing).\n",
        "docs/target.md": b"# Existing\n",
    }
    report = _partial(
        _Builder(set(sources)), sources, document_paths=frozenset({"docs/changed.md"}),
    )
    assert report.documents == frozenset({"docs/changed.md"})
    assert [f.target for f in report.findings] == ["target.md#missing"]


async def test_unavailable_stored_dependencies_fall_back_to_full_pass(tmp_path):
    from repowise.core.pipeline.incremental.doc_drift import load_doc_drift_documents

    messages = []
    assert await load_doc_drift_documents(
        tmp_path, DocDriftUpdate(changed_paths=("src/api.py",)), _BEFORE, set(_BEFORE),
        log=messages.append,
    ) is None
    assert any("rechecking all documents" in message for message in messages)
