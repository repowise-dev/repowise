"""The bundle must compose the primitives without changing what they say.

Two properties are worth protecting here. First, a lane nobody consulted has to
read as ``unsupported`` rather than as an empty answer -- the whole point of
composing evidence in one place is that a caller can tell the difference.
Second, the manifest is the one counted universe: every lane that takes a set
of changed files has to take it from there, so the filters cannot be applied
twice with two different results.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from repowise.core.analysis.branch_overlap import BranchOverlap
from repowise.core.analysis.change_health import (
    ChangeHealthDeltaService,
    DeltaRequest,
    GitRevisionSource,
    MappingRevisionSource,
)
from repowise.core.analysis.change_review import (
    CHANGE_REVIEW_CONTRACT_VERSION,
    LANES,
    ChangeManifestEntry,
    ChangeReviewEvidence,
    ChangeReviewRequest,
    ChangeReviewService,
    ContractInputs,
)
from repowise.core.analysis.change_risk import assess_change, features_from_file_changes
from repowise.core.analysis.independent_changes import IndependentChangeEvidence
from repowise.core.analysis.review_directive import review_directive

from .conftest import Repo, python_complex


def _service(repo: Repo, **kwargs) -> ChangeReviewService:
    return ChangeReviewService(GitRevisionSource(str(repo.path)), repo_path=str(repo.path), **kwargs)


def _supplied_source(repo: Repo) -> MappingRevisionSource:
    """The same change, served from bytes instead of from a checkout."""
    git_source = GitRevisionSource(str(repo.path))
    pair = git_source.resolve("HEAD")
    return MappingRevisionSource(
        pair,
        git_source.read(pair.base_sha, [c.base_path for c in pair.changes if c.base_path]),
        git_source.read(pair.head_sha, [c.head_path for c in pair.changes if c.head_path]),
    )


def _two_commit_repo(make_repo, name: str = "bundle") -> Repo:
    repo: Repo = make_repo(name)
    repo.commit("base", {"app/a.py": python_complex("run", 2), "docs/readme.md": "hello\n"})
    repo.commit("head", {"app/a.py": python_complex("run", 8), "docs/readme.md": "hello there\n"})
    return repo


# ---------------------------------------------------------------------------
# Lane honesty
# ---------------------------------------------------------------------------


def test_every_lane_is_named_even_when_nothing_was_collected(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0))

    assert set(bundle.lanes) == set(LANES)
    assert bundle.lanes["tests"].state == "unsupported"
    assert bundle.lanes["contracts"].state == "unsupported"
    assert bundle.lanes["independent_changes"].state == "unsupported"
    # An unsupported lane is an unasked question, and it has to say why.
    assert bundle.lanes["tests"].reason


def test_an_uncollected_lane_is_not_an_empty_one(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0))

    assert bundle.tests is None
    assert bundle.independent_changes is None
    assert bundle.contracts is not None and bundle.contracts.status == "unavailable"


def test_a_consulted_lane_that_found_nothing_is_available(make_repo):
    repo = _two_commit_repo(make_repo)
    evidence = ChangeReviewEvidence(
        independent=IndependentChangeEvidence(
            paths=("app/a.py",), groupable=frozenset(), pairs=frozenset(), linked=frozenset()
        )
    )

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0), evidence=evidence
    )

    assert bundle.lanes["independent_changes"].state == "available"
    # One change is not two independent ones; the lane ran and said so.
    assert bundle.independent_changes is None


def test_an_unresolvable_revspec_marks_every_lane_unavailable(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(ChangeReviewRequest(revspec="no-such-ref"))

    assert {state.state for state in bundle.lanes.values()} == {"unavailable"}
    assert bundle.directive.status == "unknown"
    assert bundle.base is None and bundle.head is None
    assert bundle.degraded_lanes == LANES


def test_a_skipped_lane_says_the_caller_skipped_it(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(skip=frozenset({"health", "risk"})),
    )

    assert bundle.health is None and bundle.risk is None
    assert bundle.lanes["health"].state == "unsupported"
    # Prior fixes ride on the risk walk, so skipping risk unsupports them too.
    assert bundle.lanes["prior_fixes"].state == "unsupported"


# ---------------------------------------------------------------------------
# One counted universe
# ---------------------------------------------------------------------------


def test_the_manifest_carries_the_change_the_source_reported(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0))

    paths = {entry.path for entry in bundle.manifest}
    assert paths == {"app/a.py", "docs/readme.md"}
    entry = next(e for e in bundle.manifest if e.path == "app/a.py")
    assert entry.status == "modified"
    assert entry.diff_reliability == "parsed"
    assert entry.added_line_count > 0


def test_filters_apply_once_and_every_lane_sees_the_result(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", extensions=(".py",), baseline=0)
    )

    assert {entry.path for entry in bundle.manifest} == {"app/a.py"}
    # The health lane filters from the same request rather than its own copy.
    assert bundle.health is not None
    assert bundle.health.scope.changed == len(bundle.manifest)


def test_exclusions_reach_the_manifest_and_the_health_scope(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", exclude_patterns=("docs/",), baseline=0)
    )

    assert {entry.path for entry in bundle.manifest} == {"app/a.py"}
    assert bundle.health is not None
    assert bundle.health.scope.changed == 1


def test_a_change_with_no_counted_file_says_so(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", extensions=(".rs",), baseline=0)
    )

    assert bundle.manifest == ()
    assert bundle.lanes["manifest"].state == "unavailable"


def test_a_deleted_path_keeps_its_base_side_in_the_manifest(make_repo):
    repo: Repo = make_repo("deletion")
    repo.commit("base", {"app/gone.py": python_complex("run", 2)})
    repo.remove("app/gone.py")
    repo.commit("head")

    bundle = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0))

    entry = next(e for e in bundle.manifest if e.path == "app/gone.py")
    assert entry.status == "deleted"
    assert entry.head_path is None
    assert entry.base_path == "app/gone.py"
    # Nothing was added, so nothing may claim added lines for it.
    assert entry.added_ranges == ()


def test_added_lines_collapse_into_inclusive_spans():
    from repowise.core.analysis.change_health import FileChange
    from repowise.core.analysis.changed_lines import FileDiff

    diff = FileDiff(path="a.py", new_lines={1, 2, 3, 7, 8, 20})
    entry = ChangeManifestEntry.from_file_change(
        FileChange(head_path="a.py", base_path="a.py", status="modified", diff=diff)
    )

    assert entry.added_ranges == ((1, 3), (7, 8), (20, 20))
    assert entry.added_line_count == 6


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def test_contract_impact_reads_its_scope_from_the_manifest(make_repo):
    """The lane must not be handed a changed-file set that can disagree."""
    repo: Repo = make_repo("contracts")
    repo.commit("base", {"app/a.py": "def run(value):\n    return value\n"})
    repo.commit("head", {"app/a.py": "def run(value, extra):\n    return value + extra\n"})

    base_parsed = [
        {
            "file_info": {"path": "app/a.py"},
            "symbols": [
                {
                    "id": "app/a.py::run",
                    "name": "run",
                    "kind": "function",
                    "signature": "run(value)",
                    "start_line": 1,
                    "end_line": 2,
                }
            ],
        }
    ]
    head_parsed = [
        {
            "file_info": {"path": "app/a.py"},
            "symbols": [
                {
                    "id": "app/a.py::run",
                    "name": "run",
                    "kind": "function",
                    "signature": "run(value, extra)",
                    "start_line": 1,
                    "end_line": 2,
                }
            ],
        }
    ]
    graph = {
        "links": [
            {"source": "other/b.py::caller", "target": "app/a.py::run", "edge_type": "calls"}
        ]
    }

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(
            contracts=ContractInputs(graph=graph, base_parsed=base_parsed, head_parsed=head_parsed)
        ),
    )

    assert bundle.lanes["contracts"].state == "available"
    assert bundle.contracts is not None
    breaking = bundle.contracts.breaking
    assert [c.name for c in breaking] == ["run"]
    assert breaking[0].outside_callers == ["other/b.py::caller"]


def test_a_snapshot_base_makes_the_contract_lane_partial(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(
            contracts=ContractInputs(
                graph={}, base_parsed=[], head_parsed=[], base_is_snapshot=True
            )
        ),
    )

    assert bundle.lanes["contracts"].state == "partial"
    assert "snapshot" in (bundle.lanes["contracts"].reason or "")


def test_the_directive_carries_the_tests_the_test_lane_found(make_repo):
    repo = _two_commit_repo(make_repo)
    tests = {
        "recommendations": [
            {"test_id": "tests/test_a.py::test_run", "basis": "measured"},
            {"test_id": "tests/test_b.py::test_other", "basis": "inferred"},
        ],
        "coverage": {"map_present": True, "status": "current"},
    }

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(tests=tests),
    )

    run = [a for a in bundle.directive.actions if a.kind == "run_tests"]
    assert len(run) == 1
    assert run[0].targets == ("tests/test_a.py::test_run", "tests/test_b.py::test_other")
    assert run[0].evidence_basis == "measured"
    assert bundle.lanes["tests"].state == "available"


def test_no_coverage_map_and_no_candidates_asks_for_coverage(make_repo):
    repo = _two_commit_repo(make_repo)
    tests = {"recommendations": [], "coverage": {"map_present": False, "status": "unavailable"}}

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(tests=tests),
    )

    assert bundle.lanes["tests"].state == "partial"
    kinds = {a.kind for a in bundle.directive.actions}
    assert "establish_test_coverage" in kinds


def test_a_supplied_delta_is_used_rather_than_recompared(make_repo):
    """A surface that runs the comparison concurrently keeps that concurrency."""
    repo = _two_commit_repo(make_repo)
    service = ChangeHealthDeltaService(GitRevisionSource(str(repo.path)), repo_path=str(repo.path))
    precomputed = service.compare(
        DeltaRequest(repo_path=str(repo.path), revspec="HEAD", extensions=(), exclude_patterns=())
    )

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(health=precomputed),
    )

    assert bundle.health is precomputed
    assert bundle.lanes["health"].state in {"available", "partial"}
    # The directive is decided from the supplied delta, not from a second one.
    assert bundle.directive.status == review_directive(precomputed).status


def test_a_supplied_delta_beats_a_skip(make_repo):
    """Handing the lane an answer is not the same as asking for it to be skipped."""
    repo = _two_commit_repo(make_repo)
    service = ChangeHealthDeltaService(GitRevisionSource(str(repo.path)), repo_path=str(repo.path))
    precomputed = service.compare(
        DeltaRequest(repo_path=str(repo.path), revspec="HEAD", extensions=(), exclude_patterns=())
    )

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(health=precomputed, skip=frozenset({"health"})),
    )

    assert bundle.health is precomputed


def test_a_supplied_risk_result_is_used_rather_than_rescored(make_repo):
    """A caller holding file stats from an API gets its own numbers back."""
    repo = _two_commit_repo(make_repo)
    supplied = assess_change(
        features_from_file_changes([("app/a.py", 40, 4)], ref="supplied"),
    )

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(risk=supplied),
    )

    assert bundle.risk is supplied
    assert bundle.lanes["risk"].state == "available"


def test_risk_without_a_checkout_or_a_result_is_unsupported(make_repo):
    repo = _two_commit_repo(make_repo)
    supplied = _supplied_source(repo)

    bundle = ChangeReviewService(supplied).review(ChangeReviewRequest(revspec="HEAD"))

    assert bundle.risk is None
    assert bundle.lanes["risk"].state == "unsupported"
    # The lanes that need no checkout still answer.
    assert bundle.lanes["health"].state in {"available", "partial"}
    assert bundle.manifest


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.CalledProcessError(128, ["git", "diff"], stderr="bad revision"),
        subprocess.TimeoutExpired(["git", "diff"], 60),
    ],
    ids=["git_failed", "git_timed_out"],
)
def test_a_failed_score_degrades_its_lane_rather_than_the_bundle(make_repo, monkeypatch, failure):
    """git failing inside one lane must not cost the caller every other lane."""
    repo = _two_commit_repo(make_repo)
    monkeypatch.setattr(
        "repowise.core.analysis.change_review.service.score_live_change",
        lambda *a, **k: (_ for _ in ()).throw(failure),
    )

    bundle = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0))

    assert bundle.risk is None
    assert bundle.lanes["risk"].state == "degraded"
    assert bundle.lanes["risk"].reason
    # The fix record rides on the same walk, so it cannot claim to be available.
    assert bundle.lanes["prior_fixes"].state == "unsupported"
    # Everything that did not depend on that walk still answered.
    assert bundle.manifest
    assert bundle.lanes["health"].state in {"available", "partial"}


def test_supplied_branch_overlap_is_carried_through(make_repo):
    repo = _two_commit_repo(make_repo)
    overlap = BranchOverlap(base="main", current="feature", branches=(), scanned=12, total=12)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(branch_overlap=overlap),
    )

    assert bundle.branch_overlap is overlap
    assert bundle.lanes["branch_overlap"].state == "available"
    assert bundle.as_dict()["branch_overlap"] == overlap.to_dict()


def test_an_absent_branch_scan_can_say_why(make_repo):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(
        ChangeReviewRequest(revspec="HEAD", baseline=0),
        evidence=ChangeReviewEvidence(branch_overlap_reason="the remote was unreachable"),
    )

    assert bundle.lanes["branch_overlap"].state == "unsupported"
    assert bundle.lanes["branch_overlap"].reason == "the remote was unreachable"


def test_a_supplied_source_bundles_the_same_change_as_a_checkout(make_repo):
    repo = _two_commit_repo(make_repo)
    supplied = _supplied_source(repo)
    request = ChangeReviewRequest(revspec="HEAD", baseline=0)

    from_git = _service(repo).review(request)
    from_supplied = ChangeReviewService(supplied).review(request)

    assert [e.as_dict() for e in from_supplied.manifest] == [
        e.as_dict() for e in from_git.manifest
    ]
    assert from_supplied.directive.status == from_git.directive.status
    assert from_supplied.directive.fingerprint == from_git.directive.fingerprint


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_the_serialized_bundle_is_versioned_and_json_safe(make_repo):
    repo = _two_commit_repo(make_repo)

    payload = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0)).as_dict()

    assert payload["contract_version"] == CHANGE_REVIEW_CONTRACT_VERSION
    assert set(payload["lanes"]) == set(LANES)
    json.dumps(payload)  # raises on anything a surface could not send


def test_serialization_keeps_every_lane_as_a_key(make_repo):
    repo = _two_commit_repo(make_repo)

    payload = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0)).as_dict()

    # A lane that answered nothing is still present, so a reader never has to
    # infer the difference between "absent" and "empty" from a missing key.
    for lane in ("risk", "health", "contracts", "tests", "branch_overlap"):
        assert lane in payload


def test_the_directive_serializes_its_fingerprints(make_repo):
    repo = _two_commit_repo(make_repo)

    payload = _service(repo).review(ChangeReviewRequest(revspec="HEAD", baseline=0)).as_dict()

    assert payload["directive"]["fingerprint"]
    for action in payload["directive"]["actions"]:
        assert action["fingerprint"]


@pytest.mark.parametrize("revspec", ["HEAD", "HEAD~1..HEAD", "HEAD~1...HEAD"])
def test_common_revspec_spellings_all_produce_a_bundle(make_repo, revspec):
    repo = _two_commit_repo(make_repo)

    bundle = _service(repo).review(ChangeReviewRequest(revspec=revspec, baseline=0))

    assert bundle.manifest
    assert bundle.lanes["manifest"].state == "available"
