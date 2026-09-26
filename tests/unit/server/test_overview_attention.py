"""The merged Overview attention list.

The old builder read three stores and ranked them by which store an item came
from. These cover the two properties that replaced that: severity is the
finding's own, and the total travels with the capped list.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DocDriftFinding, SecurityFinding
from repowise.server.services.attention import PER_SOURCE_CAP, build_attention
from tests.unit.server.conftest import create_test_repo


async def _seed_health_findings(session_factory, repo_id: str, count: int = 3) -> None:
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": f"src/mod_{i}.py",
                    "biomarker_type": "brain_method",
                    "severity": "critical" if i == 0 else "low",
                    "function_name": f"fn_{i}",
                    # 9.0 leaves a score of 1.0 (critical); 0.5 leaves 9.5.
                    "health_impact": 9.0 if i == 0 else 0.5,
                    "dimension": "defect",
                }
                for i in range(count)
            ],
        )


async def _seed_security(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        session.add(
            SecurityFinding(
                repository_id=repo_id,
                file_path="config/settings.py",
                kind="aws_access_key",
                severity="high",
                snippet="AKIA…",
                line_number=12,
                commit_sha="",
                detected_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        await session.commit()


async def _seed_drift(session_factory, repo_id: str, confidence: float) -> None:
    async with get_session(session_factory) as session:
        session.add(
            DocDriftFinding(
                repository_id=repo_id,
                file_path="README.md",
                kind="missing_path",
                line_number=3,
                target="docs/gone.md",
                confidence=confidence,
                reason="names a file that is not in the tree",
                detected_at=datetime(2026, 9, 1, tzinfo=UTC),
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_merges_every_source_and_ranks_by_the_findings_own_severity(
    client: AsyncClient, session_factory
) -> None:
    """A critical health finding outranks a high-severity stale decision.

    This is the behaviour the per-source ladder could not express: the old
    builder's top band was `high`, and every health finding would have been
    flattened into whatever constant its source was assigned.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    await _seed_health_findings(session_factory, repo_id)
    await _seed_security(session_factory, repo_id)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session,
            repo_id,
            decision_health={
                "stale_decisions": [],
                "proposed_awaiting_review": [],
                "ungoverned_hotspots": [],
            },
            knowledge_silos=[],
        )

    types = [i["type"] for i in result["items"]]
    assert "health_finding" in types
    assert "security_finding" in types

    # Critical leads. The security finding is `high`, so it sits second: within
    # a band the tie-break prefers security, but the band itself wins first.
    assert result["items"][0]["severity"] == "critical"
    assert result["items"][0]["type"] == "health_finding"
    assert result["items"][1]["type"] == "security_finding"


@pytest.mark.anyio
async def test_security_outranks_an_equally_severe_health_finding(
    client: AsyncClient, session_factory
) -> None:
    """The tie-break, isolated from the severity sort."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": "src/a.py",
                    "biomarker_type": "brain_method",
                    "severity": "high",
                    # 5.0 deducted leaves a score of 5.0, which is `high`.
                    "health_impact": 5.0,
                    "dimension": "defect",
                }
            ],
        )
    await _seed_security(session_factory, repo_id)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    high = [i for i in result["items"] if i["severity"] == "high"]
    assert [i["type"] for i in high][:2] == ["security_finding", "health_finding"]


@pytest.mark.anyio
async def test_drift_confidence_buckets_onto_the_shared_ladder(
    client: AsyncClient, session_factory
) -> None:
    """Drift has no severity column, so confidence decides its band."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    await _seed_drift(session_factory, repo_id, confidence=0.95)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    drift = [i for i in result["items"] if i["type"] == "doc_drift"]
    assert len(drift) == 1
    # Confident, so `medium` — never `high`, which would let a stale sentence in
    # a README push a credential off a five-row list.
    assert drift[0]["severity"] == "medium"


@pytest.mark.anyio
async def test_total_counts_everything_the_sources_hold_not_the_capped_list(
    client: AsyncClient, session_factory
) -> None:
    """`len(items)` is not the total, which is the whole reason for the summary."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    over_cap = PER_SOURCE_CAP + 5
    await _seed_health_findings(session_factory, repo_id, count=over_cap)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    health_items = [i for i in result["items"] if i["type"] == "health_finding"]
    assert len(health_items) == PER_SOURCE_CAP
    assert result["by_source"]["health_finding"] == over_cap
    assert result["total"] == over_cap


@pytest.mark.anyio
async def test_route_serves_the_list_and_its_summary(
    client: AsyncClient, session_factory
) -> None:
    """The payload carries both halves, and the item shape survives the route."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    await _seed_health_findings(session_factory, repo_id, count=2)
    await _seed_security(session_factory, repo_id)

    resp = await client.get(f"/api/repos/{repo_id}/overview-summary")
    assert resp.status_code == 200
    body = resp.json()

    assert body["attention_summary"]["total"] >= 3
    assert body["attention_summary"]["by_source"]["security_finding"] == 1
    # `subtype` is what lets the UI name the biomarker rather than the source.
    health_rows = [i for i in body["attention"] if i["type"] == "health_finding"]
    assert health_rows and health_rows[0]["subtype"] == "brain_method"


@pytest.mark.anyio
async def test_an_empty_repository_reports_nothing_rather_than_failing(
    client: AsyncClient, session_factory
) -> None:
    repo = await create_test_repo(client)
    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo["id"], decision_health={}, knowledge_silos=[]
        )
    assert result == {"items": [], "total": 0, "by_source": {}, "areas": []}


@pytest.mark.anyio
async def test_one_source_cannot_own_a_band(client: AsyncClient, session_factory) -> None:
    """The spread, which a plain severity sort does not give at real scale.

    On this repository the health store holds ~16,000 open findings against
    ~20 drift findings, so a strict sort hands every visible row to health and
    the section becomes a code-health list wearing a different title.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": f"src/mod_{i}.py",
                    "biomarker_type": "brain_method",
                    "severity": "high",
                    # Scores 5.00 to 5.35, all inside the `high` band.
                    "health_impact": 5.0 - i * 0.05,
                    "dimension": "defect",
                }
                for i in range(PER_SOURCE_CAP)
            ],
        )
    await _seed_security(session_factory, repo_id)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    # Security has one `high` row against health's eight, and it is not buried
    # behind all of them.
    types = [i["type"] for i in result["items"]]
    assert types[:2] == ["security_finding", "health_finding"]


@pytest.mark.anyio
async def test_a_band_never_bleeds_into_the_one_above_it(
    client: AsyncClient, session_factory
) -> None:
    """Round-robin is within a band, so severity still decides absolutely."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": "src/bad.py",
                    "biomarker_type": "brain_method",
                    "severity": "critical",
                    "health_impact": 9.0,
                    "dimension": "defect",
                },
                {
                    "file_path": "src/also_bad.py",
                    "biomarker_type": "god_class",
                    "severity": "critical",
                    "health_impact": 8.0,
                    "dimension": "defect",
                },
            ],
        )
    # `high`, and it must not be pulled above either critical just because it
    # is the only row its source has.
    await _seed_security(session_factory, repo_id)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    assert [i["severity"] for i in result["items"][:3]] == ["critical", "critical", "high"]


@pytest.mark.anyio
async def test_test_files_are_out_of_scope(client: AsyncClient, session_factory) -> None:
    """Production scope. A test file cannot be the most urgent thing in a repo."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_metrics(
            session,
            repo_id,
            [
                {"file_path": "tests/test_thing.py", "nloc": 100, "is_test": True},
                {"file_path": "src/thing.py", "nloc": 100, "is_test": False},
            ],
        )
        await crud.save_health_findings(
            session,
            repo_id,
            [
                {
                    "file_path": "tests/test_thing.py",
                    "biomarker_type": "brain_method",
                    "severity": "critical",
                    "health_impact": 9.0,
                    "dimension": "defect",
                },
                {
                    "file_path": "src/thing.py",
                    "biomarker_type": "brain_method",
                    "severity": "low",
                    "health_impact": 0.5,
                    "dimension": "defect",
                },
            ],
        )

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    paths = [i["target_id"] for i in result["items"]]
    assert "src/thing.py" in paths
    # Critical, highest impact, and still absent: scope wins over severity.
    assert "tests/test_thing.py" not in paths
    assert result["by_source"]["health_finding"] == 1


@pytest.mark.anyio
async def test_areas_roll_up_and_lead_with_the_worst_item(
    client: AsyncClient, session_factory
) -> None:
    """Every area gets a row, whatever the volume in the areas beside it."""
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    await _seed_health_findings(session_factory, repo_id, count=PER_SOURCE_CAP)
    await _seed_security(session_factory, repo_id)
    await _seed_drift(session_factory, repo_id, confidence=0.95)

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    areas = {a["key"]: a for a in result["areas"]}
    # Drift holds one finding against health's eight and still gets a row —
    # the failure the ranked item list has by construction.
    assert set(areas) == {"security", "health", "doc_drift"}
    assert areas["health"]["total"] == PER_SOURCE_CAP
    assert areas["health"]["severity"] == "critical"
    assert areas["health"]["lead"]["subtype"] == "brain_method"
    assert areas["doc_drift"]["total"] == 1
    # Fixed display order, not severity order.
    assert [a["key"] for a in result["areas"]] == ["security", "health", "doc_drift"]


@pytest.mark.anyio
async def test_the_lead_is_the_heaviest_item_not_the_first_alphabetically(
    client: AsyncClient, session_factory
) -> None:
    """The tie-break inside a band is impact, not the item id.

    It used to fall through to `id`, so the lead of an area was whichever
    member of its worst band sorted first alphabetically — and the health query
    fetches in `health_impact DESC`, an ordering the rank pass then discarded.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                # Sorts first by path, and matters least.
                {
                    "file_path": "src/aaa.py",
                    "biomarker_type": "brain_method",
                    "severity": "critical",
                    "health_impact": 0.2,
                    "dimension": "defect",
                },
                {
                    "file_path": "src/zzz.py",
                    "biomarker_type": "god_class",
                    "severity": "critical",
                    "health_impact": 8.0,
                    "dimension": "defect",
                },
            ],
        )

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    health = next(a for a in result["areas"] if a["key"] == "health")
    assert health["lead"]["target_id"] == "src/zzz.py"
    assert result["items"][0]["target_id"] == "src/zzz.py"


@pytest.mark.anyio
async def test_a_dormant_silo_never_leads_over_an_active_one(
    client: AsyncClient, session_factory
) -> None:
    """Sole ownership only costs something on code that still changes."""
    repo = await create_test_repo(client)
    silos = [
        {
            "file_path": "docker/entrypoint.sh",
            "owner_email": "a@b.c",
            "owner_pct": 0.95,
            "commit_count_90d": 0,
            "is_hotspot": False,
        },
        {
            "file_path": "src/core/engine.py",
            "owner_email": "a@b.c",
            "owner_pct": 0.85,
            "commit_count_90d": 40,
            "is_hotspot": True,
        },
    ]
    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo["id"], decision_health={}, knowledge_silos=silos
        )

    ownership = next(a for a in result["areas"] if a["key"] == "ownership")
    # Lower concentration, far more activity, and it leads.
    assert ownership["lead"]["target_id"] == "src/core/engine.py"
    assert "40 commits in 90d" in ownership["lead"]["description"]


@pytest.mark.anyio
async def test_health_leads_with_the_worst_file_not_the_worst_finding(
    client: AsyncClient, session_factory
) -> None:
    """`health_impact` saturates, so ranking findings barely orders the top.

    A file carrying many findings outranks a single finding that happens to sit
    on the per-finding cap, which is what the file's own score already says.
    """
    repo = await create_test_repo(client)
    repo_id = repo["id"]
    async with get_session(session_factory) as session:
        await crud.save_health_findings(
            session,
            repo_id,
            [
                # One finding, on the cap, and the worst band.
                {
                    "file_path": "src/one_bad_function.py",
                    "biomarker_type": "nested_complexity",
                    "severity": "critical",
                    "health_impact": 2.5,
                    "dimension": "defect",
                },
                # Six findings totalling far more deduction.
                *[
                    {
                        "file_path": "src/app.py",
                        "biomarker_type": "complex_method" if i else "god_class",
                        "severity": "high",
                        "health_impact": 2.0 if i == 0 else 1.5,
                        "dimension": "defect",
                    }
                    for i in range(6)
                ],
            ],
        )

    async with get_session(session_factory) as session:
        result = await build_attention(
            session, repo_id, decision_health={}, knowledge_silos=[]
        )

    health = [i for i in result["items"] if i["type"] == "health_finding"]
    # One row per file, not per finding.
    assert len(health) == 2
    assert health[0]["target_id"] == "src/app.py"
    assert "6 findings" in health[0]["description"]
    # Named by its own heaviest biomarker.
    assert health[0]["subtype"] == "god_class"
    # Severity describes the file: 9.5 deducted leaves a score of 0.5.
    assert health[0]["severity"] == "critical"
    # And the single capped finding leaves a score of 7.5, which is not.
    assert health[1]["severity"] == "low"
    # The count is still findings, so the row can say how much the area holds.
    assert result["by_source"]["health_finding"] == 7
