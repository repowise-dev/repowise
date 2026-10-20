"""REST surfaces honour the layer x language gates.

Each route hides rows on a gated language (Java here) by default, returns
them with ``include_unverified=true``, counts what it held back in ``gated``,
and leaves the Python rows beside them alone. The gate table is patched, so
these pin the mechanism, not which cells today's table holds back; a preview
row is served with its ``preview`` note instead.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient

from repowise.core.analysis import finding_registry
from repowise.core.analysis.finding_registry import LanguageGate, preview_note
from repowise.core.analysis.health.models import HealthFindingData, Severity
from repowise.core.analysis.health.perf.opportunities import link_performance_findings
from repowise.core.persistence import crud
from repowise.core.persistence.models import DeadCodeFinding
from tests.unit.server.conftest import create_test_repo

JAVA = "src/Billing.java"
PY = "src/billing.py"
_LAYERS = ("health", "refactoring", "performance", "dead_code")
#: A row off a preview cell leaves ``preview`` out, not null.
ABSENT = "absent"


def _java_table(state: str) -> tuple[LanguageGate, ...]:
    return tuple(
        LanguageGate(layer, "java", 0.2, "test sample", "2026-10-15", "test", state=state)
        for layer in _LAYERS
    )


@pytest.fixture(autouse=True)
def _java_gated(monkeypatch):
    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", _java_table("gated"))


def _finding(path: str, function_name: str) -> dict[str, Any]:
    return {
        "file_path": path,
        "biomarker_type": "complex_method",
        "severity": "high",
        "function_name": function_name,
        "line_start": 10,
        "line_end": 30,
        "details": {"ccn": 16},
        "health_impact": 3.0,
        "reason": "seeded",
        "dimension": "defect",
    }


def _plan(path: str, symbol: str) -> dict[str, Any]:
    return {
        "refactoring_type": "extract_method",
        "file_path": path,
        "target_symbol": symbol,
        "line_start": 10,
        "line_end": 30,
        "plan": {"extracted_name": f"_{symbol}_part", "span": {"start": 12, "end": 28}},
        "evidence": {"ccn_removed": 6, "slice_nloc": 20},
        "impact_delta": 1.0,
        "effort_bucket": "S",
        "blast_radius": {"scope": "local"},
        "confidence": "high",
        "source_biomarker": "complex_method",
    }


def _perf(path: str) -> HealthFindingData:
    return HealthFindingData(
        biomarker_type="io_in_loop",
        severity=Severity.MEDIUM,
        file_path=path,
        function_name="run",
        line_start=10,
        line_end=10,
        details={
            "boundary_kind": "db",
            "cross_function": True,
            "path": [f"{path}::run", "src/db.py::fetch"],
            "resolution_basis": "reliable-edge",
        },
        health_impact=0.0,
        reason="Database work repeats for every loop iteration.",
        dimension="performance",
    )


async def _seed(client: AsyncClient, app) -> str:
    repo_id = (await create_test_repo(client))["id"]
    perf = [_perf(JAVA), _perf(PY)]
    link_performance_findings(perf)
    async with app.state.session_factory() as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                {"file_path": p, "score": 4.0, "max_ccn": 16, "max_nesting": 2, "nloc": 100}
                for p in (JAVA, PY)
            ],
        )
        await crud.save_health_findings(
            session, repo_id, [_finding(JAVA, "charge"), _finding(PY, "charge"), *perf]
        )
        await crud.save_refactoring_suggestions(
            session, repo_id, [_plan(JAVA, "charge"), _plan(PY, "charge")]
        )
        await crud.finalize_refactoring_opportunities(session, repo_id, analyzed_commit="c" * 40)
        await crud.finalize_performance_opportunities(session, repo_id, analyzed_commit="c" * 40)
        now = datetime.now(UTC)
        for i, path in enumerate((JAVA, PY)):
            session.add(
                DeadCodeFinding(
                    id=f"dead{i}",
                    repository_id=repo_id,
                    kind="unused_export",
                    file_path=path,
                    symbol_name="old",
                    symbol_kind="function",
                    confidence=0.9,
                    reason="no callers",
                    lines=10,
                    safe_to_delete=True,
                    status="open",
                    analyzed_at=now,
                )
            )
        await session.commit()
    return repo_id


async def _get(client: AsyncClient, url: str, **params: Any) -> Any:
    resp = await client.get(url, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


def _paths(rows: list[dict]) -> set[str]:
    return {row["file_path"] for row in rows}


async def test_health_findings_and_overview(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/health"

    assert _paths(await _get(client, f"{base}/findings")) == {PY}
    assert JAVA in _paths(await _get(client, f"{base}/findings", include_unverified=True))

    overview = await _get(client, f"{base}/overview")
    assert JAVA not in _paths(overview["top_findings"])
    assert overview["summary"]["gated"]["java"]["count"] == 2
    # The score still counts the gated finding, so the file still ranks.
    assert JAVA in _paths(overview["files"])
    opted = await _get(client, f"{base}/overview", include_unverified=True)
    assert JAVA in _paths(opted["top_findings"]) and opted["summary"]["gated"] == {}

    drawer = await _get(client, f"{base}/files/breakdown", file_path=JAVA)
    assert drawer["findings"] == [] and drawer["gated"]["java"]["count"] == 2


async def test_queue_counts_take_a_gated_unit_once(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    url = f"/api/repos/{repo_id}/health/overview"

    def served(counts: dict) -> int:
        return counts["eligible"] + sum(counts["excluded"].values())

    held = (await _get(client, url))["queue_counts"]
    opted = (await _get(client, url, include_unverified=True))["queue_counts"]
    for unit in ("findings", "causes", "plans"):
        counts, every = held[unit], opted[unit]
        # The Java row is in the inventory once, as gated, and in no other level.
        assert counts["gated"] == 1, unit
        assert counts["inventory"] == served(counts) + counts["gated"], unit
        # Opted in, nothing is held back and the inventory is the same rows.
        assert every["gated"] == 0, unit
        assert every["inventory"] == served(every) == counts["inventory"], unit


async def test_dead_code_list_and_summary(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/dead-code"

    assert _paths(await _get(client, base)) == {PY}
    assert _paths(await _get(client, base, include_unverified=True)) == {PY, JAVA}
    summary = await _get(client, f"{base}/summary")
    assert summary["total_findings"] == 1 and summary["gated"]["java"]["count"] == 1
    assert (await _get(client, f"{base}/summary", include_unverified=True))["gated"] == {}


async def test_refactoring_plans_opportunities_and_rollup(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    base = f"/api/repos/{repo_id}/refactoring"

    # The extract plan, and the performance plan the perf finalizer wrote.
    every = (await _get(client, f"{base}/targets", include_unverified=True))["plans"]
    java_plans = sum(1 for plan in every if plan["file_path"] == JAVA)
    assert java_plans >= 1
    targets = await _get(client, f"{base}/targets")
    assert _paths(targets["plans"]) == {PY}
    assert targets["gated"]["java"]["count"] == java_plans

    queue = await _get(client, f"{base}/opportunities", scope="all")
    assert _paths(queue["items"]) == {PY} and queue["gated"]["java"]["count"] == 1
    opted = await _get(client, f"{base}/opportunities", scope="all", include_unverified=True)
    assert _paths(opted["items"]) == {PY, JAVA} and opted["gated"] == {}

    rollup = await _get(client, f"{base}/summary")
    assert rollup["summary"]["gated"]["java"]["count"] == 1
    lead = rollup["summary"]["lead"]
    assert lead is None or lead["file_path"] == PY


async def test_performance_queue(client: AsyncClient, app) -> None:
    repo_id = await _seed(client, app)
    url = f"/api/repos/{repo_id}/health/performance-opportunities"

    # Named filters leave the default queue, which takes no unmeasured cause.
    every = {"context": "all", "proof": "unproven", "role": "all"}
    page = await _get(client, url, **every)
    assert JAVA not in {i.get("file_path") for i in page["items"]}
    assert page["gated"]["java"]["count"] == 1
    # The headline is recounted without the gated cause.
    assert page["summary"]["total"] == page["total"]
    opted = await _get(client, url, **every, include_unverified=True)
    assert opted["total"] == page["total"] + 1 and opted["gated"] == {}


async def test_preview_rows_are_served_labelled_and_not_counted(
    client: AsyncClient, app, monkeypatch
) -> None:
    repo_id = await _seed(client, app)
    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", _java_table("preview"))
    note = preview_note(_java_table("preview")[0])

    def by_path(rows: list[dict]) -> dict[str, Any]:
        return {row["file_path"]: row.get("preview", ABSENT) for row in rows}

    findings = await _get(client, f"/api/repos/{repo_id}/health/findings")
    assert by_path(findings) == {JAVA: note, PY: ABSENT}

    queue_rows = await _get(client, f"/api/repos/{repo_id}/health/refactoring-targets")
    assert by_path(queue_rows["targets"]) == {JAVA: note, PY: ABSENT}

    page = await _get(client, f"/api/repos/{repo_id}/files/{JAVA}")
    assert {f["preview"] for f in page["health"]["findings"]} == {note}
    assert page["health"]["gated"] == {}

    targets = await _get(client, f"/api/repos/{repo_id}/refactoring/targets")
    assert by_path(targets["plans"])[JAVA] == note and by_path(targets["plans"])[PY] == ABSENT
    assert targets["gated"] == {}

    queue = await _get(client, f"/api/repos/{repo_id}/refactoring/opportunities", scope="all")
    assert by_path(queue["items"]) == {JAVA: note, PY: ABSENT}
    assert queue["gated"] == {}

    every = {"context": "all", "proof": "unproven", "role": "all"}
    perf = await _get(client, f"/api/repos/{repo_id}/health/performance-opportunities", **every)
    assert by_path(perf["items"]) == {JAVA: note, PY: ABSENT} and perf["gated"] == {}

    dead = await _get(client, f"/api/repos/{repo_id}/dead-code")
    assert by_path(dead) == {JAVA: note, PY: ABSENT}
    summary = await _get(client, f"/api/repos/{repo_id}/dead-code/summary")
    assert summary["total_findings"] == 2 and summary["gated"] == {}


async def test_preview_rows_are_labelled_on_detail_routes(
    client: AsyncClient, app, monkeypatch
) -> None:
    repo_id = await _seed(client, app)
    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", _java_table("preview"))
    note = preview_note(_java_table("preview")[0])
    base = f"/api/repos/{repo_id}"

    every = {"context": "all", "proof": "unproven", "role": "all"}
    perf = await _get(client, f"{base}/health/performance-opportunities", **every)
    for item in perf["items"]:
        detail = await _get(
            client, f"{base}/health/performance-opportunities/{item['opportunity_id']}"
        )
        assert detail.get("preview", ABSENT) == (note if item["file_path"] == JAVA else ABSENT)

    queue = await _get(client, f"{base}/refactoring/opportunities", scope="all")
    for item in queue["items"]:
        detail = await _get(client, f"{base}/refactoring/opportunities/{item['opportunity_id']}")
        assert detail.get("preview", ABSENT) == (note if item["file_path"] == JAVA else ABSENT)

    plans = (await _get(client, f"{base}/refactoring/targets"))["plans"]
    for plan in plans:
        detail = await _get(client, f"{base}/refactoring/{plan['id']}")
        assert detail.get("preview", ABSENT) == (note if plan["file_path"] == JAVA else ABSENT)

    resp = await client.post(
        f"{base}/health/related-work", json={"file_paths": [JAVA, PY]}
    )
    assert resp.status_code == 200, resp.text
    lenses = {f["file_path"]: f["lenses"] for f in resp.json()["files"]}
    assert lenses[JAVA] and all(
        block.get("preview", ABSENT) == (ABSENT if lens == "fix_first" else note)
        for lens, block in lenses[JAVA].items()
    )
    assert all("preview" not in block for block in lenses[PY].values())


async def test_preview_rows_are_labelled_on_prompts_and_evidence(
    client: AsyncClient, app, monkeypatch
) -> None:
    repo_id = await _seed(client, app)
    monkeypatch.setattr(finding_registry, "LANGUAGE_GATES", _java_table("preview"))
    note = preview_note(_java_table("preview")[0])
    base = f"/api/repos/{repo_id}"
    line = f"This language is served as a {note}."

    def check(prompt: dict, on_java: bool) -> None:
        assert prompt.get("preview", ABSENT) == (note if on_java else ABSENT)
        assert (line in prompt["text"]) is on_java

    queue = await _get(client, f"{base}/refactoring/opportunities", scope="all")
    for item in queue["items"]:
        prompt = await _get(
            client, f"{base}/refactoring/opportunities/{item['opportunity_id']}/prompt"
        )
        check(prompt, item["file_path"] == JAVA)

    for plan in (await _get(client, f"{base}/refactoring/targets"))["plans"]:
        check(await _get(client, f"{base}/refactoring/{plan['id']}/prompt"), plan["file_path"] == JAVA)

    every = {"context": "all", "proof": "unproven", "role": "all"}
    perf = await _get(client, f"{base}/health/performance-opportunities", **every)
    seen = set()
    for item in perf["items"]:
        evidence = await _get(
            client, f"{base}/health/performance-opportunities/{item['opportunity_id']}/findings"
        )
        for row in evidence["items"]:
            seen.add(row["file_path"])
            assert row.get("preview", ABSENT) == (note if row["file_path"] == JAVA else ABSENT)
    assert JAVA in seen

    fix_first = await _get(client, f"{base}/health/fix-first", scope="all", limit=20)
    for item in fix_first["items"]:
        prompt = await _get(
            client, f"{base}/health/fix-first/{item['id']}/prompt", scope="all"
        )
        check(prompt, item["target"]["file_path"] == JAVA)
