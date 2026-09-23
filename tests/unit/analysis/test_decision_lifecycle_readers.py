"""The acceptance contract's record readers, over plain mappings."""

from __future__ import annotations

import pytest

from repowise.core.analysis.decisions.lifecycle import (
    AGREEMENT_SCOPE,
    is_repo_wide,
    record_blockers,
    record_evidence,
    record_scope,
    requirement,
)
from repowise.core.persistence.crud.authority import decision_fields
from repowise.core.persistence.models import DecisionRecord


def test_reason_falls_back_to_context_then_stops() -> None:
    assert requirement({"rationale": " ", "context": "forced by X"}).reason == "forced by X"
    assert requirement({"rationale": "why", "context": "ctx"}).reason == "why"
    # `decision` is the what, never the why.
    assert requirement({"rationale": "", "context": "", "decision": "use Y"}).reason == ""
    assert requirement({"rationale": "why"}, reason="stated").reason == "stated"


def test_file_less_agreement_governs_the_repository() -> None:
    agreement = {"kind": "agreement", "affected_files": [], "affected_modules": []}
    assert record_scope(agreement) == [AGREEMENT_SCOPE]
    assert is_repo_wide(agreement)
    scoped = {**agreement, "affected_files": ["src/a.py"]}
    assert record_scope(scoped) == ["src/a.py"]
    assert not is_repo_wide(scoped)
    assert record_scope({"kind": "architectural"}) == []


@pytest.mark.parametrize(
    "files, modules",
    [(["  "], ["pkg"]), ('["  "]', '["pkg"]')],
)
def test_lists_or_their_json_text(files, modules) -> None:
    rec = {"affected_files": files, "affected_modules": modules}
    assert record_scope(rec) == ["pkg"]


def test_evidence_and_blockers() -> None:
    rec = {"evidence_commits": '["abc"]', "evidence_file": "AGENTS.md"}
    assert record_evidence(rec) == ["abc", "AGENTS.md"]
    assert record_blockers({"kind": "agreement", "rationale": "r", **rec}) == []
    # A hand-typed record is its own evidence once someone accepts it.
    assert requirement({"source": "cli"}, accepter="me").evidence == ["accepted by me"]


def test_orm_projection_matches_the_mapping_readers() -> None:
    rec = DecisionRecord(
        affected_files_json="[]",
        affected_modules_json='["pkg"]',
        kind="architectural",
        rationale="",
        context="ctx",
        source="inline_marker",
        evidence_commits_json='["abc"]',
        evidence_file=None,
    )
    fields = decision_fields(rec)
    assert record_scope(fields) == ["pkg"]
    assert record_blockers(fields) == []
