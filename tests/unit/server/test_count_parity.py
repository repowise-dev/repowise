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
from repowise.core.persistence.models import GraphNode
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
