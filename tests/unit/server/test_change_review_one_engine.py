"""The Review page, ``repowise risk`` and ``get_change_risk`` give one score and one test list.

Each commit of a small indexed repository is asked of all three surfaces: the
web route ``POST /blast-radius/change``, the CLI's ``--format json`` and the
MCP tool. The score fields and the tests must match, because all three call
``score_live_change`` and ``scored_change_tests`` / ``change_tests_block``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

_NOW = datetime(2026, 7, 18, tzinfo=UTC)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for relative_path, content in files.items():
        path = repo / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", relative_path)
    _git(repo, "-c", "user.name=Dev", "-c", "user.email=dev@example.com", "commit", "-qm", message)
    return _git(repo, "rev-parse", "HEAD")


def _history(repo: Path) -> list[str]:
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _commit(
        repo,
        {
            "src/status.py": "def status():\n    return 1\n",
            "src/util.py": "def util():\n    return 1\n",
            "tests/test_status.py": "from src.status import status\n",
            "tests/test_util.py": "from src.util import util\n",
            "docs/notes.md": "one\n",
        },
        "chore: seed",
    )
    return [
        _commit(repo, {"src/status.py": "def status():\n    return 2\n"}, "fix: status"),
        _commit(
            repo,
            {"src/util.py": "def util():\n    return 2\n", "docs/notes.md": "two\n"},
            "feat: util and notes",
        ),
        _commit(repo, {"docs/notes.md": "three\n"}, "docs: notes"),
        _commit(repo, {"tests/test_new.py": "def test_new():\n    pass\n"}, "test: new"),
        _commit(
            repo,
            {
                "src/status.py": "def status():\n    return 3\n",
                "src/util.py": "def util():\n    return 3\n",
            },
            "refactor: both",
        ),
    ]


async def _index(repo: Path, head: str):
    """The repo-local store ``repowise risk`` opens, with a small graph."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from repowise.core.persistence.database import init_db
    from repowise.core.persistence.models import GraphEdge, GraphNode, Repository

    db = repo / ".repowise" / "wiki.db"
    db.parent.mkdir()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db.as_posix()}", poolclass=NullPool)
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        s.add(
            Repository(
                id="repo1",
                name="repo",
                url="https://example.com/repo",
                local_path=str(repo),
                default_branch="main",
                settings_json="{}",
                head_commit=head,
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
        for path, is_test in (
            ("src/status.py", False),
            ("src/util.py", False),
            ("tests/test_status.py", True),
            ("tests/test_util.py", True),
        ):
            s.add(
                GraphNode(
                    repository_id="repo1",
                    node_id=path,
                    node_type="file",
                    is_test=is_test,
                    always_run_reason="" if is_test else None,
                )
            )
        for test, source in (
            ("tests/test_status.py", "src/status.py"),
            ("tests/test_util.py", "src/util.py"),
        ):
            s.add(
                GraphEdge(
                    repository_id="repo1",
                    source_node_id=test,
                    target_node_id=source,
                    edge_type="imports",
                )
            )
        await s.commit()
    return engine, factory


def _cli(repo: Path, sha: str) -> dict:
    from click.testing import CliRunner

    from repowise.cli.commands.risk_cmd.command import risk_command

    out = CliRunner().invoke(risk_command, [sha, "--path", str(repo), "--format", "json"])
    assert out.exit_code == 0, out.output
    return json.loads(out.output)


async def _web(factory, sha: str) -> dict:
    from httpx import ASGITransport, AsyncClient

    from tests.unit.server.conftest import _create_test_app

    app = _create_test_app()
    app.state.session_factory = factory
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            "/api/repos/repo1/blast-radius/change", json={"change_id": f"commit:{sha}"}
        )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _mcp(monkeypatch, repo: Path, factory, sha: str) -> dict:
    module = importlib.import_module("repowise.server.mcp_server.tool_change_risk")

    async def _context(_: str | None) -> SimpleNamespace:
        return SimpleNamespace(path=str(repo), session_factory=factory)

    monkeypatch.setattr(module, "_resolve_repo_context", _context)
    return await module.get_change_risk(sha, include=["tests", "diagnostics", "scales"])


def _present(block: dict) -> dict:
    """*block* without the fields the schema sends as ``null`` when a surface left them out."""
    return {k: v for k, v in block.items() if v is not None}


def test_web_cli_and_mcp_give_one_score_and_one_test_list(tmp_path, monkeypatch) -> None:
    repo = (tmp_path / "repo").resolve()
    shas = _history(repo)
    engine, factory = asyncio.run(_index(repo, shas[-1]))
    named = 0
    try:
        for sha in shas:
            review = asyncio.run(_web(factory, sha))["review"]
            cli = _cli(repo, sha)
            mcp = asyncio.run(_mcp(monkeypatch, repo, factory, sha))

            risk = review["risk"]
            assert {k: cli[k] for k in risk} == risk, sha
            for key in ("score", "risk_percentile", "review_priority", "drivers"):
                assert mcp[key] == risk[key], (sha, key)

            tests = review["tests"]
            assert _present(tests) == _present(cli["impacted_tests"]), sha
            shown = mcp["impacted_tests"]
            for key in ("status", "run_all", "basis", "total"):
                assert shown[key] == tests[key], (sha, key)
            assert shown["tests_to_run"] == tests["tests_to_run"][:10], sha
            named += bool(tests["tests_to_run"])
    finally:
        asyncio.run(engine.dispose())
    # The comparison holds for changes that name tests, not only for empty ones.
    assert named >= 2


@pytest.mark.asyncio
async def test_the_review_is_absent_with_its_reason_when_git_cannot_read_the_change(
    client, tmp_path
):
    from tests.unit.server.conftest import create_test_repo

    repo = await create_test_repo(client, tmp_path)
    # A checkout whose git is empty: the change resolves, the score cannot be read.
    resp = await client.post(
        f"/api/repos/{repo['id']}/blast-radius/change", json={"change_id": "working-tree"}
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["review"] is None
    assert body["review_unavailable"]
