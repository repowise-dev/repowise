"""Sealed trust fixture for ``get_why`` evidence identity and provenance."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from repowise.core.persistence.models import DecisionEvidence
from repowise.server.mcp_server._why_evidence import (
    annotate_response_evidence,
    decision_collapse_key,
    provenance_for_source,
)
from repowise.server.mcp_server.tool_why import _rank_keyword_matches


def _row_by_id(result: dict, decision_id: str) -> dict:
    """The row for *decision_id*, from whichever lane the response put it in.

    Path mode splits accepted decisions from candidates, so a record's lane
    depends on whether anybody has accepted it. Its public reference must not:
    that is the contract these tests exist to hold.
    """
    for lane in ("decisions", "candidates"):
        for row in result.get(lane) or []:
            if row["id"] == decision_id:
                return row
    raise AssertionError(f"{decision_id} is in no lane of {sorted(result)}")


_A = "a" * 40
_B = "b" * 40
_C = "c" * 40
_D = "d" * 40
_E = "e" * 40
_F = "f" * 40


def _record(
    id_: str,
    *,
    title: str,
    source: str = "pr",
    commits: list[str] | None = None,
    evidence_file: str | None = None,
    evidence_line: int | None = None,
    source_quote: str = "",
    files: list[str] | None = None,
    status: str = "proposed",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=id_,
        title=title,
        status=status,
        source=source,
        evidence_commits_json=json.dumps(commits or []),
        evidence_file=evidence_file,
        evidence_line=evidence_line,
        source_quote=source_quote,
        affected_files_json=json.dumps(files or []),
        affected_modules_json="[]",
        decision="Keep the cache bounded and deterministic",
        rationale="Avoid unbounded memory while preserving stable behavior",
        context="The cache needs a fixed policy",
        consequences_json="[]",
        tags_json="[]",
        confidence=0.9,
        staleness_score=0.0,
    )


@pytest.fixture
def sealed_evidence_fixture() -> tuple[list[SimpleNamespace], dict]:
    target = "src/cache.py"
    records = [
        _record(
            "governing",
            title="Bound the target cache",
            source="session",
            commits=[_A, _B],
            files=[target],
            status="active",
        ),
        _record(
            "reextract",
            title="Re-extracted cache bound",
            source="session",
            commits=[_B, _A],
            files=[target],
            status="active",
        ),
        _record("standing", title="Repository-wide cache guidance", commits=[_C]),
        _record("similar-one", title="Keep cache behavior stable", commits=[_D]),
        _record("similar-two", title="Keep cache behavior stable", commits=[_E]),
        _record(
            "range",
            title="Explain the cache range",
            source="inline_marker",
            evidence_file="src\\cache.py",
            evidence_line=20,
            source_quote="exact supporting range",
        ),
        _record("legacy", title="Legacy cache note", evidence_file=target),
        _record("old", title="Old cache choice", commits=[_F], status="superseded"),
        _record("new", title="Replacement cache choice", commits=[_A], status="active"),
    ]
    payload = {
        "mode": "search",
        "query": "why is the target cache bounded",
        "decisions": [
            {
                "id": "governing",
                "title": "Bound the target cache",
                "status": "active",
                "source": "session",
                "restates": ["reextract"],
            },
            {
                "id": "similar-one",
                "title": "Keep cache behavior stable",
                "status": "proposed",
                "source": "pr",
            },
            {
                "id": "similar-two",
                "title": "Keep cache behavior stable",
                "status": "proposed",
                "source": "pr",
            },
            {
                "id": "new",
                "title": "Replacement cache choice",
                "status": "active",
                "source": "pr",
                "lineage": [
                    {
                        "id": "old",
                        "title": "Old cache choice",
                        "status": "superseded",
                        "source": "pr",
                        "relation": "supersedes",
                    },
                    {
                        "id": "new",
                        "title": "Replacement cache choice",
                        "status": "active",
                        "source": "pr",
                        "relation": None,
                    },
                ],
            },
        ],
        "code_rationale": [
            {"path": target, "lines": [20, 22], "comment": "exact supporting range"},
            {"path": target, "lines": [30, 32], "comment": "different range"},
        ],
        "git_archaeology": {
            "triggered": True,
            "file_commits": [{"sha": _C, "message": "independent history"}],
            "cross_references": [],
            "git_log": [],
        },
        "episodes": [
            {
                "tier": "git",
                "kind": "code_fix",
                "subject": _A,
                "recorded": "same commit as the governing decision",
                "evidence": "commit a",
                "scope": [target],
            }
        ],
    }
    return records, payload


def test_provenance_vocabulary_is_explicit_and_legacy_safe() -> None:
    assert provenance_for_source("session") == "human_decision"
    assert provenance_for_source("inline_marker") == "extracted_rationale"
    assert provenance_for_source("git_archaeology") == "historical"
    assert provenance_for_source("inferred") == "inferred"
    assert provenance_for_source("future_extractor") == "unknown"


def test_sealed_fixture_shares_identity_without_merging_decisions(
    sealed_evidence_fixture,
) -> None:
    records, payload = sealed_evidence_fixture
    result = annotate_response_evidence(payload, "Repo-A", records)

    decisions = {row["id"]: row for row in result["decisions"]}
    governing_refs = decisions["governing"]["evidence_refs"]
    assert len(governing_refs) == 2
    assert all(ref["id"] != decisions["governing"]["id"] for ref in governing_refs)
    assert decisions["governing"]["provenance"] == "human_decision"
    assert decisions["governing"]["source"] == "session"
    assert decisions["governing"]["restates"] == ["reextract"]

    episode_ref = result["episodes"][0]["evidence_refs"][0]
    assert episode_ref["id"] in {ref["id"] for ref in governing_refs}
    assert episode_ref["provenance"] == "historical"
    assert result["episodes"][0]["provenance"] == "historical"

    similar_one = decisions["similar-one"]["evidence_refs"][0]
    similar_two = decisions["similar-two"]["evidence_refs"][0]
    assert similar_one["id"] != similar_two["id"]
    assert {"similar-one", "similar-two"} <= decisions.keys()

    lineage = decisions["new"]["lineage"]
    assert [(row["id"], row["status"]) for row in lineage] == [
        ("old", "superseded"),
        ("new", "active"),
    ]
    assert lineage[0]["relation_provenance"] == "inferred"


def test_exact_source_content_joins_and_different_source_ranges_do_not(
    sealed_evidence_fixture,
) -> None:
    records, payload = sealed_evidence_fixture
    result = annotate_response_evidence(payload, "Repo-A", records)
    range_record = next(record for record in records if record.id == "range")
    range_result = annotate_response_evidence(
        {"decisions": [{"id": "range", "source": "inline_marker"}]},
        "Repo-A",
        [range_record],
    )
    decision_ref = range_result["decisions"][0]["evidence_refs"][0]
    exact, different = result["code_rationale"]

    assert exact["provenance"] == "extracted_rationale"
    assert exact["evidence_refs"][0]["id"] == decision_ref["id"]
    assert exact["evidence_refs"][0]["range"] == [20, 22]
    assert decision_ref["range"] == [20, 20]
    assert different["evidence_refs"][0]["id"] != decision_ref["id"]


def test_restatement_key_uses_all_commits_and_rejects_incomplete_ranges(
    sealed_evidence_fixture,
) -> None:
    records, _ = sealed_evidence_fixture
    by_id = {record.id: record for record in records}

    assert decision_collapse_key(by_id["governing"]) == decision_collapse_key(
        by_id["reextract"]
    )
    assert decision_collapse_key(by_id["similar-one"]) != decision_collapse_key(
        by_id["similar-two"]
    )
    assert decision_collapse_key(by_id["legacy"]) is None
    assert decision_collapse_key(by_id["old"]) is None


def test_commit_references_are_deduplicated_and_ordered() -> None:
    record = _record(
        "multi", title="Multiple commits", commits=[_B, _A, _B], status="active"
    )
    reverse = _record(
        "multi", title="Multiple commits", commits=[_A, _B], status="active"
    )

    first = annotate_response_evidence({"decisions": [{"id": "multi"}]}, "Repo-A", [record])
    second = annotate_response_evidence(
        {"decisions": [{"id": "multi"}]}, "Repo-A", [reverse]
    )

    assert first["decisions"][0]["evidence_refs"] == second["decisions"][0][
        "evidence_refs"
    ]
    assert len(first["decisions"][0]["evidence_refs"]) == 2


def test_shared_evidence_does_not_collapse_a_superseded_decision() -> None:
    old = _record(
        "old", title="Old choice", commits=[_A], status="superseded"
    )
    new = _record("new", title="New choice", commits=[_A], status="active")

    assert decision_collapse_key(old) is None
    assert decision_collapse_key(new) is not None


def test_incomplete_coordinates_remain_conservative() -> None:
    first = _record("legacy-one", title="Same words", evidence_file="src/cache.py")
    second = _record("legacy-two", title="Same words", evidence_file="src/cache.py")
    payload = {"decisions": [{"id": first.id}, {"id": second.id}]}

    result = annotate_response_evidence(payload, "Repo-A", [first, second])
    refs = [row["evidence_refs"][0] for row in result["decisions"]]
    assert refs[0]["kind"] == refs[1]["kind"] == "legacy"
    assert refs[0]["id"] != refs[1]["id"]


def test_unknown_source_is_preserved_without_claiming_stronger_provenance() -> None:
    record = _record("future", title="Future source", source="future_extractor")
    payload = {"decisions": [{"id": record.id, "source": record.source}]}

    result = annotate_response_evidence(payload, "Repo-A", [record])
    assert result["decisions"][0]["source"] == "future_extractor"
    assert result["decisions"][0]["provenance"] == "unknown"


def test_accreted_evidence_preserves_each_source_role() -> None:
    record = _record("accreted", title="Accreted", source="session")
    record._why_evidence_rows = [
        SimpleNamespace(
            id="human-evidence",
            source="session",
            evidence_commit=None,
            evidence_file="docs/decision.md",
            evidence_line=8,
            source_quote="Choose bounded caching",
            verification="exact",
        ),
        SimpleNamespace(
            id="comment-evidence",
            source="inline_marker",
            evidence_commit=None,
            evidence_file="src/cache.py",
            evidence_line=20,
            source_quote="exact supporting range",
            verification="exact",
        ),
        SimpleNamespace(
            id="fuzzy-evidence",
            source="inline_marker",
            evidence_commit=None,
            evidence_file="src/cache.py",
            evidence_line=44,
            source_quote="approximately the same rationale",
            verification="fuzzy",
        ),
    ]

    result = annotate_response_evidence(
        {"decisions": [{"id": record.id, "source": record.source}]},
        "Repo-A",
        [record],
    )
    decision = result["decisions"][0]

    assert decision["provenance"] == "human_decision"
    assert {ref["provenance"] for ref in decision["evidence_refs"]} == {
        "human_decision",
        "extracted_rationale",
    }
    assert {ref["source"] for ref in decision["evidence_refs"]} == {
        "session",
        "inline_marker",
    }
    fuzzy = next(ref for ref in decision["evidence_refs"] if ref.get("verification") == "fuzzy")
    assert fuzzy["kind"] == "legacy"
    assert "line" not in fuzzy and "range" not in fuzzy


def test_orphan_semantic_decision_does_not_invent_evidence_identity() -> None:
    result = annotate_response_evidence(
        {"decisions": [{"id": "semantic-only", "snippet": "nearest decision"}]},
        "Repo-A",
    )

    assert result["decisions"] == [
        {
            "id": "semantic-only",
            "snippet": "nearest decision",
            "provenance": "inferred",
        }
    ]


@pytest.mark.asyncio
async def test_persisted_evidence_rows_accrete_into_the_public_decision(
    setup_mcp, factory
) -> None:
    from repowise.server.mcp_server import get_why

    async with factory() as session:
        session.add_all(
            [
                DecisionEvidence(
                    id="evidence-human",
                    decision_id="dec1",
                    source="session",
                    source_rank=5,
                    evidence_file="docs/auth.md",
                    evidence_line=4,
                    source_quote="Use JWT for all API authentication",
                    confidence=1.0,
                    verification="exact",
                ),
                DecisionEvidence(
                    id="evidence-rationale",
                    decision_id="dec1",
                    source="inline_marker",
                    source_rank=2,
                    evidence_file="src/auth/service.py",
                    evidence_line=12,
                    source_quote="JWT keeps authentication stateless",
                    confidence=0.9,
                    verification="exact",
                ),
            ]
        )
        await session.commit()

    result = await get_why("why is JWT used for authentication")
    path_result = await get_why("src/auth/service.py")
    decision = _row_by_id(result, "dec1")
    path_decision = _row_by_id(path_result, "dec1")

    assert {ref["provenance"] for ref in decision["evidence_refs"]} == {
        "human_decision",
        "extracted_rationale",
    }
    assert {ref["source"] for ref in decision["evidence_refs"]} == {
        "session",
        "inline_marker",
    }
    assert path_decision["evidence_refs"] == decision["evidence_refs"]


def test_evidence_ids_are_order_independent_and_repository_scoped(
    sealed_evidence_fixture,
) -> None:
    records, payload = sealed_evidence_fixture
    forward = annotate_response_evidence(deepcopy(payload), "Repo-A", records)
    reverse = annotate_response_evidence(deepcopy(payload), "Repo-A", list(reversed(records)))
    other_repo = annotate_response_evidence(deepcopy(payload), "Repo-B", records)
    with_freshness = deepcopy(payload)
    with_freshness["_meta"] = {"indexed_commit": _A, "live_head": _B}
    with_freshness = annotate_response_evidence(with_freshness, "Repo-A", records)

    def ids(result: dict) -> dict[str, list[str]]:
        return {
            row["id"]: [ref["id"] for ref in row["evidence_refs"]]
            for row in result["decisions"]
        }

    assert ids(forward) == ids(reverse)
    assert ids(forward) == ids(with_freshness)
    assert ids(forward)["governing"] != ids(other_repo)["governing"]
    assert forward["episodes"][0]["evidence_refs"][0]["repository"] == "repo-a"
    assert other_repo["episodes"][0]["evidence_refs"][0]["repository"] == "repo-b"


def test_target_governing_record_ranks_before_unrelated_standing_decision(
    sealed_evidence_fixture,
) -> None:
    records, _ = sealed_evidence_fixture
    by_id = {record.id: record for record in records}
    ranked = _rank_keyword_matches(
        [by_id["standing"], by_id["governing"]],
        "why is the cache guidance bounded",
        {"src/cache.py"},
    )

    assert ranked[0].id == "governing"


@pytest.mark.asyncio
async def test_every_single_repository_query_mode_keeps_the_exact_public_reference(
    setup_mcp,
) -> None:
    from repowise.server.mcp_server import get_why

    expected = {
        "id": "ev_3d722ae232bb20add04b",
        "repository": "default",
        "kind": "legacy",
        "content_id": "8962562181b9a6600ce9",
        "provenance": "historical",
        "source": "readme_mining",
        "source_kind": "readme_mining",
        "verification_basis": "indexed",
    }
    calls = [
        await get_why("why is JWT used for authentication"),
        await get_why(
            "authentication approach",
            targets=["src/auth/service.py"],
        ),
        await get_why("src/auth/service.py"),
        await get_why(targets=["src/auth/service.py"]),
    ]
    for result in calls:
        decision = _row_by_id(result, "dec1")
        assert decision["source"] == "readme_mining"
        assert decision["provenance"] == "historical"
        assert decision["evidence_refs"] == [expected]
        assert list(decision)[-2:] == ["provenance", "evidence_refs"]

    multi_target = await get_why(
        targets=["src/auth/service.py", "src/auth/middleware.py"]
    )
    target_decision = multi_target["target_context"]["src/auth/service.py"][
        "governing_decisions"
    ][0]
    assert target_decision == {
        "id": "dec1",
        "title": "Use JWT for authentication",
        "status": "proposed",
        "source": "readme_mining",
        "provenance": "historical",
        "evidence_refs": [expected],
    }

    dashboard = await get_why()
    proposed = next(row for row in dashboard["proposed_awaiting_review"] if row["id"] == "dec1")
    assert proposed["source"] == "readme_mining"
    assert proposed["provenance"] == "historical"
    assert proposed["evidence_refs"] == [expected]


@pytest.mark.asyncio
async def test_every_single_repository_mode_is_exactly_accounted_after_middleware(
    setup_mcp,
) -> None:
    from repowise.server.mcp_server import get_why, tool_middleware

    call = tool_middleware(get_why)
    results = [
        await call("why is JWT used for authentication"),
        await call("src/auth/service.py"),
        await call(targets=["src/auth/service.py", "src/auth/middleware.py"]),
        await call(),
    ]

    for result in results:
        accounting = result["_meta"]["response_budget"]
        assert accounting["serialized_chars"] == len(
            json.dumps(result, separators=(",", ":"), default=str)
        )
        assert accounting["serialized_chars"] <= accounting["limit_chars"]


@pytest.mark.asyncio
async def test_no_decision_and_unsupported_modes_remain_compatible(setup_mcp) -> None:
    from repowise.server.mcp_server import get_why

    fallback = await get_why("src/other/utils.py")
    assert list(fallback)[:5] == [
        "mode",
        "path",
        "decisions",
        "origin_story",
        "alignment",
    ]
    assert fallback["decisions"] == []
    assert fallback["git_archaeology"]["provenance"] == "historical"

    unsupported = await get_why(repo="all")
    assert unsupported == {
        "error": (
            "repo='all' is not supported for get_why (health dashboard). "
            "Specify a repo alias instead. Available: []"
        )
    }
