"""Sealed final-wire fixtures for recoverable MCP response caps."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from repowise.core.persistence.models import (
    DecisionEdge,
    DecisionRecord,
    GitMetadata,
    GraphEdge,
    GraphNode,
)
from repowise.server.mcp_server import (
    get_context,
    get_risk,
    get_symbol,
    get_why,
    tool_middleware,
)
from repowise.server.mcp_server._budget import (
    DEFAULT_RESPONSE_CHARS,
    EXPANDED_RESPONSE_CHARS,
)

_NOW = datetime(2026, 8, 26, tzinfo=UTC)


def _configure_omissions(tmp_path: Path) -> None:
    import repowise.server.mcp_server as mcp_mod

    (tmp_path / ".repowise").mkdir(exist_ok=True)
    mcp_mod._repo_path = str(tmp_path)


def _wire_size(result: dict[str, Any]) -> int:
    return len(json.dumps(result, separators=(",", ":"), default=str))


def _assert_wire(result: dict[str, Any], first: str, limit: int) -> None:
    assert next(iter(result)) == first
    accounting = result["_meta"]["response_budget"]
    assert accounting["serialized_chars"] == _wire_size(result) <= limit
    assert accounting["limit_chars"] == limit


async def _recover(result: dict[str, Any]) -> str:
    recovered: list[str] = []
    for ref in result.get("_meta", {}).get("omitted", {}).get("refs", []):
        row = await get_symbol(ref)
        assert "error" not in row, row
        recovered.append(row["content"])
    return "\n".join(recovered)


async def _recover_one(result: dict[str, Any], *sentinels: str) -> str:
    """Prove each named omitted row is contained by one public recovery call."""
    refs = result.get("_meta", {}).get("omitted", {}).get("refs", [])
    assert refs
    contents: list[str] = []
    for ref in refs:
        row = await get_symbol(ref)
        assert "error" not in row, row
        contents.append(row["content"])
    for sentinel in sentinels:
        assert any(sentinel in content for content in contents), sentinel
    return "\n".join(contents)


def _omission_section(recovered: str, label: str) -> str:
    marker = f"==== {label} ===="
    assert marker in recovered, label
    return recovered.split(marker, 1)[1].split("==== ", 1)[0].strip()


async def _seed_context_fanout(session: Any, rid: str, count: int) -> str:
    target = "src/sealed/hub.py::hub"
    session.add(
        GraphNode(
            id="sealed-hub",
            repository_id=rid,
            node_id=target,
            node_type="symbol",
            name="hub",
            file_path="src/sealed/hub.py",
            kind="function",
            start_line=1,
            end_line=2,
            created_at=_NOW,
        )
    )
    for index in range(count):
        caller = f"src/sealed/caller_{index:03d}.py::call_{index:03d}"
        callee = f"src/sealed/callee_{index:03d}.py::work_{index:03d}"
        session.add_all(
            [
                GraphNode(
                    id=f"sealed-caller-{index}",
                    repository_id=rid,
                    node_id=caller,
                    node_type="symbol",
                    name=f"call_{index:03d}",
                    file_path=caller.split("::", 1)[0],
                    kind="function",
                    start_line=index + 1,
                    end_line=index + 2,
                    created_at=_NOW,
                ),
                GraphNode(
                    id=f"sealed-callee-{index}",
                    repository_id=rid,
                    node_id=callee,
                    node_type="symbol",
                    name=f"work_{index:03d}",
                    file_path=callee.split("::", 1)[0],
                    kind="function",
                    start_line=index + 1,
                    end_line=index + 2,
                    created_at=_NOW,
                ),
                GraphEdge(
                    id=f"sealed-in-{index}",
                    repository_id=rid,
                    source_node_id=caller,
                    target_node_id=target,
                    edge_type="calls",
                    confidence=0.99,
                    created_at=_NOW,
                ),
                GraphEdge(
                    id=f"sealed-out-{index}",
                    repository_id=rid,
                    source_node_id=target,
                    target_node_id=callee,
                    edge_type="calls",
                    confidence=0.99,
                    created_at=_NOW,
                ),
            ]
        )
    await session.flush()
    return target


@pytest.mark.asyncio
async def test_context_real_minimum_and_typical_wire_shapes(
    setup_mcp: str, session: Any, tmp_path: Path
) -> None:
    _configure_omissions(tmp_path)
    wrapped = tool_middleware(get_context)

    minimum = await wrapped(["src/auth/service.py"])
    _assert_wire(minimum, "targets", DEFAULT_RESPONSE_CHARS)
    assert "truncated" not in minimum

    target = await _seed_context_fanout(session, setup_mcp, 3)
    typical = await wrapped([target], include=["callers", "callees"])
    _assert_wire(typical, "targets", EXPANDED_RESPONSE_CHARS)
    card = typical["targets"][target]
    assert len(card["callers"]) == len(card["callees"]) == 3
    assert "callers_total" not in card and "callees_total" not in card


@pytest.mark.asyncio
async def test_context_real_adversarial_wire_recovers_independent_tails(
    setup_mcp: str, session: Any, tmp_path: Path
) -> None:
    _configure_omissions(tmp_path)
    target = await _seed_context_fanout(session, setup_mcp, 63)
    result = await tool_middleware(get_context)(
        [target], include=["callers", "callees"]
    )

    _assert_wire(result, "targets", EXPANDED_RESPONSE_CHARS)
    card = result["targets"][target]
    for key in ("callers", "callees"):
        assert card[f"{key}_total"] == 63
        assert card[f"{key}_emitted"] == 50
        assert card[f"{key}_reduced_reason"] == "construction_cap"
    recovered = await _recover_one(result, "caller_062", "callee_062")
    assert "caller_062" in recovered and "callee_062" in recovered
    assert "caller_000" not in recovered and "callee_000" not in recovered


@pytest.mark.asyncio
async def test_context_used_by_and_relations_recover_in_one_bounded_query_shape(
    setup_mcp: str, session: Any, tmp_path: Path
) -> None:
    from sqlalchemy import event

    _configure_omissions(tmp_path)
    target = await _seed_context_fanout(session, setup_mcp, 1)
    for index in range(26):
        file_id = f"src/sealed/user_{index:03d}.py"
        relation_id = f"src/sealed/type_{index:03d}.py::Type{index:03d}"
        session.add_all(
            [
                GraphNode(
                    id=f"sealed-user-{index}",
                    repository_id=setup_mcp,
                    node_id=file_id,
                    node_type="file",
                    file_path=file_id,
                    pagerank=index / 100,
                    created_at=_NOW,
                ),
                GraphNode(
                    id=f"sealed-type-{index}",
                    repository_id=setup_mcp,
                    node_id=relation_id,
                    node_type="symbol",
                    name=f"Type{index:03d}",
                    file_path=relation_id.split("::", 1)[0],
                    kind="class",
                    created_at=_NOW,
                ),
                GraphEdge(
                    id=f"sealed-import-{index}",
                    repository_id=setup_mcp,
                    source_node_id=file_id,
                    target_node_id="src/auth/service.py",
                    edge_type="imports",
                    imported_names_json='["AuthService"]',
                    created_at=_NOW,
                ),
                GraphEdge(
                    id=f"sealed-extends-{index}",
                    repository_id=setup_mcp,
                    source_node_id=relation_id,
                    target_node_id=target,
                    edge_type="extends",
                    confidence=0.95,
                    created_at=_NOW,
                ),
            ]
        )
    await session.flush()

    statements = 0

    def count_statement(*_args: Any, **_kwargs: Any) -> None:
        nonlocal statements
        statements += 1

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", count_statement)
    try:
        relation_result = await tool_middleware(get_context)(
            [target], include=["callers"]
        )
        relation_statements = statements
        statements = 0
        used_by_result = await tool_middleware(get_context)(["AuthService"])
        used_by_statements = statements
    finally:
        event.remove(engine, "before_cursor_execute", count_statement)

    relation = next(
        row
        for row in relation_result["targets"][target]["relations"]
        if row["edge_type"] == "extends"
    )
    assert relation["rows_total"] == 26 and relation["rows_emitted"] == 5
    docs = used_by_result["targets"]["AuthService"]["docs"]
    assert docs["used_by_total"] >= 26 and docs["used_by_emitted"] == 20
    recovered = await _recover_one(used_by_result, "src/sealed/user_000.py")
    assert "src/sealed/user_000.py" in recovered
    assert "src/sealed/user_025.py" not in recovered
    relation_recovered = await _recover_one(relation_result, "Type025")
    assert "Type025" in relation_recovered
    assert "Type000" not in relation_recovered
    assert relation_statements <= 20 and used_by_statements <= 20


@pytest.mark.asyncio
async def test_risk_real_minimum_and_typical_wire_shapes(
    setup_mcp: str, tmp_path: Path
) -> None:
    _configure_omissions(tmp_path)
    wrapped = tool_middleware(get_risk)

    minimum = await wrapped(["src/auth/service.py"])
    _assert_wire(minimum, "targets", DEFAULT_RESPONSE_CHARS)

    typical = await wrapped(
        ["src/auth/service.py"], changed_files=["src/auth/service.py"]
    )
    _assert_wire(typical, "directive", DEFAULT_RESPONSE_CHARS)
    assert next(iter(typical["directive"])) == "may_break"
    for key in ("breaking_changes", "conformance_violations", "dependency_cycles"):
        assert f"{key}_total" not in typical["directive"]


@pytest.mark.asyncio
async def test_risk_multi_target_graph_cards_recover_relationship_tails(
    setup_mcp: str, session: Any, tmp_path: Path
) -> None:
    _configure_omissions(tmp_path)
    for index in range(14):
        file_id = f"src/sealed/risk_consumer_{index:03d}.py"
        session.add_all(
            [
                GraphNode(
                    id=f"sealed-risk-file-{index}",
                    repository_id=setup_mcp,
                    node_id=file_id,
                    node_type="file",
                    file_path=file_id,
                    created_at=_NOW,
                ),
                GraphEdge(
                    id=f"sealed-risk-import-{index}",
                    repository_id=setup_mcp,
                    source_node_id=file_id,
                    target_node_id="src/auth/service.py",
                    edge_type="imports",
                    created_at=_NOW,
                ),
            ]
        )
    await session.flush()

    result = await tool_middleware(get_risk)(
        ["src/auth/service.py", "src/auth/middleware.py"], include=["graph"]
    )
    _assert_wire(result, "targets", EXPANDED_RESPONSE_CHARS)
    card = result["targets"]["src/auth/service.py"]
    assert card["dependents_total"] >= 14
    assert card["dependents_emitted"] < card["dependents_total"]
    recovered = await _recover_one(result, "risk_consumer_013.py")
    assert "risk_consumer_000.py" not in recovered


@pytest.mark.asyncio
async def test_risk_real_adversarial_wire_recovers_each_directive_lane(
    setup_mcp: str, session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from repowise.core.analysis.pr_blast import PRBlastRadiusAnalyzer

    _configure_omissions(tmp_path)
    for index in range(8):
        session.add(
            GraphNode(
                id=f"sealed-test-{index}",
                repository_id=setup_mcp,
                node_id=f"tests/sealed_{index}.py",
                node_type="file",
                is_test=True,
                created_at=_NOW,
            )
        )
    await session.flush()

    original = PRBlastRadiusAnalyzer.analyze_files

    async def sealed_analyze(self: Any, changed_files: list[str], **kwargs: Any) -> dict:
        payload = await original(self, changed_files, **kwargs)
        payload["transitive_affected"] = [
            *[f"src/downstream_{i}.py" for i in range(9)],
            *[f"tests/sealed_{i}.py" for i in range(8)],
        ]
        payload["cochange_warnings"] = [f"src/cochange_{i}.py" for i in range(8)]
        payload["guarding_tests"] = {
            "tests_to_run": [f"tests/GUARD_{i}.py::test_it" for i in range(14)],
            "basis": "measured",
        }
        recommendations = [
            {
                "test_id": f"tests/REC_{i}.py::test_it",
                "basis": "measured" if i % 2 == 0 else "inferred",
                "basis_detail": f"REC_DETAIL_{i}_" + "d" * 2000,
            }
            for i in range(15)
        ]
        payload["test_impact"] = {
            "recommendations": recommendations,
            "recommendations_by_primary_basis": {"measured": 8, "inferred": 7},
            "coverage": {"status": "available", "freshness": {"status": "fresh"}},
            "inference": {"status": "available"},
            "analysis": {"status": "available"},
            "files_without_measured_tests": [
                f"src/NO_MEASURED_{i}.py" for i in range(12)
            ],
            "unknown_files": [f"src/UNKNOWN_{i}.py" for i in range(12)],
        }
        payload["test_gaps"] = list(changed_files)
        payload["sealed_support_noise"] = "FINAL_BUDGET_SENTINEL_" + "z" * 30_000
        return payload

    monkeypatch.setattr(PRBlastRadiusAnalyzer, "analyze_files", sealed_analyze)
    changed = [f"src/gap_{i}.py" for i in range(6)]
    result = await tool_middleware(get_risk)(
        ["src/auth/service.py"], changed_files=changed
    )

    _assert_wire(result, "directive", DEFAULT_RESPONSE_CHARS)
    directive = result["directive"]
    expected = {
        "may_break": 9,
        "may_break_tests": 8,
        "missing_cochanges": 8,
        "missing_tests": 6,
        "tests_to_run": 14,
        "test_recommendations": 15,
        "files_without_measured_tests": 12,
        "test_unknown_files": 12,
    }
    for key, total in expected.items():
        assert directive[f"{key}_total"] == total
        assert directive[f"{key}_emitted"] < total
        assert directive[f"{key}_reduced_reason"].startswith("construction_cap")
    assert result["truncated"] is True
    assert "pr_blast_radius" not in result
    assert directive["test_recommendations_reduced_reason"] == (
        "construction_cap_and_response_budget"
    )
    assert directive["test_recommendations_omitted"] == (
        directive["test_recommendations_total"]
        - directive["test_recommendations_emitted"]
    )
    recovered = await _recover_one(
        result,
        "downstream_8",
        "sealed_7",
        "cochange_7",
        "REC_14",
        "GUARD_13",
        "src/gap_5.py",
        "FINAL_BUDGET_SENTINEL",
        "NO_MEASURED_11",
        "UNKNOWN_11",
    )
    assert "downstream_8" in recovered
    assert "sealed_7" in recovered
    assert "cochange_7" in recovered
    assert "REC_14" in recovered and '"basis": "measured"' in recovered
    rec_14_row = recovered.split('"test_id": "tests/REC_14.py::test_it"', 1)[1][
        :5000
    ]
    assert '"basis": "measured"' in rec_14_row
    assert "REC_DETAIL_14" in rec_14_row
    assert "GUARD_13" in recovered
    assert "src/gap_5.py" in recovered
    assert "FINAL_BUDGET_SENTINEL" in recovered


@pytest.mark.asyncio
async def test_risk_breaking_and_conformance_caps_are_counted_and_recoverable(
    setup_mcp: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server.tool_risk.directives as directives_mod
    from repowise.server.mcp_server import _state

    _configure_omissions(tmp_path)
    changes = [
        {
            "contract_id": f"contract-{index}",
            "contract_type": "code",
            "kind": "removed_endpoint",
            "severity": "breaking",
            "detail": f"BREAKING_SENTINEL_{index}",
            "provider_file": f"src/provider_{index}.py",
            "impacted_consumers": [
                {
                    "repo": "consumer",
                    "service": "api",
                    "file": f"src/consumer_{index}_{consumer}.py",
                }
                for consumer in range(7)
            ],
        }
        for index in range(7)
    ]
    violations = [
        {
            "source": "repowise",
            "target": f"forbidden-{index}",
            "rule_source": "repowise",
            "rule_target": f"forbidden-{index}",
            "edge_kind": "imports",
            "rule_description": f"CONFORMANCE_SENTINEL_{index}",
        }
        for index in range(7)
    ]
    cycles = [
        {"nodes": ["repowise", f"cycle-{index}"], "length": 2} for index in range(5)
    ]
    enricher = SimpleNamespace(
        has_breaking_changes=True,
        has_conformance=True,
        get_breaking_changes_for_repo=lambda alias: changes,
        get_conformance_for_repo=lambda alias: {
            "violations": violations,
            "cycles": cycles,
        },
        get_system_graph=lambda: None,
    )
    monkeypatch.setattr(_state, "_cross_repo_enricher", enricher)
    monkeypatch.setattr(directives_mod, "_is_workspace_mode", lambda: True)
    response = await tool_middleware(get_risk)(
        ["src/auth/service.py"], changed_files=["src/auth/service.py"]
    )
    _assert_wire(response, "directive", DEFAULT_RESPONSE_CHARS)

    directive = response["directive"]
    for key, total, emitted in (
        ("breaking_changes", 7, 5),
        ("conformance_violations", 7, 5),
        ("dependency_cycles", 5, 3),
    ):
        assert directive[f"{key}_total"] == total
        assert directive[f"{key}_emitted"] == emitted
        assert directive[f"{key}_reduced_reason"] == "construction_cap"
    assert directive["breaking_changes"][0]["impacted_consumers_total"] == 7
    assert directive["breaking_changes_truncated"] == 2
    recovered = await _recover_one(
        response,
        "BREAKING_SENTINEL_6",
        "CONFORMANCE_SENTINEL_6",
        "cycle-4",
        "src/consumer_0_6.py",
    )
    assert "BREAKING_SENTINEL_6" in recovered
    assert "CONFORMANCE_SENTINEL_6" in recovered
    assert "cycle-4" in recovered
    assert "src/consumer_0_6.py" in recovered


async def _seed_why_decisions(session: Any, rid: str, count: int) -> None:
    for index in range(count):
        session.add_all(
            [
                DecisionRecord(
                    id=f"sealed-why-ancestor-{index}",
                    repository_id=rid,
                    title=f"Legacy unrelated policy {index}",
                    status="superseded",
                    decision="Retain the former policy for lineage.",
                    affected_files_json="[]",
                    affected_modules_json="[]",
                    alternatives_json="[]",
                    consequences_json="[]",
                    tags_json="[]",
                    source="git_history",
                    created_at=_NOW,
                    updated_at=_NOW,
                ),
                DecisionRecord(
                id=f"sealed-why-{index}",
                repository_id=rid,
                title=f"Use sealed response contract {index}",
                status="active" if index == 0 else "proposed",
                context="sealed response context " + "x" * 1200,
                decision="Use sealed response contract for recovery.",
                rationale=f"WHY_SENTINEL_{index}_" + "r" * 800,
                affected_files_json=json.dumps(["src/auth/service.py"]),
                affected_modules_json="[]",
                alternatives_json="[]",
                consequences_json="[]",
                tags_json='["sealed", "response"]',
                source="session",
                evidence_commits_json=json.dumps([f"{index + 1:040x}"]),
                confidence=0.9 - index / 100,
                staleness_score=0.0,
                created_at=_NOW,
                updated_at=_NOW,
                ),
                DecisionEdge(
                    id=f"sealed-lineage-{index}",
                    repository_id=rid,
                    src_decision_id=f"sealed-why-{index}",
                    dst_decision_id=f"sealed-why-ancestor-{index}",
                    kind="supersedes",
                    confidence=1.0,
                    created_at=_NOW,
                ),
            ]
        )
    await session.flush()


@pytest.mark.asyncio
async def test_why_real_minimum_and_typical_wire_shapes(
    setup_mcp: str, tmp_path: Path
) -> None:
    _configure_omissions(tmp_path)
    wrapped = tool_middleware(get_why)

    minimum = await wrapped("where is the episode store")
    _assert_wire(minimum, "mode", DEFAULT_RESPONSE_CHARS)
    assert minimum["decisions"] == [] and "truncated" not in minimum

    typical = await wrapped("src/auth/service.py")
    _assert_wire(typical, "mode", DEFAULT_RESPONSE_CHARS)
    assert typical["decisions"]


@pytest.mark.asyncio
async def test_why_real_adversarial_wire_recovers_decisions_docs_and_episodes(
    setup_mcp: str, session: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import repowise.server.mcp_server.tool_why as why_mod

    _configure_omissions(tmp_path)
    await _seed_why_decisions(session, setup_mcp, 9)

    semantic_decisions: list[Any] = []
    semantic_docs = [
        SimpleNamespace(
            page_id=f"file_page:docs/sealed_{i}.md",
            title=f"Sealed documentation {i}",
            page_type="file_page",
            snippet=f"DOC_SENTINEL_{i}_" + "d" * 400,
            score=0.9 - i / 100,
        )
        for i in range(8)
    ]

    async def sealed_semantic(ctx: Any, query: str) -> tuple[list[Any], list[Any]]:
        return semantic_decisions, semantic_docs

    def sealed_episodes(*args: Any, **kwargs: Any) -> tuple[list[dict], list]:
        population: list[dict[str, Any]] = []
        pending: list[tuple[dict[str, Any], str, str]] = []
        for i in range(8):
            body = f"EPISODE_SENTINEL_{i}_START_" + "e" * 1100 + f"_END_{i}"
            entry = {
                "tier": "git",
                "kind": "sealed",
                "subject": f"Episode {i}",
                "recorded": body[:900],
                "evidence": {"commit": f"{i:040x}"},
                "scope": ["src/auth/service.py"],
            }
            population.append(entry)
            pending.append((entry, f"episode:sealed:{i}", body))
        full = kwargs.get("full_population")
        if isinstance(full, list):
            full.extend(population)
        return population[:3], pending

    monkeypatch.setattr(why_mod, "_semantic_lanes", sealed_semantic)
    monkeypatch.setattr(why_mod, "episode_evidence", sealed_episodes)

    result = await tool_middleware(get_why)(
        "why use sealed response contract",
        targets=["src/auth/service.py"],
    )

    _assert_wire(result, "mode", DEFAULT_RESPONSE_CHARS)
    assert result["decisions_total"] >= 9
    assert result["decisions_emitted"] <= 3
    assert "construction_cap" in result["decisions_reduced_reason"]
    assert result["related_documentation_total"] == 8
    assert result["episodes_total"] == 8
    context = result["target_context"]["src/auth/service.py"]
    assert context["governing_decisions_total"] >= 9

    recovered = await _recover_one(
        result,
        "WHY_SENTINEL_8",
        "DOC_SENTINEL_7",
        "EPISODE_SENTINEL_7_START_",
        "_END_7",
    )
    assert "WHY_SENTINEL_8" in recovered
    assert "DOC_SENTINEL_7" in recovered
    assert "EPISODE_SENTINEL_7" in recovered
    assert '"evidence_refs"' in recovered
    assert "sealed-why-ancestor" in recovered
    omitted_decisions = json.loads(
        _omission_section(recovered, "search decisions beyond cap=3")
    )
    why_8_row = next(row for row in omitted_decisions if row["id"] == "sealed-why-8")
    assert any(
        row["id"] == "sealed-why-ancestor-8" for row in why_8_row["lineage"]
    )
    assert why_8_row["evidence_refs"]
    assert "repowise#" in result["episodes"][0]["recorded"]
    hidden_body_refs = [
        ref
        for ref in result["_meta"]["omitted"]["refs"]
        if "_END_7" in (
            await get_symbol(ref)
        ).get("content", "")
    ]
    assert len(hidden_body_refs) == 1
    emitted_ids = {row["id"] for row in result["decisions"] if "id" in row}
    assert all(decision_id not in recovered for decision_id in emitted_ids)


@pytest.mark.asyncio
async def test_why_health_and_targets_only_modes_are_bounded_and_recoverable(
    setup_mcp: str,
    session: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy import select

    import repowise.core.persistence.crud as crud_mod

    _configure_omissions(tmp_path)
    await _seed_why_decisions(session, setup_mcp, 9)
    records = list(
        (
            await session.execute(
                select(DecisionRecord).where(
                    DecisionRecord.id.in_([f"sealed-why-{index}" for index in range(9)])
                )
            )
        )
        .scalars()
        .all()
    )
    records[0].affected_files_json = json.dumps(
        [f"src/HEALTH_AFFECTED_{index}.py" for index in range(8)]
    )

    async def sealed_health(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "summary": {"active": 1, "stale": len(records)},
            "stale_decisions": records,
            "proposed_awaiting_review": records,
            "ungoverned_hotspots": [
                {"file_path": f"src/HEALTH_HOTSPOT_{index}.py"}
                for index in range(12)
            ],
            "conflicts": [
                {"detail": f"HEALTH_CONFLICT_{index}"} for index in range(12)
            ],
        }

    monkeypatch.setattr(crud_mod, "get_decision_health_summary", sealed_health)
    wrapped = tool_middleware(get_why)

    health = await wrapped()
    _assert_wire(health, "mode", DEFAULT_RESPONSE_CHARS)
    assert health["stale_decisions_total"] == 9
    assert health["stale_decisions_emitted"] == 5
    assert health["proposed_awaiting_review_total"] == 9
    assert health["proposed_awaiting_review_emitted"] == 5
    assert health["ungoverned_hotspots_total"] == 12
    assert health["ungoverned_hotspots_emitted"] == 8
    assert health["conflicts_total"] == 12
    assert health["conflicts_emitted"] == 10
    assert health["stale_decisions"][0]["affected_files_total"] == 8
    health_recovered = await _recover_one(
        health,
        "sealed response contract 8",
        "HEALTH_HOTSPOT_11",
        "HEALTH_CONFLICT_11",
        "HEALTH_AFFECTED_7",
    )
    assert "sealed response contract 8" in health_recovered.lower()
    stale_tail = json.loads(
        _omission_section(
            health_recovered, "health stale_decisions beyond cap=5"
        )
    )
    proposed_tail = json.loads(
        _omission_section(
            health_recovered, "health proposed_awaiting_review beyond cap=5"
        )
    )
    assert any(row["id"] == "sealed-why-8" for row in stale_tail)
    assert any(row["id"] == "sealed-why-8" for row in proposed_tail)

    targets_only = await wrapped(
        targets=["src/auth/service.py", "src/auth/middleware.py"]
    )
    _assert_wire(targets_only, "mode", DEFAULT_RESPONSE_CHARS)
    context = targets_only["target_context"]["src/auth/service.py"]
    assert context["governing_decisions_total"] >= 9
    assert context["governing_decisions_emitted"] == 8
    recovered = await _recover_one(targets_only, "sealed-why-8")
    assert "sealed-why-8" in recovered
    assert '"evidence_refs"' in recovered


@pytest.mark.asyncio
async def test_why_fallback_archaeology_and_rationale_wire_recover_annotated_tails(
    setup_mcp: str,
    session: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import repowise.server.mcp_server.tool_why as why_mod

    _configure_omissions(tmp_path)
    commits = [
        {
            "sha": f"{index + 100:040x}",
            "message": f"ARCHAEOLOGY_SENTINEL_{index}",
            "author": "sealed",
            "date": f"2026-08-{index + 1:02d}",
        }
        for index in range(13)
    ]
    session.add(
        GitMetadata(
            id="sealed-fallback-git",
            repository_id=setup_mcp,
            file_path="src/sealed/fallback.py",
            significant_commits_json=json.dumps(commits),
        )
    )
    for index in range(13):
        session.add(
            GitMetadata(
                id=f"sealed-cross-git-{index}",
                repository_id=setup_mcp,
                file_path=f"src/sealed/cross_{index}.py",
                significant_commits_json=json.dumps(
                    [
                        {
                            "sha": f"{index + 200:040x}",
                            "message": (
                                f"fallback.py CROSS_REFERENCE_SENTINEL_{index}"
                            ),
                            "author": "sealed",
                            "date": f"2026-07-{index + 1:02d}",
                        }
                    ]
                ),
            )
        )
    from repowise.core.persistence.models import Repository

    repository = await session.get(Repository, setup_mcp)
    repository.local_path = str(Path.cwd())
    await session.flush()

    async def sealed_git_log(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
        return [
            {
                "sha": f"{index + 300:040x}",
                "message": f"GIT_LOG_SENTINEL_{index}",
                "author": "sealed",
                "date": f"2026-06-{index + 1:02d}",
                "source": "git_log",
            }
            for index in range(23)
        ]

    monkeypatch.setattr(why_mod, "_run_git_log", sealed_git_log)
    monkeypatch.setattr(
        why_mod,
        "_mine_rationale",
        lambda *_args, **_kwargs: [
            {
                "path": "src/sealed/fallback.py",
                "lines": f"{index + 1}-{index + 1}",
                "comment": f"RATIONALE_SENTINEL_{index}",
            }
            for index in range(8)
        ],
    )

    result = await tool_middleware(get_why)("src/sealed/fallback.py")
    _assert_wire(result, "mode", DEFAULT_RESPONSE_CHARS)
    assert result["git_archaeology"]["file_commits_total"] == 13
    assert result["git_archaeology"]["cross_references_total"] == 13
    assert result["git_archaeology"]["git_log_total"] == 23
    assert result["code_rationale_total"] == 8
    recovered = await _recover_one(
        result,
        "ARCHAEOLOGY_SENTINEL_12",
        "CROSS_REFERENCE_SENTINEL_0",
        "GIT_LOG_SENTINEL_22",
        "RATIONALE_SENTINEL_7",
    )
    assert "ARCHAEOLOGY_SENTINEL_12" in recovered
    assert "RATIONALE_SENTINEL_7" in recovered
    assert "CROSS_REFERENCE_SENTINEL_12" not in recovered
    rationale_row = recovered.split("RATIONALE_SENTINEL_7", 1)[1][:2000]
    assert '"provenance": "extracted_rationale"' in rationale_row
    assert '"evidence_refs"' in rationale_row
