"""Golden output of the knowledge-map services and endpoint.

Pins silos, onboarding targets and the full map exactly, so moving the folds
between packages cannot change what Overview or ``/knowledge-map`` serves.
Rewrite with ``REPOWISE_REWRITE_GOLDEN=1`` only for an intended behaviour change.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, Page
from repowise.server.routers import knowledge_map
from repowise.server.services.knowledge_map import (
    compute_knowledge_map,
    compute_knowledge_silos,
    compute_onboarding_targets,
)
from tests.unit.server.conftest import create_test_repo

GOLDEN = Path(__file__).parent / "golden" / "knowledge_map.json"
_AT = datetime(2026, 9, 1, tzinfo=UTC)

# (path, owner, email, pct, commits_90d, hotspot)
_GIT = [
    ("src/api.py", "Alice", "alice@example.com", 0.95, 4, False),
    ("src/core.py", "Bob", "bob@example.com", 0.85, 30, True),
    ("src/db.py", "Alice", "alice@example.com", 0.99, 30, True),
    ("src/cli.py", "Carol", "carol@example.com", 0.81, 12, False),
    ("src/old.py", "Carol", None, 1.0, 0, False),
    ("src/shared.py", "Bob", "bob@example.com", 0.8, 50, True),
    ("src/team.py", "Dan", "dan@example.com", 0.4, 9, False),
    ("src/none.py", None, None, None, 0, False),
]

# (node_id, pagerank, is_test)
_NODES = [(f"src/m{i:02d}.py", 0.01 * (i + 1), False) for i in range(8)] + [
    ("src/m10.py", 0.2, False),
    ("src/m11.py", 0.3, False),
    ("src/api.py", 0.5, False),
    ("src/core.py", 0.9, False),
    ("tests/test_core.py", 0.7, True),
    ("src/zero.py", 0.0, False),
]

# Shortlisted by character length (m11 before m10), reported in words.
_PAGES = {
    "src/m11.py": "a b c d e f",
    "src/m10.py": "supercalifragilisticexpialidocious",
    "src/api.py": "a " * 400,
    "src/core.py": "documented " * 50,
}


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for path, owner, email, pct, commits, hotspot in _GIT:
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                primary_owner_name=owner,
                primary_owner_email=email,
                primary_owner_commit_pct=pct,
                commit_count_90d=commits,
                is_hotspot=hotspot,
            )
        session.add_all(
            [
                GraphNode(
                    id=f"n{i:02d}",
                    repository_id=repo_id,
                    node_id=node_id,
                    pagerank=pagerank,
                    is_test=is_test,
                )
                for i, (node_id, pagerank, is_test) in enumerate(_NODES)
            ]
        )
        session.add_all(
            [
                Page(
                    id=f"file_page:{path}",
                    repository_id=repo_id,
                    page_type="file_page",
                    title=path,
                    content=content,
                    target_path=path,
                    source_hash="h",
                    model_name="m",
                    provider_name="p",
                    created_at=_AT,
                    updated_at=_AT,
                )
                for path, content in _PAGES.items()
            ]
        )
        await session.commit()


@pytest.mark.anyio
async def test_knowledge_map_matches_the_golden(
    client: AsyncClient, app, session_factory
) -> None:
    # The shared test app does not mount this router.
    app.include_router(knowledge_map.router)
    repo = await create_test_repo(client)
    await _seed(session_factory, repo["id"])

    async with get_session(session_factory) as session:
        result = {
            "silos": await compute_knowledge_silos(session, repo["id"]),
            "onboarding_targets": await compute_onboarding_targets(session, repo["id"]),
            "knowledge_map": await compute_knowledge_map(session, repo["id"]),
        }
    resp = await client.get(f"/api/repos/{repo['id']}/knowledge-map")
    assert resp.status_code == 200
    result["endpoint"] = resp.json()

    if os.environ.get("REPOWISE_REWRITE_GOLDEN"):
        GOLDEN.parent.mkdir(exist_ok=True)
        GOLDEN.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8", newline="\n")
        pytest.skip("golden rewritten")
    assert result == json.loads(GOLDEN.read_text(encoding="utf-8"))
