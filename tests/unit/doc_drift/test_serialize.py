"""A report and a store serialize to the same bytes.

The reason this file exists: one consumer of this data holds ORM rows and
another holds a ``DocDriftReport`` with no database under it at all. Those were
two hand-written dicts waiting to drift, which is the failure
``tests/unit/dead_code/test_confidence_parity.py`` records having already
happened once. Both paths now go through one builder, and these tests assert
the identity rather than the resemblance.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from repowise.core.analysis.doc_drift.constants import DETECTION_BASIS
from repowise.core.analysis.doc_drift.models import (
    DocDriftFindingData,
    DocDriftReport,
    DriftKind,
    ResolvedDocReference,
)
from repowise.core.analysis.doc_drift.serialize import (
    collapse_reference_sites,
    derive_doc_drift_id,
    documents_with_drift,
    serialize_finding,
    serialize_reference,
    serialize_report,
    summarize_findings,
)
from repowise.core.persistence.crud.analysis.doc_drift import (
    serialize_doc_drift_reference_row,
    serialize_doc_drift_row,
)
from repowise.core.persistence.models import DocDriftFinding, DocDriftReference


def _finding(**over) -> DocDriftFindingData:
    base = {
        "kind": DriftKind.PATH,
        "file_path": "docs/architecture.md",
        "line_number": 42,
        "target": "src/auth.py",
        "confidence": 0.9,
        "reason": "No file matches this path.",
        "origin": "path_no_candidate",
        "evidence": ["line 42: see src/auth.py"],
        "raw": "src/auth.py",
        "context": "The resolver lives in src/auth.py.",
    }
    base.update(over)
    return DocDriftFindingData(**base)


def _row_from(finding: DocDriftFindingData) -> DocDriftFinding:
    """The ORM row the writer would have produced for *finding*."""
    return DocDriftFinding(
        repository_id="r1",
        file_path=finding.file_path,
        kind=str(finding.kind.value),
        line_number=finding.line_number,
        target=finding.target,
        confidence=finding.confidence,
        reason=finding.reason,
        origin=finding.origin,
        evidence_json=json.dumps(list(finding.evidence)),
        raw=finding.raw,
        context=finding.context,
    )


def test_a_report_finding_and_its_stored_row_serialize_identically():
    finding = _finding()
    assert serialize_finding(finding) == serialize_doc_drift_row(_row_from(finding))


def test_the_evidence_toggle_agrees_on_both_paths():
    finding = _finding()
    assert serialize_finding(finding, evidence=False) == serialize_doc_drift_row(
        _row_from(finding), evidence=False
    )
    assert "evidence" not in serialize_finding(finding, evidence=False)


def test_a_report_reference_and_its_stored_row_serialize_identically():
    reference = ResolvedDocReference(
        doc_path="docs/architecture.md",
        target_path="src/auth.py",
        kind=DriftKind.LINK,
        line=7,
        section="Extension points",
    )
    row = DocDriftReference(
        repository_id="r1",
        document_path=reference.doc_path,
        target_path=reference.target_path,
        kind=str(reference.kind.value),
        line_number=reference.line,
        section=reference.section,
    )
    assert serialize_reference(reference) == serialize_doc_drift_reference_row(row)


def test_an_empty_section_is_absent_rather_than_blank():
    reference = ResolvedDocReference(
        doc_path="docs/a.md", target_path="src/x.py", kind=DriftKind.PATH, line=1
    )
    assert "section" not in serialize_reference(reference)


def test_confidence_is_rounded_once_so_two_surfaces_cannot_disagree():
    finding = _finding(confidence=0.925)
    assert serialize_finding(finding)["confidence"] == 0.93
    assert serialize_doc_drift_row(_row_from(finding))["confidence"] == 0.93


class TestDeriveId:
    def test_the_same_site_gets_the_same_id_across_runs(self):
        first = derive_doc_drift_id("docs/a.md", DriftKind.PATH, 3, "src/x.py")
        second = derive_doc_drift_id("docs/a.md", "path", 3, "src/x.py")
        assert first == second

    def test_the_id_is_the_house_width(self):
        value = derive_doc_drift_id("docs/a.md", DriftKind.PATH, 3, "src/x.py")
        assert len(value) == 32
        assert value == value.lower()

    @pytest.mark.parametrize(
        "args",
        [
            ("docs/b.md", DriftKind.PATH, 3, "src/x.py"),
            ("docs/a.md", DriftKind.LINK, 3, "src/x.py"),
            ("docs/a.md", DriftKind.PATH, 4, "src/x.py"),
            ("docs/a.md", DriftKind.PATH, 3, "src/y.py"),
        ],
    )
    def test_every_part_of_the_key_moves_the_id(self, args):
        base = derive_doc_drift_id("docs/a.md", DriftKind.PATH, 3, "src/x.py")
        assert derive_doc_drift_id(*args) != base

    def test_the_separator_cannot_be_forged_by_concatenation(self):
        """Two different keys must not join to one string."""
        assert derive_doc_drift_id("a", "path", 1, "bc") != derive_doc_drift_id(
            "a", "path", 1, "b\x00c"
        )


class TestSummary:
    def test_it_counts_documents_not_findings(self):
        findings = [
            serialize_finding(_finding(file_path="docs/a.md", line_number=1)),
            serialize_finding(_finding(file_path="docs/a.md", line_number=2)),
            serialize_finding(_finding(file_path="docs/b.md")),
        ]
        summary = summarize_findings(findings)
        assert summary["findings_total"] == 3
        assert summary["documents"] == 2

    def test_it_carries_the_basis_so_a_count_never_travels_alone(self):
        assert summarize_findings([])["findings_basis"] == DETECTION_BASIS

    def test_an_empty_repository_still_names_every_tier(self):
        assert summarize_findings([])["confidence"] == {
            "high": 0,
            "medium": 0,
            "low": 0,
        }

    def test_kinds_are_ordered_so_equal_data_serializes_equal_bytes(self):
        findings = [
            serialize_finding(_finding(kind=DriftKind.PATH)),
            serialize_finding(_finding(kind=DriftKind.ANCHOR, line_number=2)),
            serialize_finding(_finding(kind=DriftKind.LINK, line_number=3)),
        ]
        assert list(summarize_findings(findings)["by_kind"]) == [
            "anchor",
            "link",
            "path",
        ]


class TestCollapseAndDrift:
    def test_a_link_with_a_fragment_is_one_mention_not_two(self):
        rows = [
            {"document": "docs/a.md", "line": 5, "kind": "link"},
            {"document": "docs/a.md", "line": 5, "kind": "anchor"},
            {"document": "docs/a.md", "line": 6, "kind": "link"},
        ]
        collapsed = collapse_reference_sites(rows)
        assert [r["kind"] for r in collapsed] == ["link", "link"]
        assert [r["line"] for r in collapsed] == [5, 6]

    def test_collapsing_does_not_mutate_the_caller_s_rows(self):
        rows = [{"document": "docs/a.md", "line": 5, "kind": "link"}]
        collapse_reference_sites(rows)[0]["kind"] = "anchor"
        assert rows[0]["kind"] == "link"

    def test_only_documents_that_carry_drift_are_listed(self):
        references = [
            {"document": "docs/a.md", "line": 1, "kind": "path"},
            {"document": "docs/b.md", "line": 1, "kind": "path"},
        ]
        assert documents_with_drift(references, {"docs/b.md": 2, "docs/c.md": 9}) == [
            {"document": "docs/b.md", "findings": 2}
        ]

    def test_a_clean_answer_lists_nothing(self):
        references = [{"document": "docs/a.md", "line": 1, "kind": "path"}]
        assert documents_with_drift(references, {}) == []


class TestSerializeReport:
    def _report(self) -> DocDriftReport:
        return DocDriftReport(
            repo_id="r1",
            analyzed_at=datetime(2026, 9, 18, tzinfo=UTC),
            total_findings=1,
            findings=[_finding()],
            confidence_summary={"high": 1, "medium": 0, "low": 0},
            documents_scanned=12,
            references_checked=340,
            verdict_summary={
                "resolved": 300,
                "missing": 1,
                "ambiguous": 5,
                "uncheckable": 34,
            },
            resolved_references=[
                ResolvedDocReference(
                    doc_path="docs/b.md",
                    target_path="src/auth.py",
                    kind=DriftKind.LINK,
                    line=9,
                ),
                ResolvedDocReference(
                    doc_path="docs/a.md",
                    target_path="src/auth.py",
                    kind=DriftKind.PATH,
                    line=3,
                ),
                ResolvedDocReference(
                    doc_path="docs/a.md",
                    target_path="src/other.py",
                    kind=DriftKind.PATH,
                    line=4,
                ),
            ],
        )

    def test_references_arrive_inverted_by_the_file_that_was_named(self):
        payload = serialize_report(self._report())
        assert set(payload["references_by_target"]) == {"src/auth.py", "src/other.py"}
        assert len(payload["references_by_target"]["src/auth.py"]) == 2

    def test_each_target_is_ordered_the_way_the_store_returns_it(self):
        payload = serialize_report(self._report())
        rows = payload["references_by_target"]["src/auth.py"]
        assert [r["document"] for r in rows] == ["docs/a.md", "docs/b.md"]

    def test_the_findings_are_the_same_dicts_a_store_would_serve(self):
        """Identical but for the id, which no store column holds."""
        report = self._report()
        payload = serialize_report(report)
        served = [{k: v for k, v in f.items() if k != "id"} for f in payload["findings"]]
        assert served == [
            serialize_doc_drift_row(_row_from(f)) for f in report.findings
        ]

    def test_every_finding_carries_the_id_the_wire_contract_requires(self):
        """A consumer serving drift from this artifact feeds a table that keys
        its rows on the id; without one, every row keys on nothing."""
        report = self._report()
        payload = serialize_report(report)
        assert [f["id"] for f in payload["findings"]] == [
            derive_doc_drift_id(f.file_path, f.kind, f.line_number, f.target)
            for f in report.findings
        ]

    def test_the_uncheckable_denominator_survives_serialization(self):
        """The count that keeps the detector honest about its own coverage."""
        payload = serialize_report(self._report())
        assert payload["verdict_summary"]["uncheckable"] == 34
        assert payload["references_checked"] == 340

    def test_the_summary_rides_along_so_no_consumer_recomputes_it(self):
        payload = serialize_report(self._report())
        assert payload["summary"] == summarize_findings(payload["findings"])
