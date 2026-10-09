"""Regressions for defects found in adversarial review of the comparison."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.analysis.change_health.analyzer import MAX_FILE_BYTES
from repowise.core.analysis.change_health.identity import change_finding_id, finding_key
from repowise.core.analysis.change_health.matcher import FindingMatcher
from repowise.core.analysis.change_health.models import FindingKey
from repowise.core.analysis.change_health.service import (
    ChangeHealthDeltaService,
    DeltaRequest,
    _suggestion,
)
from repowise.core.analysis.change_health.sources import GitRevisionSource
from repowise.core.analysis.health import HealthFindingData, Severity
from repowise.core.analysis.health.scoring import score_file

from .conftest import python_complex


def finding(marker="long_method", *, line, severity=Severity.MEDIUM, symbol=None, impact=1.0):
    return HealthFindingData(
        biomarker_type=marker,
        severity=severity,
        file_path="app.py",
        function_name=symbol,
        line_start=line,
        line_end=line + 5,
        details={},
        health_impact=impact,
        reason="",
        dimension="defect",
    )


# -- a base-side failure must not fabricate introduced findings --------------


def test_a_base_side_read_failure_excludes_the_file_instead_of_inventing_findings(make_repo):
    """The file shrank below the size ceiling: its base blob is unreadable.

    Without both-sided bookkeeping every head finding in it reads as new, and
    the run still claims it compared both sides.
    """
    repo = make_repo()
    filler = "# pad\n" * (MAX_FILE_BYTES // 6)
    repo.commit("seed", {"app.py": filler + python_complex("tangle", 18)})
    repo.commit("shrink", {"app.py": python_complex("tangle", 18)})

    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD"))

    assert delta.skipped.get("app.py") == "base_too_large"
    assert delta.introduced_total == 0
    assert delta.status in {"partial", "unavailable"}
    assert not delta.is_clean


# -- pairing must be closest-first, not first-come-first-served --------------


def test_an_unrelated_new_finding_does_not_absorb_a_moved_one():
    """First-come pairing hid the new finding and flagged the moved one."""
    base = [finding(line=100, severity=Severity.CRITICAL)]
    head = [
        finding(line=50, severity=Severity.MEDIUM),  # genuinely new, listed first
        finding(line=102, severity=Severity.CRITICAL),  # the same one, shifted
    ]

    result = FindingMatcher().match(base, head)
    by_line = {m.head.line_start: m for m in result.matched}

    assert by_line[102].kind == "unchanged"
    assert by_line[102].base is base[0]
    assert by_line[50].kind == "introduced"
    assert by_line[50].base is None


def test_a_base_finding_left_over_inside_a_matched_group_is_resolved():
    base = [finding(line=10), finding(line=200)]
    head = [finding(line=12)]

    result = FindingMatcher().match(base, head)

    assert result.matched[0].kind == "unchanged"
    assert [f.line_start for f in result.resolved] == [200]


# -- cat-file batch parsing --------------------------------------------------


def test_a_path_with_a_space_missing_at_a_revision_does_not_derail_the_batch(make_repo):
    """``<sha>:<path> missing`` splits into three fields when the path has one."""
    repo = make_repo()
    repo.commit("seed", {"keep.py": "x = 1\n"})
    repo.commit("add", {"a file.py": "y = 2\n", "after.py": "z = 3\n"})

    source = GitRevisionSource(str(repo.path))
    pair = source.resolve("HEAD")
    # Read the added paths at the BASE revision, where neither exists yet.
    blobs = source.read(pair.base_sha, ["a file.py", "after.py", "keep.py"])

    assert "a file.py" not in blobs
    assert "after.py" not in blobs
    # The reader stayed in sync and still found the file that does exist.
    assert blobs["keep.py"] == b"x = 1\n"


def test_a_change_touching_a_path_with_a_space_still_compares(make_repo):
    repo = make_repo()
    repo.commit("seed", {"keep.py": "x = 1\n"})
    repo.commit("add", {"my module.py": python_complex("tangle", 18)})

    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    delta = service.compare(DeltaRequest(str(repo.path), "HEAD"))

    assert delta.status == "available"
    assert any(f.path == "my module.py" for f in delta.findings)


# -- ephemeral ids are scoped to their comparison ----------------------------


def test_the_same_finding_shape_in_two_comparisons_gets_two_ids():
    key = FindingKey("defect", "complex_method", "app.py", "handler")

    assert change_finding_id(key, 0, comparison="a:b") != change_finding_id(
        key, 0, comparison="c:d"
    )
    assert change_finding_id(key, 0, comparison="a:b") == change_finding_id(
        key, 0, comparison="a:b"
    )


def test_an_id_from_one_revspec_does_not_resolve_against_another(make_repo):
    repo = make_repo()
    repo.commit("seed", {"app.py": "x = 1\n"})
    first = repo.commit("add", {"app.py": python_complex("tangle", 18)})
    repo.commit("touch", {"other.py": "y = 1\n"})
    second = repo.commit("again", {"app.py": python_complex("tangle", 19)})

    service = ChangeHealthDeltaService(repo_path=str(repo.path))
    older = service.compare(DeltaRequest(str(repo.path), first))
    newer = service.compare(DeltaRequest(str(repo.path), second))

    older_ids = {f.change_finding_id for f in older.findings}
    newer_ids = {f.change_finding_id for f in newer.findings}
    assert older_ids.isdisjoint(newer_ids)


def test_line_movement_alone_keeps_the_finding_key_stable():
    moved = finding(line=400, symbol="handler")
    original = finding(line=10, symbol="handler")

    assert finding_key(moved) == finding_key(original)


# -- a capped category must not report its survivors as worsened -------------


def _scored(specs: list[tuple[str, str, int]]) -> list[HealthFindingData]:
    """Findings carrying the health_impact the real scorer would give them.

    *specs* is ``(marker, symbol, line)``, so the same function keeps the same
    identity on both sides of the comparison.
    """

    class _R:
        def __init__(self, marker: str) -> None:
            self.biomarker_type = marker
            self.severity = Severity.MEDIUM
            self.deduction = None

    _score, impacts = score_file([_R(m) for m, _sym, _line in specs])
    return [
        finding(marker, line=line, symbol=symbol, impact=impact)
        for (marker, symbol, line), impact in zip(specs, impacts, strict=True)
    ]


def test_deleting_one_finding_does_not_report_its_neighbours_as_worsened():
    """A capped category splits the cap across whatever it holds.

    Remove one finding and every survivor's ``health_impact`` rises, because
    the same cap is shared out fewer ways. Classifying on that number called a
    code-deleting diff a regression in every finding it left behind.
    """
    markers = ["function_hotspot"] * 5 + ["prior_defect", "knowledge_loss", "ownership_risk"]
    specs = [(m, f"fn_{i}", 10 + i * 10) for i, m in enumerate(markers)]
    base = _scored(specs)
    # The head deletes fn_0. Every other function is untouched.
    head = _scored(specs[1:])

    # The premise: the cap binds on both sides and the survivors' impacts rise.
    # History-only findings, so the cap is the structure-conditioned one at 0.
    from repowise.core.analysis.health.scoring import history_cap

    assert sum(f.health_impact for f in base) == pytest.approx(history_cap(0.0))
    assert sum(f.health_impact for f in head) == pytest.approx(history_cap(0.0))
    assert head[0].health_impact > base[1].health_impact

    result = FindingMatcher().match(base, head)
    assert [m.kind for m in result.matched] == ["unchanged"] * len(head)
    assert len(result.resolved) == 1


def test_a_continuous_marker_still_worsens_when_its_own_deduction_grows():
    """The signal the impact comparison exists for.

    ``coverage_gradient`` holds one severity and varies a continuous
    deduction, so only that number can say coverage got worse. Held here with
    ``health_impact`` identical on both sides, so the verdict cannot come from
    it.
    """

    def gradient(uncovered: float) -> HealthFindingData:
        return HealthFindingData(
            biomarker_type="coverage_gradient",
            severity=Severity.MEDIUM,
            file_path="app.py",
            function_name=None,
            line_start=None,
            line_end=None,
            details={"deduction": round(4.0 * uncovered, 4)},
            health_impact=0.5,
            reason="",
            dimension="defect",
        )

    worse = FindingMatcher().match([gradient(0.2)], [gradient(0.6)]).matched
    assert [m.kind for m in worse] == ["worsened"]

    better = FindingMatcher().match([gradient(0.6)], [gradient(0.2)]).matched
    assert [m.kind for m in better] == ["unchanged"]


def test_an_expected_cause_says_there_is_nothing_to_change():
    perf = SimpleNamespace(
        actionability_state="expected",
        actionability_reason="inherent_to_boundary",
        intervention_symbol=None,
    )
    assert "inherent_to_boundary" not in _suggestion(SimpleNamespace(), perf)
