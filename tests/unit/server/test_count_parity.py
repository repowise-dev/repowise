"""One number per concept: overview, stats and module health agree.

Languages come from one split (code apart from docs/config) and module counts
from one axis (the top-level directory module health buckets on), so no two
surfaces can print different figures for the same repository.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from repowise.core.persistence import crud
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GraphNode, HealthFileMetric
from repowise.core.stats_highlights import file_mix
from repowise.server.routers.stats import stats_highlights
from tests.unit.server.conftest import create_test_repo

# Two code modules plus root, a docs-only directory and a config file.
_FILES = (
    ("src/app.py", "python"),
    ("src/util.py", "python"),
    ("web/index.ts", "typescript"),
    ("setup.py", "python"),
    ("docs/guide.md", "markdown"),
    ("config.yaml", "yaml"),
)


async def _seed(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for path, language in _FILES:
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=path,
                    node_type="file",
                    language=language,
                    is_test=False,
                )
            )
            is_code = language in {"python", "typescript"}
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                commit_count_total=3,
                primary_owner_name="Alice",
                primary_owner_email="alice@example.com",
                history_only=not is_code,
            )
        await session.commit()


async def _surfaces(client: AsyncClient, app) -> tuple[dict, dict, dict]:
    """The overview payload, the Stats scale block and the module health list."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])
    overview = (await client.get(f"/api/repos/{repo['id']}/overview-summary")).json()
    modules = (await client.get(f"/api/repos/{repo['id']}/modules/health")).json()
    async with get_session(app.state.session_factory) as session:
        scale = (await stats_highlights(repo["id"], session))["scale"]
    return overview, scale, modules


@pytest.mark.asyncio
async def test_overview_stats_language_parity(client: AsyncClient, app) -> None:
    overview, scale, _ = await _surfaces(client, app)

    assert overview["languages"] == scale["languages"]
    assert overview["docs_config_languages"] == scale["docs_config_languages"]
    assert [row["language"] for row in scale["languages"]] == ["python", "typescript"]
    assert {row["language"] for row in scale["docs_config_languages"]} == {"markdown", "yaml"}
    assert scale["language_count"] == 2


@pytest.mark.asyncio
async def test_module_count_matches_modules_health(client: AsyncClient, app) -> None:
    overview, scale, modules = await _surfaces(client, app)

    assert {m["module_path"] for m in modules["items"]} == {"src", "web", "root"}
    assert overview["stats"]["module_count"] == scale["module_count"] == modules["total"] == 3


def test_file_mix_splits_code_from_docs_config() -> None:
    mix = file_mix(
        [
            {"node_id": "lib/Main.hs", "language": "haskell"},
            {"node_id": "lib/Util.hs", "language": "haskell"},
            {"node_id": "README.md", "language": "markdown"},
            {"node_id": "data.weird", "language": "not-a-language"},
            {"node_id": "external:base", "language": "external"},
            {"node_id": "unparsed", "language": None},
        ]
    )
    # A language with no AST parser is still what the project is written in.
    assert mix["languages"] == [{"language": "haskell", "file_count": 2}]
    assert mix["docs_config_languages"] == [
        {"language": "markdown", "file_count": 1},
        {"language": "not-a-language", "file_count": 1},
    ]
    assert mix["module_count"] == 1


@pytest.mark.asyncio
async def test_files_index_labels_loc_as_nloc(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"])

    body = (await client.get(f"/api/repos/{repo['id']}/files")).json()
    assert body["loc_unit"] == "nloc"
    assert body["loc_null_reason"]
    assert all(row["loc"] is None for row in body["files"])


# A production hotspot, a test-origin one and a generated one, all in one
# community under ``src/`` (#6266). The hotspot list shows only the first.
_HOTSPOTS = (
    ("src/core.py", "production"),
    ("src/test_core.py", "test"),
    ("src/core_pb2.py", "generated"),
)


async def _seed_hotspots(session_factory, repo_id: str) -> None:
    async with get_session(session_factory) as session:
        for rank, (path, origin) in enumerate(_HOTSPOTS):
            session.add(
                GraphNode(
                    repository_id=repo_id,
                    node_id=path,
                    node_type="file",
                    language="python",
                    is_test=origin == "test",
                    community_id=0,
                    pagerank=0.5 - rank / 10,
                )
            )
            await crud.upsert_git_metadata(
                session,
                repository_id=repo_id,
                file_path=path,
                commit_count_total=30,
                primary_owner_name="Alice",
                is_hotspot=True,
                churn_percentile=0.9,
                temporal_hotspot_score=9.0 - rank,
            )
            session.add(
                HealthFileMetric(
                    repository_id=repo_id,
                    file_path=path,
                    score=7.0,
                    nloc=10,
                    is_test=origin == "test",
                    code_origin=origin,
                )
            )
        await session.commit()


@pytest.mark.asyncio
async def test_every_hotspot_count_matches_the_hotspot_list(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    rid = repo["id"]
    await _seed_hotspots(app.state.session_factory, rid)

    async def get(path: str, **params) -> dict:
        resp = await client.get(path, params=params)
        assert resp.status_code == 200, resp.text
        return resp.json()

    listed = await get(f"/api/repos/{rid}/hotspots", hold="map")
    assert [r["file_path"] for r in listed["items"]] == ["src/core.py"]
    shown = listed["total"]
    assert shown == 1

    modules = await get(f"/api/repos/{rid}/modules/health")
    assert sum(m["hotspot_count"] for m in modules["items"]) == shown
    module = await get(f"/api/repos/{rid}/modules/health/src")
    assert module["hotspot_count"] == shown
    assert module["top_hotspots"] == ["src/core.py"]

    tree = await get(f"/api/repos/{rid}/areas/tree")
    assert tree["root"]["hotspot_count"] == shown
    area = await get(f"/api/repos/{rid}/areas/src")
    assert area["area"]["hotspot_count"] == area["hotspots"]["total"] == shown

    graph = await get(f"/api/graph/{rid}")
    assert graph["hot_total"] == graph["hot_in_view"] == shown
    module_graph = await get(f"/api/graph/{rid}/modules")
    assert sum(m["hotspot_count"] for m in module_graph["nodes"]) == shown
    community = await get(f"/api/graph/{rid}/communities/0")
    assert community["hot_count"] == shown
    architecture = await get(f"/api/graph/{rid}/architecture", min_members=1)
    assert sum(n["hotspot_count"] for n in architecture["nodes"]) == shown
