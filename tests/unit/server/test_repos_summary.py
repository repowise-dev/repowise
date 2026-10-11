"""Tests for GET /api/repos/summary — the multi-repo dashboard payload.

The two properties worth pinning are that the figures are counts of what
their names say, and that the query count does not grow with the number of
repositories. The route exists because the shape it replaces did both wrong.
"""

from __future__ import annotations

import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import event

from repowise.core.persistence.crud import upsert_page
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GitMetadata,
    GraphNode,
    HealthSnapshot,
)
from tests.unit.persistence.helpers import make_page_kwargs


async def _register(client: AsyncClient, name: str) -> dict:
    """Register a repo against a real temp dir (the create route validates it)."""
    repo_dir = Path(tempfile.mkdtemp()) / name
    repo_dir.mkdir()
    (repo_dir / ".git").mkdir()
    resp = await client.post(
        "/api/repos",
        json={"index": False, "name": name, "local_path": str(repo_dir), "url": ""},
    )
    assert resp.status_code == 201
    return resp.json()


async def _seed(session, repo_id: str, *, files: int, symbols_per_file: int = 3) -> None:
    """Give a repo one of everything the summary counts."""
    for i in range(files):
        session.add(
            GraphNode(
                repository_id=repo_id,
                node_id=f"src/mod{i}.py",
                node_type="file",
                symbol_count=symbols_per_file,
                is_entry_point=(i == 0),
            )
        )
        # Symbol nodes share the table with file nodes. They are what makes the
        # unscoped count wrong, so every fixture carries some.
        for j in range(symbols_per_file):
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=f"src/mod{i}.py::sym{j}",
                    node_type="symbol",
                    symbol_count=0,
                )
            )
        session.add(
            GitMetadata(
                repository_id=repo_id,
                file_path=f"src/mod{i}.py",
                is_hotspot=(i == 0),
            )
        )
        await upsert_page(
            session,
            **make_page_kwargs(
                repo_id,
                page_id=f"file_page:src/mod{i}.py",
                target_path=f"src/mod{i}.py",
                title=f"mod{i}",
                freshness_status="fresh" if i == 0 else "stale",
            ),
        )
    session.add(
        DeadCodeFinding(
            repository_id=repo_id,
            kind="unused_export",
            file_path="src/mod0.py",
            status="open",
        )
    )
    # Should not be counted: a resolved export, and an open finding of another kind.
    session.add(
        DeadCodeFinding(
            repository_id=repo_id,
            kind="unused_export",
            file_path="src/mod0.py",
            symbol_name="already_gone",
            status="resolved",
        )
    )
    session.add(
        DeadCodeFinding(
            repository_id=repo_id,
            kind="unreachable_file",
            file_path="src/orphan.py",
            status="open",
        )
    )
    await session.commit()


@pytest.mark.asyncio
async def test_summary_is_not_shadowed_by_the_repo_id_route(client: AsyncClient) -> None:
    """`/summary` must be declared before `/{repo_id}`.

    Declared after it, FastAPI matches `get_repo(repo_id="summary")` and the
    route answers 404 "Repository not found" — with nothing else failing.
    """
    resp = await client.get("/api/repos/summary")
    assert resp.status_code == 200
    assert resp.json() == {"repos": []}


@pytest.mark.asyncio
async def test_file_count_excludes_symbol_nodes(client: AsyncClient, session) -> None:
    """The count has to be counting the thing the noun names.

    `/stats` counts every `graph_nodes` row as a file; on this codebase that
    reports 38,813 files against 3,600 real ones.
    """
    repo = await _register(client, "counted")
    await _seed(session, repo["id"], files=4, symbols_per_file=3)

    resp = await client.get("/api/repos/summary")
    assert resp.status_code == 200
    row = resp.json()["repos"][0]

    assert row["file_count"] == 4  # not 16
    assert row["symbol_count"] == 12
    assert row["entry_point_count"] == 1


@pytest.mark.asyncio
async def test_summary_reports_each_figure(client: AsyncClient, session) -> None:
    repo = await _register(client, "figures")
    await _seed(session, repo["id"], files=3)
    session.add(
        HealthSnapshot(
            repository_id=repo["id"],
            taken_at=datetime.now(UTC),
            average_health=7.43,
            hotspot_health=4.62,
        )
    )
    await session.commit()

    row = (await client.get("/api/repos/summary")).json()["repos"][0]

    assert row["id"] == repo["id"]
    assert row["name"] == "figures"
    assert row["doc_page_count"] == 3
    assert row["doc_fresh_page_count"] == 1
    assert row["dead_export_count"] == 1  # open unused_export only
    assert row["tracked_file_count"] == 3
    assert row["hotspot_count"] == 1
    assert row["average_health"] == 7.43
    assert row["hotspot_health"] == 4.62


@pytest.mark.asyncio
async def test_health_uses_the_latest_snapshot(client: AsyncClient, session) -> None:
    repo = await _register(client, "trending")
    now = datetime.now(UTC)
    for offset, score in ((2, 5.0), (0, 8.5), (1, 6.0)):
        session.add(
            HealthSnapshot(
                repository_id=repo["id"],
                taken_at=now - timedelta(hours=offset),
                average_health=score,
                hotspot_health=score,
            )
        )
    await session.commit()

    row = (await client.get("/api/repos/summary")).json()["repos"][0]
    assert row["average_health"] == 8.5


@pytest.mark.asyncio
async def test_never_analysed_repo_reports_null_health_not_zero(
    client: AsyncClient,
) -> None:
    """A missing score and a bad score must not render the same.

    Zero would read as "analysed, and terrible" on a dashboard row.
    """
    await _register(client, "fresh-registration")

    row = (await client.get("/api/repos/summary")).json()["repos"][0]

    assert row["average_health"] is None
    assert row["hotspot_health"] is None
    assert row["file_count"] == 0
    assert row["doc_page_count"] == 0


@pytest.mark.asyncio
async def test_figures_are_grouped_per_repo(client: AsyncClient, session) -> None:
    """One repo's rows must not leak into another's counts."""
    big = await _register(client, "big")
    small = await _register(client, "small")
    await _seed(session, big["id"], files=5)
    await _seed(session, small["id"], files=2)

    rows = {r["name"]: r for r in (await client.get("/api/repos/summary")).json()["repos"]}

    assert rows["big"]["file_count"] == 5
    assert rows["small"]["file_count"] == 2
    assert rows["big"]["tracked_file_count"] == 5
    assert rows["small"]["tracked_file_count"] == 2


@pytest.mark.asyncio
async def test_query_count_does_not_grow_with_repo_count(
    client: AsyncClient, session, test_engine
) -> None:
    """The whole point of the route.

    The shape it replaces issued six `/stats` queries plus a full
    `git_metadata` hydration *per repository*, across two sequential request
    waves. Grouped aggregates make the cost flat, and this is the only test
    that would notice a future edit reintroducing a per-repo query.
    """
    for i in range(2):
        repo = await _register(client, f"repo{i}")
        await _seed(session, repo["id"], files=3)

    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(test_engine.sync_engine, "before_cursor_execute", record)
    try:
        two_repos = len(await _count_queries(client, statements))
        statements.clear()
        for i in range(2, 6):
            repo = await _register(client, f"repo{i}")
            await _seed(session, repo["id"], files=3)
        statements.clear()
        six_repos = len(await _count_queries(client, statements))
    finally:
        event.remove(test_engine.sync_engine, "before_cursor_execute", record)

    assert six_repos == two_repos, (
        f"query count grew from {two_repos} (2 repos) to {six_repos} (6 repos) — "
        "something in the route is running per repository again"
    )
    # One repo list plus five grouped aggregates. Pinned so "flat" has a value:
    # a change that merges or splits an aggregate should be a deliberate edit
    # here, not a silent drift.
    assert six_repos == 6


async def _count_queries(client: AsyncClient, statements: list[str]) -> list[str]:
    statements.clear()
    resp = await client.get("/api/repos/summary")
    assert resp.status_code == 200
    return list(statements)
