"""Regressions for defects found in adversarial review of the comparison."""

from __future__ import annotations

from repowise.core.analysis.change_health.analyzer import MAX_FILE_BYTES
from repowise.core.analysis.change_health.identity import change_finding_id, finding_key
from repowise.core.analysis.change_health.matcher import FindingMatcher
from repowise.core.analysis.change_health.models import FindingKey
from repowise.core.analysis.change_health.service import ChangeHealthDeltaService, DeltaRequest
from repowise.core.analysis.change_health.sources import GitRevisionSource
from repowise.core.analysis.health import HealthFindingData, Severity

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
