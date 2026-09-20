"""The response models that must not drop a key they were not told about.

Three payloads are assembled outside this layer: the composed refactoring
opportunity, the stored rollup, and the chat artifact's per-tool result. Their
models are deliberately open, and these pin that openness — a model that closed
would silently drop the keys it did not declare, which is the failure the whole
contract effort exists to prevent.
"""

from __future__ import annotations

from repowise.server.schemas import (
    ChatArtifactEnvelope,
    RefactoringOpportunitiesResponse,
    RefactoringOpportunityDetailResponse,
)


def test_opportunity_detail_carries_keys_it_never_declared() -> None:
    payload = {
        "resolved": True,
        "steps": [{"plan_id": "p1"}],
        "steps_total": 1,
        "steps_emitted": 1,
        "validation_profiles": [],
        "affected_files": ["a.py"],
        "lead_finding_ids": [],
        "next_actions": [],
        # Composed in core, and unknown to this module by design.
        "opportunity_id": "o1",
        "lead_type": "extract_method",
        "evidence": [{"finding_id": "f1"}],
        "evidence_total": 1,
    }

    dumped = RefactoringOpportunityDetailResponse.model_validate(payload).model_dump(
        exclude_unset=True
    )

    assert [key for key in payload if key not in dumped] == []


def test_the_opportunity_page_omits_ignored_arguments_until_there_are_some() -> None:
    body = {
        "items": [],
        "total": 0,
        "offset": 0,
        "has_more": False,
        "next_offset": None,
        "facets": {},
        "summary": None,
    }

    without = RefactoringOpportunitiesResponse.model_validate(body).model_dump(exclude_unset=True)
    with_ignored = RefactoringOpportunitiesResponse.model_validate(
        {**body, "ignored_arguments": {"effort": "unknown value"}}
    ).model_dump(exclude_unset=True)

    assert "ignored_arguments" not in without
    assert with_ignored["ignored_arguments"] == {"effort": "unknown value"}


def test_an_artifact_carries_whichever_tool_shape_it_holds() -> None:
    envelope = ChatArtifactEnvelope.model_validate(
        {
            "id": "a1",
            "version": 1,
            "type": "risk",
            "tool_name": "get_risk",
            "title": "get_risk",
            "presentation": "card",
            "data": {"directive": {"status": "ok"}, "score": 4.2},
            "evidence": {"basis": "extracted", "limits": {"collections": []}},
            "pinned": False,
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    assert envelope.data["directive"] == {"status": "ok"}
    assert envelope.evidence["limits"] == {"collections": []}
