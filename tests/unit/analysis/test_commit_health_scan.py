"""Row building and budget behaviour of the per-commit health scan."""

from __future__ import annotations

import time

import pytest

from repowise.core.analysis.change_health.commit_scan import scan_commits
from repowise.core.analysis.change_health.models import (
    AnalysisFingerprint,
    ChangeFinding,
    ChangeHealthDelta,
    RevisionId,
    ScopeCounts,
)

FINGERPRINT = AnalysisFingerprint(
    analyzer_version=21, rules_fingerprint="abc", performance_model_version=2
)


def _finding(fid: str, severity: str = "high", impact: float = 1.0, **over) -> ChangeFinding:
    return ChangeFinding(
        change_finding_id=fid,
        change_kind=over.pop("change_kind", "introduced"),
        dimension="defect",
        biomarker_type="long_method",
        severity=severity,
        path=over.pop("path", "a.py"),
        symbol="f",
        line_start=1,
        line_end=9,
        reason="added 40 lines",
        attribution_basis="added_lines",
        attribution_confidence="high",
        attribution_detail="",
        suggestion="",
        follow_up="",
        health_impact=impact,
        **over,
    )


def _delta(findings: list[ChangeFinding], status: str = "available", **over) -> ChangeHealthDelta:
    return ChangeHealthDelta(
        status=status,
        explanation="",
        base=RevisionId("a^", "a" * 40, "commit"),
        head=RevisionId("a", "b" * 40, "commit"),
        comparison_basis="",
        fingerprint=FINGERPRINT,
        scope=ScopeCounts(changed=3, eligible=3, analyzed=3),
        findings=findings,
        **over,
    )


class _StubService:
    """Stands in for the real comparison; records what it was asked for."""

    def __init__(self, by_sha: dict, delay: float = 0.0) -> None:
        self.by_sha = by_sha
        self.delay = delay
        self.seen: list[str] = []

    def compare(self, request):
        self.seen.append(request.revspec)
        if self.delay:
            time.sleep(self.delay)
        result = self.by_sha[request.revspec]
        if isinstance(result, Exception):
            raise result
        return result


def test_a_delta_row_carries_the_fingerprint_that_produced_it() -> None:
    service = _StubService({"aaa": _delta([_finding("f0")], resolved_total=2)})

    scan = scan_commits("/repo", ["aaa"], service=service)

    (row,) = scan.delta_rows
    assert row["sha"] == "aaa"
    assert row["analyzer_version"] == 21
    assert row["rules_fingerprint"] == "abc"
    assert row["performance_model_version"] == 2
    assert (row["introduced_count"], row["resolved_count"]) == (1, 2)
    assert row["findings_stored"] == 1


def test_findings_are_stored_worst_first() -> None:
    findings = [
        _finding("low", "low", 0.1),
        _finding("crit", "critical", 0.1),
        _finding("med", "medium", 0.1),
    ]
    service = _StubService({"aaa": _delta(findings)})

    scan = scan_commits("/repo", ["aaa"], service=service)

    assert [r["change_finding_id"] for r in scan.finding_rows] == ["crit", "med", "low"]
    assert [r["position"] for r in scan.finding_rows] == [0, 1, 2]


def test_the_cap_keeps_the_worst_and_the_row_still_reports_the_true_total() -> None:
    findings = [_finding(f"f{i}", "high", float(i)) for i in range(10)]
    service = _StubService({"aaa": _delta(findings)})

    scan = scan_commits("/repo", ["aaa"], service=service, max_findings=3)

    (row,) = scan.delta_rows
    assert row["introduced_count"] == 10
    assert row["findings_stored"] == 3
    assert [r["change_finding_id"] for r in scan.finding_rows] == ["f9", "f8", "f7"]


@pytest.mark.parametrize("status", ["unavailable", "too_large", "unsupported_range"])
def test_a_comparison_that_could_not_run_stores_nothing(status: str) -> None:
    """A stored row would make "no findings" mean two different things."""
    service = _StubService({"aaa": _delta([], status=status)})

    scan = scan_commits("/repo", ["aaa"], service=service)

    assert scan.delta_rows == []
    assert scan.skipped == 1


def test_a_partial_comparison_is_stored_and_says_so() -> None:
    service = _StubService({"aaa": _delta([_finding("f0")], status="partial")})

    scan = scan_commits("/repo", ["aaa"], service=service)

    assert scan.delta_rows[0]["status"] == "partial"


def test_one_commit_blowing_up_does_not_abandon_the_rest() -> None:
    service = _StubService({"bad": RuntimeError("boom"), "good": _delta([_finding("f0")])})

    scan = scan_commits("/repo", ["bad", "good"], service=service)

    assert [r["sha"] for r in scan.delta_rows] == ["good"]
    assert scan.skipped == 1


def test_the_budget_stops_the_scan_and_says_it_ran_out() -> None:
    by_sha = {sha: _delta([]) for sha in ("a", "b", "c", "d")}
    service = _StubService(by_sha, delay=0.05)

    scan = scan_commits("/repo", list(by_sha), service=service, budget_seconds=0.08)

    assert scan.exhausted_budget
    assert len(service.seen) < 4
    assert service.seen == list(by_sha)[: len(service.seen)]  # in the order given


def test_no_shas_is_not_an_error() -> None:
    scan = scan_commits("/repo", [], service=_StubService({}))

    assert (scan.delta_rows, scan.finding_rows, scan.scanned) == ([], [], 0)
