"""Tests for the security findings endpoint.

The endpoint had no tests at all, which is how it shipped serving a snippet
with no line number for its whole life. These pin the line contract: a line is
served only when it was checked against the live file, corrected when the code
moved, and withdrawn when the code is gone — never handed over as a guess
dressed as fact.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from httpx import AsyncClient

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import SecurityFinding
from tests.unit.server.conftest import create_test_repo


async def _insert(session_factory, repo_id: str, **kw) -> None:
    async with get_session(session_factory) as session:
        session.add(
            SecurityFinding(
                repository_id=repo_id,
                file_path=kw.get("file_path", "app.py"),
                kind=kw.get("kind", "hardcoded_secret"),
                severity=kw.get("severity", "high"),
                snippet=kw.get("snippet"),
                line_number=kw.get("line_number"),
                commit_sha=kw.get("commit_sha", ""),
                commit_at=kw.get("commit_at"),
                detected_at=datetime.now(UTC),
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_list_security_empty(client: AsyncClient) -> None:
    repo = await create_test_repo(client)
    resp = await client.get(f"/api/repos/{repo['id']}/security")
    assert resp.status_code == 200
    assert resp.json() == []


@pytest.mark.asyncio
async def test_line_number_verified_against_live_file(client: AsyncClient, app) -> None:
    """The stored line still holds, so it is served verified."""
    repo = await create_test_repo(client)
    src = Path(repo["local_path"]) / "app.py"
    src.write_text("import os\npassword = 'letmein'\nprint(1)\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        snippet="password = 'letmein'",
        line_number=2,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_number"] == 2
    assert row["line_verified"] is True


@pytest.mark.asyncio
async def test_line_number_corrected_when_code_moved(client: AsyncClient, app) -> None:
    """Lines inserted above the finding move it; the served line follows."""
    repo = await create_test_repo(client)
    src = Path(repo["local_path"]) / "app.py"
    src.write_text("# new\n# header\n# lines\nimport os\npassword = 'letmein'\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        snippet="password = 'letmein'",
        line_number=2,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_number"] == 5, "the line should follow the code, not stay stale"
    assert row["line_verified"] is True


@pytest.mark.asyncio
async def test_line_withdrawn_when_code_is_gone(client: AsyncClient, app) -> None:
    """No line at all beats a line pointing at innocent code."""
    repo = await create_test_repo(client)
    src = Path(repo["local_path"]) / "app.py"
    src.write_text("import os\nprint('all clean now')\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        snippet="password = 'letmein'",
        line_number=2,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_number"] is None
    assert row["line_verified"] is False


@pytest.mark.asyncio
async def test_ambiguous_line_is_not_claimed_verified(client: AsyncClient, app) -> None:
    """A snippet that recurs cannot be pinned, so it is served unverified."""
    repo = await create_test_repo(client)
    src = Path(repo["local_path"]) / "app.py"
    src.write_text("eval(x)\nprint(1)\neval(x)\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        kind="eval_call",
        snippet="eval(x)",
        line_number=99,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_number"] is not None
    assert row["line_verified"] is False


@pytest.mark.asyncio
async def test_symbol_name_finding_is_never_relocated_or_withdrawn(
    client: AsyncClient, app
) -> None:
    """A symbol-name snippet is a bare identifier, not the text of its line.

    Relocating on it lands on the first coincidental use, and failing to find
    it does not mean the symbol is gone — so it is checked in place only.
    """
    repo = await create_test_repo(client)
    src = Path(repo["local_path"]) / "app.py"
    src.write_text("token = 1\nuse(token)\nuse(token)\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        kind="security_sensitive_symbol",
        severity="low",
        snippet="token",
        line_number=42,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_number"] == 42, "must not be relocated onto a coincidental use"
    assert row["line_verified"] is False


@pytest.mark.asyncio
async def test_path_outside_the_repo_is_not_read(client: AsyncClient, app) -> None:
    """A finding path is not a licence to read anywhere on disk."""
    repo = await create_test_repo(client)
    await _insert(
        app.state.session_factory,
        repo["id"],
        file_path="../../../../etc/passwd",
        snippet="root:",
        line_number=1,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    # Unreadable, so the line degrades — it must never come back verified.
    assert row["line_verified"] is False


@pytest.mark.asyncio
async def test_credential_directories_are_not_read(client: AsyncClient, app) -> None:
    """`.repowise/.env` and `.git/config` live inside the root; both stay shut."""
    repo = await create_test_repo(client)
    secrets = Path(repo["local_path"]) / ".repowise"
    secrets.mkdir(exist_ok=True)
    (secrets / ".env").write_text("OPENAI_API_KEY=sk-real-key\n")

    await _insert(
        app.state.session_factory,
        repo["id"],
        file_path=".repowise/.env",
        snippet="OPENAI_API_KEY=sk-real-key",
        line_number=1,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["line_verified"] is False


@pytest.mark.asyncio
async def test_history_finding_carries_commit_date(client: AsyncClient, app) -> None:
    """A secret found in history says when it landed, not just that it did."""
    repo = await create_test_repo(client)
    committed = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    await _insert(
        app.state.session_factory,
        repo["id"],
        snippet="password = 'old'",
        line_number=1,
        commit_sha="abc123def456",
        commit_at=committed,
    )

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["found_in_history"] is True
    assert row["commit_sha"] == "abc123def456"
    assert row["commit_at"] is not None
    assert row["commit_at"].startswith("2026-03-01")


@pytest.mark.asyncio
async def test_working_tree_finding_has_no_commit_date(client: AsyncClient, app) -> None:
    repo = await create_test_repo(client)
    await _insert(app.state.session_factory, repo["id"], snippet="x", line_number=1)

    row = (await client.get(f"/api/repos/{repo['id']}/security")).json()[0]
    assert row["found_in_history"] is False
    assert row["commit_at"] is None
