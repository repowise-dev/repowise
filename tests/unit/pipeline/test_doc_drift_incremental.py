"""The drift pass on the incremental update path.

Phase 3 measured a persisted reverse index and refused it: the pass is 0.19s
against a 125s floor for the cheapest real update, and its input is already in
``source_map``. These pin the recompute design instead, and specifically the
three ways it can be silently wrong.
"""

from __future__ import annotations

from typing import Any

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer
from repowise.core.pipeline import PhaseTimings
from repowise.core.pipeline.incremental import run_doc_drift_partial


class _Builder:
    """Stands in for the graph builder, carrying only what the pass reads."""

    def __init__(self, tracked: set[str] | None) -> None:
        if tracked is not None:
            self.traversed_file_paths = tracked


def _sources(doc: bytes) -> dict[str, bytes]:
    return {"docs/guide.md": doc, "src/real.py": b"x = 1\n"}


def test_a_document_naming_a_live_file_is_not_a_finding() -> None:
    report = run_doc_drift_partial(
        _Builder({"docs/guide.md", "src/real.py"}),
        _sources(b"# Guide\n\nSee `src/real.py` for details.\n"),
    )
    assert report is not None
    assert report.total_findings == 0


def test_a_document_naming_a_missing_file_is_a_finding() -> None:
    report = run_doc_drift_partial(
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
    report = run_doc_drift_partial(
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
    report = run_doc_drift_partial(
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
    assert run_doc_drift_partial(_Builder(None), _sources(b"See `src/gone.py`.\n")) is None
    assert run_doc_drift_partial(_Builder(set()), _sources(b"See `src/gone.py`.\n")) is None


@pytest.mark.parametrize("empty", [None, {}])
def test_an_empty_source_map_returns_none_rather_than_an_empty_report(empty: Any) -> None:
    """``analyze()`` does not raise on an empty source_map.

    It returns a valid zero-finding report, so without this guard a run whose
    ingestion degraded would look like a clean pass. Returning ``None`` keeps
    "the pass did not run" distinguishable from "the pass found nothing".
    """
    assert run_doc_drift_partial(_Builder({"docs/guide.md"}), empty) is None


def test_a_clean_run_still_returns_a_report_so_stale_rows_are_cleared() -> None:
    """Zero findings is a result, not a reason to skip the write."""
    report = run_doc_drift_partial(
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
        run_doc_drift_partial(
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
    run_doc_drift_partial(
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
    partial = run_doc_drift_partial(_Builder(tracked), sources)

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
