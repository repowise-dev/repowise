"""Golden of the whole ``/overview-summary`` payload, byte for byte.

Pins every block, value and dict key order the Overview page reads, so a
refactor of ``overview_summary`` must serve identical JSON. Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import time_machine
from httpx import AsyncClient
from sqlalchemy import select, update

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
    GraphNode,
    HealthFileMetric,
    Page,
    WikiSymbol,
)
from tests.unit.server.conftest import (
    assert_payload_golden,
    create_test_repo,
    normalize_payload,
    path_placeholders,
)
from tests.unit.server.test_attention_golden import _seed as seed_findings

GOLDEN = Path(__file__).parent / "golden" / "overview_summary.json"
_CLOCK = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)


async def _seed_index(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for i, (path, lang, entry, rank) in enumerate(
            [
                ("src/core.py", "python", True, 0.4),
                ("src/util.py", "python", False, 0.25),
                ("web/app.ts", "typescript", True, 0.5),
                ("tests/test_core.py", "python", False, 0.05),
            ]
        ):
            session.add(
                GraphNode(
                    id=f"gn{i}",
                    repository_id=repo_id,
                    node_id=path,
                    node_type="file",
                    language=lang,
                    symbol_count=i + 2,
                    is_entry_point=entry,
                    is_test=path.startswith("tests/"),
                    pagerank=rank,
                    created_at=_CLOCK,
                )
            )
            # The symbol index the stat strip counts, matching each file's count.
            for j in range(i + 2):
                session.add(
                    WikiSymbol(
                        id=f"ws{i}_{j}",
                        repository_id=repo_id,
                        file_path=path,
                        symbol_id=f"{path}::sym{j}",
                        name=f"sym{j}",
                        qualified_name=f"sym{j}",
                        kind="function",
                        created_at=_CLOCK,
                        updated_at=_CLOCK,
                    )
                )
        for i, (path, owner, hotspot, churn) in enumerate(
            [
                ("src/core.py", "Alice", True, 0.95),
                ("src/util.py", "Alice", False, 0.5),
                ("web/app.ts", "Bob", True, 0.75),
            ]
        ):
            session.add(
                GitMetadata(
                    id=f"gm{i}",
                    repository_id=repo_id,
                    file_path=path,
                    commit_count_total=40 - i * 10,
                    commit_count_90d=12 - i * 3,
                    commit_count_30d=4 - i,
                    last_commit_at=datetime(2026, 8, 20 + i, tzinfo=UTC),
                    primary_owner_name=owner,
                    primary_owner_commit_pct=0.9,
                    top_authors_json=json.dumps([{"name": owner, "count": 30}]),
                    is_hotspot=hotspot,
                    churn_percentile=churn,
                    temporal_hotspot_score=3.0 - i,
                    created_at=_CLOCK,
                    updated_at=_CLOCK,
                )
            )
        # Onboarding targets are production code files the health index scored.
        # The findings seed may already hold a row for a path: class it rather
        # than add a second.
        scored = {
            m.file_path: m
            for m in (
                await session.scalars(
                    select(HealthFileMetric).where(HealthFileMetric.repository_id == repo_id)
                )
            )
        }
        for path in ("src/core.py", "src/util.py", "web/app.ts"):
            if path in scored:
                scored[path].code_origin = "production"
            else:
                session.add(
                    HealthFileMetric(
                        repository_id=repo_id,
                        file_path=path,
                        score=8.0,
                        code_origin="production",
                    )
                )
        for i, (path, status, confidence) in enumerate(
            [("src/core.py", "fresh", 0.9), ("web/app.ts", "stale", 0.5)]
        ):
            session.add(
                Page(
                    id=f"file_page:{path}",
                    repository_id=repo_id,
                    page_type="file_page",
                    title=path,
                    content=f"# {path}",
                    target_path=path,
                    source_hash=f"h{i}",
                    model_name="mock",
                    provider_name="template" if i else "mock",
                    generation_level=2,
                    confidence=confidence,
                    freshness_status=status,
                    metadata_json="{}",
                    created_at=_CLOCK,
                    updated_at=_CLOCK,
                )
            )
        session.add(
            DecisionRecord(
                id="dec1",
                repository_id=repo_id,
                title="Keep the engine single-threaded",
                status="proposed",
                decision="One worker per repo",
                rationale="Simpler locking",
                affected_files_json=json.dumps(["src/core.py"]),
                source="cli",
                confidence=0.9,
                created_at=_CLOCK,
                updated_at=_CLOCK,
            )
        )
        # The findings seed leaves scores unset; score its one code file.
        await session.execute(
            update(HealthFileMetric)
            .where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.file_path == "src/core.py",
            )
            .values(
                score=4.5,
                defect_score=4.5,
                maintainability_score=5.5,
                performance_score=9.5,
                max_ccn=12,
                max_nesting=3,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_overview_summary_matches_the_golden(
    client: AsyncClient, session_factory, tmp_path
) -> None:
    with time_machine.travel(_CLOCK, tick=False):
        repo = await create_test_repo(client, tmp_path)
        await seed_findings(session_factory, repo["id"])
        await _seed_index(session_factory, repo["id"])
        resp = await client.get(f"/api/repos/{repo['id']}/overview-summary")

    assert resp.status_code == 200
    replace = {repo["id"]: "<repo-id>", **path_placeholders(tmp_path, "<tmp>")}
    assert_payload_golden(GOLDEN, normalize_payload(resp.json(), replace))
