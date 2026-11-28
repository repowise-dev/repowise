"""History markers on production files are context, not defect-list findings.

A git-derived marker (``ownership_risk``, ``change_entropy`` ...) orders
hotspots and Fix first; finding lists and their counts hold it back under the
``history`` file hold, with its reason, and ``include=["unverified"]`` still
lists it. Scores, KPIs and the worst-file order still count it.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import select

from repowise.core.analysis.finding_registry import FILE_HOLDS, split_gated

_APP = "src/app_service.py"


async def _seed(session, rid: str) -> None:
    from repowise.core.persistence.crud import upsert_health_findings, upsert_health_metrics

    await upsert_health_metrics(
        session,
        rid,
        [
            {
                "file_path": _APP,
                "score": 4.0,
                "max_ccn": 25,
                "max_nesting": 5,
                "nloc": 300,
                "has_test_file": False,
                "module": "x",
                "code_origin": "production",
                "is_test": False,
            }
        ],
    )
    await upsert_health_findings(
        session,
        rid,
        [
            {
                "file_path": _APP,
                "biomarker_type": kind,
                "severity": "high",
                "function_name": "run",
                "line_start": 1,
                "line_end": 90,
                "details": {},
                "health_impact": impact,
                "reason": f"{kind} on {_APP}",
            }
            for kind, impact in (("ownership_risk", 1.5), ("change_entropy", 1.0),
                                 ("complex_method", 2.0))
        ],
        file_paths=[_APP],
    )
    await session.commit()


@pytest.mark.asyncio
async def test_sql_and_in_memory_holds_agree(session, health_data):
    from repowise.core.persistence.crud import (
        gated_health_counts,
        get_health_findings,
        get_health_metrics,
    )

    await _seed(session, health_data)
    every = await get_health_findings(session, health_data, include_unverified=True)
    metrics = {m.file_path: m for m in await get_health_metrics(session, health_data)}

    sql_kept = {f.id for f in await get_health_findings(session, health_data)}
    sql_held = await gated_health_counts(session, health_data)
    mem_kept, mem_held = split_gated("health", every, kind="biomarker_type", files=metrics)

    assert {f.id for f in mem_kept} == sql_kept
    assert sql_held == mem_held
    assert sql_held["history"] == {"count": 2, "reason": FILE_HOLDS["history"]}
    kinds = {(f.file_path, f.biomarker_type) for f in every if f.id in sql_kept}
    assert (_APP, "complex_method") in kinds
    assert not {(_APP, "ownership_risk"), (_APP, "change_entropy")} & kinds
    # The opt-in lists them.
    assert {(_APP, "ownership_risk"), (_APP, "change_entropy")} <= {
        (f.file_path, f.biomarker_type) for f in every
    }


def test_test_files_keep_their_own_key():
    rows = [
        {"file_path": "tests/test_a.py", "biomarker_type": "ownership_risk"},
        {"file_path": "src/a.py", "biomarker_type": "ownership_risk"},
    ]
    files = {
        "tests/test_a.py": SimpleNamespace(code_origin="test", is_test=True),
        "src/a.py": SimpleNamespace(code_origin="production", is_test=False),
    }
    shown, gated = split_gated(
        "health", rows, kind="biomarker_type", get=lambda r, k: r[k], files=files
    )
    assert shown == []
    assert {k: v["count"] for k, v in gated.items()} == {"test_history": 1, "history": 1}


@pytest.mark.asyncio
async def test_a_history_view_keeps_them(session, health_data):
    from repowise.core.persistence.crud.analysis.shown import ungated
    from repowise.core.persistence.models import HealthFinding

    await _seed(session, health_data)

    async def kinds(**kw) -> set[str]:
        rows = await session.execute(
            select(HealthFinding.biomarker_type).where(
                HealthFinding.repository_id == health_data,
                HealthFinding.file_path == _APP,
                ungated("health", HealthFinding.file_path, HealthFinding.biomarker_type, **kw),
            )
        )
        return set(rows.scalars())

    assert await kinds() == {"complex_method"}
    assert await kinds(hold_history=False) == {"complex_method", "ownership_risk", "change_entropy"}


@pytest.mark.asyncio
async def test_get_health_lists_count_and_scores_hold(setup_mcp, session, health_data, monkeypatch):
    from repowise.core.analysis import finding_registry
    from repowise.core.persistence.crud.analysis import shown as shown_module
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    default = await get_health(only=["top_findings", "kpis", "worst_files"], limit=50)
    listed = {(f["file_path"], f["biomarker_type"]) for f in default["top_findings"]}
    assert (_APP, "complex_method") in listed
    assert not {(_APP, "ownership_risk"), (_APP, "change_entropy")} & listed
    assert default["_meta"]["gated"]["health"] == {"history": 2}

    opted = await get_health(only=["top_findings"], include=["unverified"], limit=50)
    assert (_APP, "ownership_risk") in {
        (f["file_path"], f["biomarker_type"]) for f in opted["top_findings"]
    }

    # Scores, KPIs and the worst-file order still count the held markers.
    monkeypatch.setattr(shown_module, "_file_holds", lambda *a, **k: [])
    monkeypatch.setattr(finding_registry, "row_hold", lambda *a, **k: None)
    plain = await get_health(only=["top_findings", "kpis", "worst_files"], limit=50)
    assert default["kpis"] == plain["kpis"]
    assert default["worst_files"] == plain["worst_files"]


@pytest.mark.asyncio
async def test_get_health_history_count_costs_one_int(setup_mcp, session, health_data):
    import json

    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    result = await get_health()
    gated = result["_meta"]["gated"]
    # One integer under one key; no reason sentence while findings are listed.
    assert gated["health"] == {"history": 2}
    assert "reasons" not in gated
    assert len(json.dumps(gated["health"])) < 20

    # Nothing listed on the target but history markers: the reason comes once.
    from repowise.core.persistence.crud import upsert_health_findings

    await upsert_health_findings(
        session,
        health_data,
        [
            {
                "file_path": _APP,
                "biomarker_type": "ownership_risk",
                "severity": "high",
                "function_name": "run",
                "line_start": 1,
                "line_end": 90,
                "details": {},
                "health_impact": 1.5,
                "reason": "ownership_risk only",
            }
        ],
        file_paths=[_APP],
    )
    await session.commit()
    targeted = await get_health(targets=[_APP])
    assert not targeted.get("findings")
    assert targeted["_meta"]["gated"]["health"] == {"history": 1}
    assert targeted["_meta"]["gated"]["reasons"] == {"history": FILE_HOLDS["history"]}


@pytest.mark.asyncio
async def test_targeted_findings_say_what_the_holds_deduct(setup_mcp, session, health_data):
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    result = await get_health(targets=[_APP], only=["findings"])
    assert [f["biomarker_type"] for f in result["findings"]] == ["complex_method"]
    # The two history markers deduct 1.5 + 1.0 points the list does not show.
    assert result["findings_hidden"] == (
        '2 held-back findings (history 2) carry 2.50 points of total_deduction not listed here; '
        'include=["unverified"] lists them.'
    )

    opted = await get_health(targets=[_APP], include=["unverified"])
    assert "findings_hidden" not in opted
    assert len(opted["findings"]) == 3


@pytest.mark.asyncio
async def test_targeted_findings_unchanged_when_nothing_is_held(setup_mcp, session, health_data):
    from repowise.core.persistence.crud import upsert_health_findings
    from repowise.server.mcp_server import get_health

    await _seed(session, health_data)
    await upsert_health_findings(
        session,
        health_data,
        [
            {
                "file_path": _APP,
                "biomarker_type": "complex_method",
                "severity": "high",
                "function_name": "run",
                "line_start": 1,
                "line_end": 90,
                "details": {},
                "health_impact": 2.0,
                "reason": "complex_method only",
            }
        ],
        file_paths=[_APP],
    )
    await session.commit()
    result = await get_health(targets=[_APP])
    assert "findings_hidden" not in result
    assert "gated" not in result["_meta"]
