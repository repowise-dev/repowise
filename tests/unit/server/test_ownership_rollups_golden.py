"""Golden output of the owners, module-health and reviewer endpoints.

Pins every field and order these three rollups serve over one seeded repo, so
moving the folds between packages cannot change the wire. Rewrite with
``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DeadCodeFinding, DecisionRecord, WikiSymbol
from tests.unit.server.conftest import create_test_repo

GOLDEN = Path(__file__).parent / "golden" / "ownership_rollups.json"

_ALICE = {"name": "Alice", "email": "alice@example.com"}
_BOB = {"name": "Bob", "email": "bob@example.com"}
_BOB_NOREPLY = {"name": "Bob", "email": "4242+bob@users.noreply.github.com"}
_CAROL = {"name": "Carol", "email": None}


def _authors(*rows: tuple[dict, int, int | None]) -> str:
    return json.dumps(
        [
            {**who, "commit_count": n, **({"last_commit_ts": ts} if ts else {})}
            for who, n, ts in rows
        ]
    )


# Naive on purpose: SQLite hands them back naive, which the fold must coerce.
_GIT = {
    "src/core.py": dict(
        primary_owner_name="Alice",
        primary_owner_email="alice@example.com",
        primary_owner_commit_pct=0.7,
        top_authors_json=_authors((_ALICE, 35, 1_780_000_000), (_BOB, 10, None), (_CAROL, 5, None)),
        commit_count_total=50,
        commit_count_90d=20,
        lines_added_90d=300,
        lines_deleted_90d=100,
        commit_categories_json=json.dumps({"fix": 4, "feature": 6}),
        first_commit_at=datetime(2025, 1, 2, 3, 4, 5),
        last_commit_at=datetime(2026, 8, 1, 12, 0, 0),
        is_hotspot=True,
        temporal_hotspot_score=0.9,
        churn_percentile=0.95,
        bus_factor=1,
        agent_authored_pct=0.2,
        agent_commit_count=10,
        agent_tier_counts_json=json.dumps({"confirmed": 6, "likely": 4}),
        co_change_partners_json=json.dumps(
            [
                {"file_path": "lib/util.py", "co_change_count": 9},
                {"file_path": "src/api.py", "co_change_count": 4},
            ]
        ),
    ),
    "src/api.py": dict(
        primary_owner_name="Bob",
        primary_owner_email="4242+bob@users.noreply.github.com",
        primary_owner_commit_pct=0.9,
        top_authors_json=_authors((_BOB_NOREPLY, 18, 1_785_000_000), (_ALICE, 2, None)),
        commit_count_total=20,
        commit_count_90d=5,
        lines_added_90d=80,
        first_commit_at=datetime(2025, 6, 1),
        last_commit_at=datetime(2026, 7, 1),
        is_hotspot=True,
        temporal_hotspot_score=0.4,
        churn_percentile=0.6,
        bus_factor=1,
        agent_authored_pct=0.0,
        agent_commit_count=0,
    ),
    "src/legacy.py": dict(
        primary_owner_name="Carol",
        primary_owner_email=None,
        primary_owner_commit_pct=1.0,
        top_authors_json=_authors((_CAROL, 7, None)),
        commit_count_total=7,
        first_commit_at=datetime(2019, 3, 3),
        last_commit_at=datetime(2020, 3, 3),
        churn_percentile=0.1,
        bus_factor=1,
    ),
    "lib/util.py": dict(
        primary_owner_name="Bob",
        primary_owner_email="bob@example.com",
        primary_owner_commit_pct=0.6,
        top_authors_json=_authors((_BOB, 6, None), (_ALICE, 4, 1_770_000_000)),
        commit_count_total=10,
        commit_count_90d=3,
        commit_categories_json="{not json",
        churn_percentile=0.4,
        bus_factor=2,
        co_change_partners_json=json.dumps([{"file_path": "src/core.py", "co_change_count": 9}]),
    ),
    "lib/broken.py": dict(
        primary_owner_name="Alice",
        primary_owner_email="alice@example.com",
        primary_owner_commit_pct=0.5,
        top_authors_json="not json",
        churn_percentile=0.2,
        bus_factor=3,
    ),
    "setup.py": dict(
        primary_owner_name=None,
        primary_owner_email=None,
        top_authors_json="[]",
        bus_factor=0,
    ),
}


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for path, fields in _GIT.items():
            await crud.upsert_git_metadata(
                session, repository_id=repo_id, file_path=path, **fields
            )
        session.add_all(
            [
                WikiSymbol(
                    id=f"sym{i}",
                    repository_id=repo_id,
                    file_path=path,
                    symbol_id=f"{path}::f{i}",
                    name=f"f{i}",
                    qualified_name=f"f{i}",
                    kind="function",
                    docstring=doc,
                )
                for i, (path, doc) in enumerate(
                    [
                        ("src/core.py", "Does the thing."),
                        ("src/core.py", "   "),
                        ("src/api.py", None),
                        ("lib/util.py", "Helps."),
                        ("orphan/x.py", "No git row, so no module."),
                    ]
                )
            ]
        )
        session.add_all(
            [
                DeadCodeFinding(
                    id=f"dead{i}",
                    repository_id=repo_id,
                    kind="unused_export",
                    file_path=path,
                    symbol_name="old",
                    symbol_kind="function",
                    confidence=0.9,
                    lines=lines,
                    safe_to_delete=True,
                    primary_owner=owner,
                )
                for i, (path, lines, owner) in enumerate(
                    [
                        ("src/core.py", 12, "Alice"),
                        ("src/legacy.py", 30, "Carol"),
                        ("lib/util.py", None, "Nobody"),
                        ("orphan/x.py", 5, None),
                    ]
                )
            ]
        )
        session.add_all(
            [
                DecisionRecord(
                    id=did,
                    repository_id=repo_id,
                    title=title,
                    status="active",
                    affected_modules_json=modules,
                )
                for did, title, modules in [
                    ("dec-src", "Keep src flat", json.dumps(["src"])),
                    ("dec-both", "Share one util", json.dumps(["lib", "src", "gone"])),
                    ("dec-bad", "Malformed scope", "{oops"),
                ]
            ]
        )
        await session.commit()


@pytest.mark.anyio
async def test_ownership_rollups_match_the_golden(client: AsyncClient, session_factory) -> None:
    repo = await create_test_repo(client)
    await _seed(session_factory, repo["id"])
    base = f"/api/repos/{repo['id']}"

    async def get(url: str, params=None):
        resp = await client.get(base + url, params=params)
        assert resp.status_code == 200, url
        return resp.json()

    owners = await get("/owners")
    result = {
        "owners": owners,
        "owners_by_commits": await get("/owners", {"sort": "commit_count_90d"}),
        "owner_profiles": {
            o["key"]: await get(f"/owners/{quote(o['key'], safe='')}") for o in owners["items"]
        },
        "modules": await get("/modules/health"),
        "module_details": {
            path: await get(f"/modules/health/{quote(path)}")
            for path in ("src", "lib", "root", "src/api.py", "src/nested/missing.py")
        },
        "reviewers": await get(
            "/reviewer-suggestions", [("paths", "src/core.py"), ("paths", "src/api.py")]
        ),
        "reviewers_limited": await get(
            "/reviewer-suggestions", [("paths", "lib/util.py"), ("limit", "1")]
        ),
    }

    if os.environ.get("REPOWISE_REWRITE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("golden rewritten")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert result == golden
