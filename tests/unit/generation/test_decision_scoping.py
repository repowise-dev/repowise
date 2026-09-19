"""Module pages get their own decisions, ranked (#1587).

Two halves. The behaviour half exercises the helpers directly. The guard half
is an architecture check in the style of ``test_no_test_path_copies``: a page
type must not go back to slicing the repo-wide list, because the slice is not
only rendered, it is prompt context for the page being written.
"""

from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.decisions.scope import SCOPE_BASIS_FOOTPRINT
from repowise.core.generation.page_generator.helpers import (
    build_decision_maps,
    decisions_for_files,
    rank_decisions,
)

LEVELS = Path(__file__).resolve().parents[3] / (
    "packages/core/src/repowise/core/generation/page_generator/levels.py"
)


def _decision(title: str, *, confidence: float | None = 1.0, evidence: str = "e.py") -> dict:
    payload = {"title": title, "source": "git", "evidence_file": evidence}
    if confidence is not None:
        payload["confidence"] = confidence
    return payload


def _record(
    title: str,
    *,
    status: str = "proposed",
    confidence: float = 0.5,
    scope_basis: str = "",
    affected_files: list[str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        title=title,
        decision=f"{title}: do the thing",
        rationale="because the alternative was measured slower",
        source="session",
        confidence=confidence,
        evidence_file="e.py",
        affected_files=affected_files or ["src/app.py"],
        status=status,
        scope_basis=scope_basis,
    )


def test_module_decisions_are_scoped_to_the_modules_files():
    a1 = _decision("a1", confidence=0.4, evidence="a/x.py")
    a2 = _decision("a2", confidence=0.9, evidence="a/y.py")
    b1 = _decision("b1", confidence=1.0, evidence="b/z.py")
    by_file = {"a/x.py": [a1, a2], "a/y.py": [a2], "b/z.py": [b1]}

    module_a = decisions_for_files(by_file, ["a/x.py", "a/y.py"])
    assert [d["title"] for d in module_a] == ["a2", "a1"], "most confident first"

    module_b = decisions_for_files(by_file, ["b/z.py"])
    assert [d["title"] for d in module_b] == ["b1"], "another module's decisions do not leak in"

    assert decisions_for_files(by_file, ["unindexed.py"]) == []


def test_a_decision_affecting_several_files_appears_once():
    shared = _decision("shared", evidence="a/x.py")
    by_file = {"a/x.py": [shared], "a/y.py": [shared], "a/z.py": [shared]}

    assert len(decisions_for_files(by_file, ["a/x.py", "a/y.py", "a/z.py"])) == 1


def test_two_records_sharing_a_title_are_not_collapsed():
    # The dedupe key mirrors the decision_records uniqueness constraint
    # (title, source, evidence_file), so a repeated title from a different
    # source or file is a different decision.
    same_title_other_file = [
        _decision("Use X", evidence="a/x.py"),
        _decision("Use X", evidence="b/z.py"),
    ]
    by_file = {"a/x.py": same_title_other_file}

    assert len(decisions_for_files(by_file, ["a/x.py"])) == 2


def test_ranking_is_stable_and_tolerates_a_missing_confidence():
    ranked = rank_decisions(
        [_decision("low", confidence=0.1), _decision("none", confidence=None), _decision("high")]
    )
    assert [d["title"] for d in ranked] == ["high", "low", "none"]

    # Equal confidence keeps discovery order rather than reshuffling.
    tied = rank_decisions([_decision("first"), _decision("second"), _decision("third")])
    assert [d["title"] for d in tied] == ["first", "second", "third"]


def test_no_page_type_slices_the_repo_wide_decision_list_unranked():
    """A raw ``decisions_all`` slice is the bug this closes.

    Repo-wide scope is correct for the overview; taking its first N is not.
    Anything reaching for ``decisions_all`` has to say how it ordered them.
    """
    source = LEVELS.read_text(encoding="utf-8")
    offenders = [
        line.strip()
        for line in source.splitlines()
        if re.search(r"decisions_all\s*\[", line) and "rank_decisions" not in line
    ]
    assert not offenders, "slice a ranked list, or scope it with decisions_for_files: " + "; ".join(
        offenders
    )


# ---------------------------------------------------------------------------
# Authority: a page has room for a handful, and a proposal is not a rule
# ---------------------------------------------------------------------------


def test_an_accepted_decision_outranks_a_more_confident_proposal():
    """A confidence score and an acceptance measure different things.

    Confidence says how well a candidate is grounded in what it was mined
    from. An acceptance says a person read it and agreed it binds. The
    promotion bar admits several times as many proposals as it used to, and
    without this they crowd the rules off the page.
    """
    ranked = rank_decisions(
        [
            {"title": "proposal", "confidence": 0.99, "status": "proposed"},
            {"title": "rule", "confidence": 0.10, "status": "active"},
        ]
    )
    assert [d["title"] for d in ranked] == ["rule", "proposal"]


def test_a_payload_with_no_status_still_ranks_by_confidence():
    """Every pre-existing caller passed no status at all."""
    ranked = rank_decisions(
        [{"title": "low", "confidence": 0.1}, {"title": "high", "confidence": 0.9}]
    )
    assert [d["title"] for d in ranked] == ["high", "low"]


def test_a_dismissed_record_never_reaches_a_page():
    """A tombstone is what stops re-extraction re-proposing something.

    Publishing it as a decision says the opposite of what the dismissal meant.
    """
    report = SimpleNamespace(
        decisions=[
            _record("Kept"),
            _record("Rejected on review", status="dismissed"),
        ]
    )

    by_file, all_decisions = build_decision_maps(report)

    assert [d["title"] for d in all_decisions] == ["Kept"]
    assert [d["title"] for d in by_file["src/app.py"]] == ["Kept"]


def test_the_status_reaches_the_payload_so_ranking_can_read_it():
    report = SimpleNamespace(decisions=[_record("A rule", status="active")])

    _, all_decisions = build_decision_maps(report)

    assert all_decisions[0]["status"] == "active"


def test_a_record_that_carries_no_status_reads_as_a_proposal():
    """Not as a rule: the absence of an acceptance is not an acceptance."""
    record = _record("Unknown")
    del record.status
    report = SimpleNamespace(decisions=[record])

    _, all_decisions = build_decision_maps(report)

    assert all_decisions[0]["status"] == "proposed"


# ---------------------------------------------------------------------------
# A commit footprint reaches the overview but not a file page
# ---------------------------------------------------------------------------


def test_a_footprint_is_kept_out_of_the_per_file_index():
    """One 38-file record headed five unrelated file pages before this.

    Its files are the footprint of the commit it was mined from, so it is a
    true statement about the change and a false one about most of the files.
    """
    report = SimpleNamespace(
        decisions=[
            _record("Narrow rule"),
            _record(
                "Collapse unchanged re-reads",
                scope_basis=SCOPE_BASIS_FOOTPRINT,
                affected_files=["src/app.py", "src/unrelated.py"],
            ),
        ]
    )

    by_file, _ = build_decision_maps(report)

    assert [d["title"] for d in by_file["src/app.py"]] == ["Narrow rule"]
    assert "src/unrelated.py" not in by_file


def test_a_footprint_still_reaches_the_repository_overview():
    """A claim about a whole change belongs on the page about the whole repo."""
    report = SimpleNamespace(
        decisions=[_record("Wide rule", scope_basis=SCOPE_BASIS_FOOTPRINT)]
    )

    _, all_decisions = build_decision_maps(report)

    assert [d["title"] for d in all_decisions] == ["Wide rule"]


def test_a_record_with_no_basis_attribute_still_binds():
    """Resume rehydrates records as plain namespaces.

    That path now carries the basis, but a namespace built somewhere else
    must not silently lose its file pages.
    """
    record = _record("Legacy shape")
    del record.scope_basis
    report = SimpleNamespace(decisions=[record])

    by_file, _ = build_decision_maps(report)

    assert [d["title"] for d in by_file["src/app.py"]] == ["Legacy shape"]
