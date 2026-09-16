"""The drift pass end to end: report shape, cutoff, and the known defects."""

from __future__ import annotations

import pytest

from repowise.core.analysis.doc_drift import DocDriftAnalyzer, summarize_confidence
from repowise.core.analysis.doc_drift.constants import (
    DEFAULT_MIN_CONFIDENCE,
    DRIFT_ORIGIN_VALUES,
    HIGH_CONFIDENCE_THRESHOLD,
    ORIGIN_CONFIDENCE,
    REVIEW_CONFIDENCE_THRESHOLD,
)
from repowise.core.analysis.doc_drift.models import DriftKind, DriftVerdict


def _analyze(files: dict[str, str], config: dict | None = None):
    source_map = {k: v.encode("utf-8") for k, v in files.items()}
    return DocDriftAnalyzer("repo", source_map=source_map).analyze(config)


# ---------------------------------------------------------------------------
# The fourteen known defects, as fixtures
# ---------------------------------------------------------------------------


def test_module_became_a_package():
    """The single most common drift shape Phase 1 found.

    Four of the eleven real path findings are this: a module became a package
    and the prose still names the ``.py``.
    """
    report = _analyze(
        {
            "docs/a.md": "See `packages/core/ingestion/git_indexer.py` for this.\n",
            "packages/core/ingestion/git_indexer/__init__.py": "",
            "packages/core/ingestion/git_indexer/walker.py": "",
        }
    )
    assert report.total_findings == 1
    assert report.findings[0].kind is DriftKind.PATH
    assert report.findings[0].line_number == 1


def test_deleted_file_is_flagged():
    report = _analyze(
        {"docs/a.md": "See `src/persistence/vector.py`.\n", "src/persistence/store.py": ""}
    )
    assert report.total_findings == 1


def test_renamed_heading_leaves_the_inbound_link_behind():
    """All three real anchor findings are this shape."""
    report = _analyze(
        {
            "docs/CONTRIBUTING.md": "See [quickstart](../README.md#quickstart-under-5-minutes).\n",
            "README.md": "## Start in minutes (no API key)\n",
        }
    )
    anchors = [f for f in report.findings if f.kind is DriftKind.ANCHOR]
    assert len(anchors) == 1
    assert anchors[0].origin == "anchor_no_heading"


def test_a_correct_document_produces_nothing():
    report = _analyze(
        {
            "docs/a.md": "See `src/app.py` and [readme](../README.md#setup).\n",
            "README.md": "## Setup\n",
            "src/app.py": "",
        }
    )
    assert report.total_findings == 0
    assert report.verdict_summary[DriftVerdict.MISSING.value] == 0


# ---------------------------------------------------------------------------
# Report shape
# ---------------------------------------------------------------------------


def test_uncheckable_is_counted_not_hidden():
    """684 of 903 path references on this repo are uncheckable.

    Saying so is the difference between a detector and a noise generator.
    """
    report = _analyze({"docs/a.md": "Edit `CLAUDE.md` and `config.yaml`.\n", "src/x.py": ""})
    assert report.total_findings == 0
    assert report.verdict_summary[DriftVerdict.UNCHECKABLE.value] == 2
    assert report.references_checked == 2


def test_verdict_summary_covers_every_reference():
    report = _analyze(
        {
            "docs/a.md": "`src/app.py` `CLAUDE.md` `src/gone.py`\n",
            "src/app.py": "",
        }
    )
    assert sum(report.verdict_summary.values()) == report.references_checked


def test_historical_documents_are_not_scanned():
    report = _analyze({"CHANGELOG.md": "Removed `src/gone.py` in 1.0.\n", "src/a.py": ""})
    assert report.documents_scanned == 0
    assert report.total_findings == 0


def test_min_confidence_cutoff_and_hidden_counter():
    files = {"docs/tutorial.md": "Create `src/specs/mylang.py`.\n", "src/specs/real.py": ""}
    shown = _analyze(files)
    assert shown.total_findings == 1
    assert shown.hidden_below_threshold == 0

    hidden = _analyze(files, {"min_confidence": 0.9})
    assert hidden.total_findings == 0
    assert hidden.hidden_below_threshold == 1


def test_classes_can_be_disabled_independently():
    files = {
        "docs/a.md": "`src/gone.py` and [x](../README.md#nope)\n",
        "README.md": "## Yes\n",
        # A real file under src/, so the path reference is anchored and
        # therefore checkable. Without it the path is uncheckable and this
        # test would pass for the wrong reason.
        "src/real.py": "",
    }
    assert _analyze(files, {"check_path": False}).total_findings == 1
    assert _analyze(files, {"check_anchor": False}).total_findings == 1
    assert _analyze(files, {"check_path": False, "check_anchor": False}).total_findings == 0


def test_findings_are_ordered_most_confident_first():
    report = _analyze(
        {
            "docs/guide.md": "`src/one.py`\n",
            "docs/architecture.md": "`src/two.py`\n",
            "src/real.py": "",
        }
    )
    confidences = [f.confidence for f in report.findings]
    assert confidences == sorted(confidences, reverse=True)


def test_finding_carries_evidence_and_a_reason():
    report = _analyze({"docs/a.md": "See `src/gone.py`.\n", "src/real.py": ""})
    finding = report.findings[0]
    assert finding.reason
    assert any("docs/a.md:1" in e for e in finding.evidence)
    assert finding.raw == "src/gone.py"


def test_oversized_documents_are_skipped():
    """Reproduces the 500KB ``max_file_size_kb`` ceiling."""
    big = "See `src/gone.py`.\n" + ("x" * (600 * 1024))
    report = _analyze({"docs/big.md": big, "src/real.py": ""})
    assert report.documents_scanned == 0


def test_rerunning_changes_nothing():
    """Exit criterion: re-running changes no rows."""
    files = {"docs/a.md": "See `src/gone.py`.\n", "src/real.py": ""}
    first, second = _analyze(files), _analyze(files)
    key = lambda r: [  # noqa: E731
        (f.file_path, f.kind, f.line_number, f.target, f.confidence) for f in r.findings
    ]
    assert key(first) == key(second)


def test_empty_repository_produces_an_empty_report():
    report = _analyze({})
    assert report.total_findings == 0
    assert report.documents_scanned == 0
    assert report.confidence_summary == {"high": 0, "medium": 0, "low": 0}


# ---------------------------------------------------------------------------
# Confidence vocabulary
# ---------------------------------------------------------------------------


def test_every_origin_has_a_confidence():
    """The Literal vocabulary and the confidence table are two views of one
    thing, so neither may carry a name the other lacks."""
    assert frozenset(ORIGIN_CONFIDENCE) == DRIFT_ORIGIN_VALUES


@pytest.mark.parametrize("origin", sorted(DRIFT_ORIGIN_VALUES))
def test_confidences_are_probabilities(origin: str):
    assert 0.0 < ORIGIN_CONFIDENCE[origin] <= 1.0


def test_default_cutoff_hides_nothing_that_ships():
    """Every shipping origin sits at or above the default cutoff, so a user
    who passes no flag sees the whole detector."""
    assert min(ORIGIN_CONFIDENCE.values()) >= DEFAULT_MIN_CONFIDENCE


def test_bucket_boundaries_are_ordered():
    assert 0.0 < REVIEW_CONFIDENCE_THRESHOLD < HIGH_CONFIDENCE_THRESHOLD <= 1.0


def test_summarize_confidence_buckets_are_exhaustive():
    class _F:
        def __init__(self, c):
            self.confidence = c

    findings = [_F(0.95), _F(0.9), _F(0.5), _F(0.2)]
    summary = summarize_confidence(findings)
    assert summary == {"high": 2, "medium": 1, "low": 1}
    assert sum(summary.values()) == len(findings)
