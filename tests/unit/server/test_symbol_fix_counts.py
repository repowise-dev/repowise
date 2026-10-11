"""Symbol-level bug-fix counts on /api/symbols and /api/symbols/detail.

The counts come from the per-file rollup (`git_metadata.fix_symbol_counts_json`),
not from a scan of `fix_events`. The rollup already stores exactly this number,
recomputed on every index and update.

`None` means the file has no git history row at all. It is deliberately NOT a
"the rollup has not run yet" signal: the column stores `"{}"` both before the
rollup and after a run that found nothing, so a tracked file always reports a
real `0`. Claiming otherwise would be a distinction the storage cannot back.

The `bug_fixed` filter is symbol-level, unlike the file-level `in_hot_files`
beside it: a bug-fixed file says little about the one function you are reading.
"""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import GitMetadata, WikiSymbol, _new_uuid
from tests.unit.server.conftest import create_test_repo

_PATH = "src/pipeline.py"


async def _seed(session_factory, repo_id: str, *, fix_counts: dict) -> None:
    """Two symbols in one file; only ``run`` has been bug-fixed."""
    async with get_session(session_factory) as session:
        for name, start in (("run", 1), ("load", 40)):
            session.add(
                WikiSymbol(
                    id=_new_uuid(),
                    repository_id=repo_id,
                    file_path=_PATH,
                    symbol_id=f"{_PATH}::{name}",
                    name=name,
                    qualified_name=f"pipeline.{name}",
                    kind="function",
                    start_line=start,
                    end_line=start + 20,
                    visibility="public",
                    language="python",
                )
            )
        session.add(
            GitMetadata(
                id=_new_uuid(),
                repository_id=repo_id,
                file_path=_PATH,
                fix_symbol_counts_json=json.dumps(fix_counts),
            )
        )


@pytest.mark.asyncio
async def test_list_carries_per_symbol_fix_counts(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], fix_counts={f"{_PATH}::run": 3})

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"]})
    assert resp.status_code == 200
    by_name = {s["name"]: s for s in resp.json()["items"]}

    assert by_name["run"]["fix_count"] == 3
    # Rolled up, no fixes here: a real 0, not "unknown".
    assert by_name["load"]["fix_count"] == 0


@pytest.mark.asyncio
async def test_file_without_git_history_reports_unknown(client: AsyncClient, app) -> None:
    """No git_metadata row is the one genuine "we do not know"."""
    repo = await create_test_repo(client)
    async with get_session(app.state.session_factory) as session:
        session.add(
            WikiSymbol(
                id=_new_uuid(),
                repository_id=repo["id"],
                file_path="untracked.py",
                symbol_id="untracked.py::helper",
                name="helper",
                qualified_name="untracked.helper",
                kind="function",
                start_line=1,
                end_line=5,
                visibility="public",
                language="python",
            )
        )

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"]})
    assert all(s["fix_count"] is None for s in resp.json()["items"])


@pytest.mark.asyncio
async def test_empty_rollup_reports_zero_not_unknown(client: AsyncClient, app) -> None:
    """A tracked file whose rollup found nothing reports 0, not None."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], fix_counts={})

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"]})
    assert all(s["fix_count"] == 0 for s in resp.json()["items"])


@pytest.mark.asyncio
async def test_bug_fixed_filter_is_symbol_level(client: AsyncClient, app) -> None:
    """Both symbols share a bug-fixed file; only the fixed one comes back."""
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], fix_counts={f"{_PATH}::run": 3})

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"], "bug_fixed": "true"})
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["total"] == 1
    assert [s["name"] for s in payload["items"]] == ["run"]


@pytest.mark.asyncio
async def test_bug_fixed_filter_returns_nothing_without_a_rollup(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], fix_counts={})

    resp = await client.get("/api/symbols", params={"repo_id": repo["id"], "bug_fixed": "true"})
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_detail_carries_the_count_without_an_extra_query(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _seed(app.state.session_factory, repo["id"], fix_counts={f"{_PATH}::run": 3})

    resp = await client.get(
        "/api/symbols/detail",
        params={"repo_id": repo["id"], "symbol_id": f"{_PATH}::run"},
    )
    assert resp.status_code == 200
    assert resp.json()["fix_count"] == 3
