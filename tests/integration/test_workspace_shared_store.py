"""A workspace MCP server over one configured database (``REPOWISE_DB_URL``).

Two repositories indexed into one store, declared as one workspace: every
member is served from that store, scoped to its own repository, and a token
limited to one member cannot read the other. Runs on a shared SQLite file, and
on PostgreSQL too when a scratch database is configured::

    REPOWISE_TEST_PG_URL=postgresql+asyncpg://user@localhost:5432/repowise_test \\
        uv run pytest tests/integration/test_workspace_shared_store.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from repowise.server.auth import Principal
from repowise.server.mcp_server import _server, _state

PG_URL = os.environ.get("REPOWISE_TEST_PG_URL")
_INIT = (sys.executable, "-m", "repowise.cli.main", "init")

_FILES = {
    "code": {
        "src/billing.py": (
            "def compute_invoice_total(items):\n"
            '    """Sum the line amounts of an invoice."""\n'
            "    return sum(item.amount for item in items)\n"
        ),
    },
    "docs": {
        "confluence/ENG/release-process.md": (
            "# Release process\n\n"
            "The release freeze window opens every second Thursday.\n"
        ),
    },
}


def _repo(root: Path, name: str) -> Path:
    path = root / name
    for rel, text in _FILES[name].items():
        (path / rel).parent.mkdir(parents=True, exist_ok=True)
        (path / rel).write_text(text, encoding="utf-8")
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@e.x",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@e.x",
    }
    for args in (["init", "-q"], ["add", "."], ["commit", "-qm", "seed"]):
        subprocess.run(["git", *args], cwd=path, env=env, check=True, capture_output=True)
    return path


def _empty_pg() -> str:
    database = (PG_URL or "").rsplit("/", 1)[-1].split("?")[0]
    if "test" not in database and "scratch" not in database:
        pytest.skip(f"refusing to drop tables in {database!r}: name it *test* or *scratch*")
    import asyncio

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    async def drop() -> None:
        engine = create_async_engine(PG_URL or "")
        async with engine.begin() as conn:
            await conn.execute(text("DROP SCHEMA public CASCADE"))
            await conn.execute(text("CREATE SCHEMA public"))
        await engine.dispose()

    asyncio.run(drop())
    return PG_URL or ""


@pytest.fixture(
    scope="module",
    params=[
        "sqlite",
        pytest.param(
            "postgresql",
            marks=pytest.mark.skipif(not PG_URL, reason="REPOWISE_TEST_PG_URL not set"),
        ),
    ],
)
def indexed_store(request, tmp_path_factory):
    """``code`` and ``docs`` indexed into one store, declared as one workspace.

    Indexed once per store: ``(store url, checkout paths)``.
    """
    tmp_path = tmp_path_factory.mktemp(request.param)
    if request.param == "sqlite":
        store = tmp_path / "store" / "wiki.db"
        store.parent.mkdir()
        url = f"sqlite+aiosqlite:///{store.as_posix()}"
    else:
        url = _empty_pg()
    root = tmp_path / "ws"
    paths = {name: _repo(root, name) for name in ("code", "docs")}
    env = {k: v for k, v in os.environ.items() if k != "REPOWISE_DATABASE_URL"}
    env.update(REPOWISE_DB_URL=url, REPOWISE_EMBEDDER="mock", PYTHONIOENCODING="utf-8")
    for path in paths.values():
        # A process of its own, as the indexer runs beside a server.
        result = subprocess.run(
            [*_INIT, str(path), "--no-prose", "--yes", "--no-editor-setup"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        assert result.returncode == 0, result.stdout + result.stderr
    (root / ".repowise-workspace.yaml").write_text(
        "version: 1\ndefault_repo: code\nrepos:\n"
        "  - {path: code, alias: code}\n  - {path: docs, alias: docs}\n",
        encoding="utf-8",
    )
    return url, paths


@pytest.fixture
def shared_workspace(indexed_store, monkeypatch):
    """A workspace server's environment over :func:`indexed_store`."""
    url, paths = indexed_store
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setenv("REPOWISE_DB_URL", url)
    monkeypatch.setenv("REPOWISE_EMBEDDER", "mock")
    monkeypatch.setattr(_state, "_repo_path", str(paths["code"]))
    monkeypatch.setattr(_state, "_force_single_repo", False)
    return paths


def _text(result) -> str:
    return json.dumps(result, default=str)


async def test_one_store_serves_every_member_scoped_to_its_repo(shared_workspace) -> None:
    from repowise.server.mcp_server import get_answer, get_context, list_repos, search_codebase
    from repowise.server.mcp_server._failure_shield import shield

    async with _server._lifespan(_server.mcp):
        assert _state._registry is not None
        listed = await shield(list_repos)()
        assert [r["alias"] for r in listed["repos"]] == ["code", "docs"]
        assert listed["default_repo"] == "code"

        # The default repo answers a call without ``repo``.
        code = _text(await shield(search_codebase)(query="compute_invoice_total"))
        assert "src/billing.py" in code
        assert "release-process" not in code

        # Each member is reached by ``repo``, and only its own rows answer.
        docs = _text(await shield(search_codebase)(query="freeze", repo="docs"))
        assert "confluence/ENG/release-process.md" in docs
        assert "billing" not in docs
        crossed = _text(await shield(search_codebase)(query="compute_invoice_total", repo="docs"))
        assert "src/billing.py" not in crossed

        context = _text(await shield(get_context)(targets=["src/billing.py"]))
        assert "compute_invoice_total" in context
        symbol = _text(await shield(get_context)(targets=["compute_invoice_total"]))
        assert "src/billing.py" in symbol
        page = _text(
            await shield(get_context)(targets=["confluence/ENG/release-process.md"], repo="docs")
        )
        assert "Release process" in page or "release-process" in page
        assert "billing" not in page

        answer = _text(await shield(get_answer)(question="How is an invoice total computed?"))
        assert "src/billing.py" in answer
        assert "release-process" not in answer
        answer = _text(
            await shield(get_answer)(question="When does the release freeze window open?", repo="docs")
        )
        assert "billing" not in answer

    # Served from the one store: no member grew a store of its own.
    assert not any((p / ".repowise" / "wiki.db").exists() for p in shared_workspace.values())


async def test_a_token_scoped_to_one_member_cannot_read_the_other(
    shared_workspace, monkeypatch
) -> None:
    from mcp.server.fastmcp.exceptions import ToolError

    from repowise.server.mcp_server import ensure_full_surface

    server = ensure_full_surface()
    async with _server._lifespan(server):
        code_id = _state._registry.repo_id("code")
        assert code_id is not None
        # By alias (a tokens file) and by repository id (an org's repo list).
        for scope in ({"code"}, {code_id}):
            principal = Principal("token:t", "token", frozenset(scope), None)
            monkeypatch.setattr(server, "_request_principal", lambda p=principal: p)
            await server.call_tool("search_codebase", {"query": "compute_invoice_total"})
            await server.call_tool(
                "search_codebase", {"query": "compute_invoice_total", "repo": "code"}
            )
            with pytest.raises(ToolError, match="outside this credential's scope"):
                await server.call_tool("search_codebase", {"query": "freeze", "repo": "docs"})
