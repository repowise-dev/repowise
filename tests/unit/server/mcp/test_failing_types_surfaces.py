"""Default MCP output for the finding types the registry hides or demotes.

Hidden types (``dry_violation``, ``unused_internal``) never appear; a test file never heads
the worst or leverage lists but is ranked in ``test_worst_files``; perf text
says "N+1" only with database evidence.
"""

from __future__ import annotations

import pytest

from repowise.core.persistence.models import HealthFileMetric


@pytest.fixture
async def failing_types_data(session, health_data):
    from repowise.core.persistence.crud import save_health_findings

    rid = health_data
    session.add(
        HealthFileMetric(
            repository_id=rid,
            file_path="tests/test_service.py",
            score=1.0,
            max_ccn=30,
            max_nesting=6,
            nloc=400,
            has_test_file=False,
            module="tests",
            is_test=True,
        )
    )
    await save_health_findings(
        session,
        rid,
        [
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "dry_violation",
                "severity": "high",
                "function_name": None,
                "line_start": 1,
                "line_end": 30,
                "details": {"duplication_pct": 40.0},
                "health_impact": 9.0,
                "reason": "40% of file duplicated",
            },
            {
                "file_path": "src/auth/service.py",
                "biomarker_type": "io_in_loop",
                "severity": "medium",
                "function_name": "notify",
                "line_start": 90,
                "line_end": 90,
                "details": {"boundary_kind": "network", "cross_function": False},
                "health_impact": 0.0,
                "reason": "a network call runs once per loop iteration (I/O call inside a loop)",
                "dimension": "performance",
            },
        ],
    )
    await session.commit()
    return rid


@pytest.mark.asyncio
async def test_hidden_type_absent_from_default_health_output(setup_mcp, failing_types_data):
    from repowise.server.mcp_server import get_health

    dashboard = await get_health(only=["top_findings", "test_findings"], limit=50)
    assert "dry_violation" not in {f["biomarker_type"] for f in dashboard["top_findings"]}
    targeted = await get_health(targets=["src/auth/service.py"], include=["unverified"])
    assert "dry_violation" not in {f["biomarker_type"] for f in targeted["findings"]}


@pytest.mark.asyncio
async def test_unused_internal_absent_from_default_dead_code_output(setup_mcp, session, populated_db):
    from repowise.core.persistence.models import DeadCodeFinding
    from repowise.server.mcp_server import get_dead_code

    session.add(
        DeadCodeFinding(
            id="dc_internal",
            repository_id=populated_db,
            kind="unused_internal",
            file_path="src/auth/service.py",
            symbol_name="_helper",
            symbol_kind="function",
            confidence=0.65,
            reason="Private symbol with no callers",
            lines=5,
            safe_to_delete=False,
            status="open",
        )
    )
    await session.commit()

    result = await get_dead_code(min_confidence=0.0)
    kinds = [f["kind"] for tier in result["tiers"].values() for f in tier["findings"]]
    assert "unused_internal" not in kinds
    assert result["summary"]["withheld_types"]["unused_internal"]["count"] == 1
    # Naming the kind does not bring it back.
    named = await get_dead_code(kind="unused_internal", min_confidence=0.0)
    assert [f for tier in named["tiers"].values() for f in tier["findings"]] == []


@pytest.mark.asyncio
async def test_test_files_ranked_apart_from_production(setup_mcp, failing_types_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(
        only=["worst_files", "test_worst_files", "high_leverage_files", "kpis"], limit=50
    )
    worst = [r["file_path"] for r in result["worst_files"]]
    assert worst and "tests/test_service.py" not in worst
    assert "tests/test_service.py" not in [r["file_path"] for r in result["high_leverage_files"]]
    assert [r["file_path"] for r in result["test_worst_files"]] == ["tests/test_service.py"]
    assert result["kpis"]["worst_performer_path"] == "src/auth/service.py"
    assert result["kpis"]["worst_test_path"] == "tests/test_service.py"


@pytest.mark.asyncio
async def test_perf_text_has_no_n_plus_one_without_db(setup_mcp, failing_types_data):
    from repowise.server.mcp_server import get_health

    result = await get_health(include=["performance"], only=["top_findings"], limit=50)
    perf = [f for f in result["top_findings"] if f["dimension"] == "performance"]
    assert perf
    for f in perf:
        if f["details"].get("boundary_kind") != "db":
            assert "N+1" not in f["reason"]
