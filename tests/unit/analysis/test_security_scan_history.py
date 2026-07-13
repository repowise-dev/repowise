"""Unit tests for security scanning: working-tree persist + full-history scan.

Covers the maintainer's review points for issue #818:
* idempotent re-runs via the unique provenance constraint (no duplicate rows);
* secret-oriented gating for history mode (code smells excluded by default);
* per-row failure isolation (``continue`` rather than aborting the batch);
* unique-blob dedup so identical content is scanned once.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from repowise.core.analysis.history_scan import HistorySecurityScanner
from repowise.core.analysis.security_scan import (
    SECRET_KINDS,
    SecurityScanner,
)
from repowise.core.persistence.models import Base


@pytest.fixture
async def session() -> AsyncSession:
    """In-memory SQLite session with the full schema (incl. unique constraint)."""
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
        engine, expire_on_commit=False, class_=AsyncSession
    )
    async with factory() as s:
        yield s
    await engine.dispose()


def _findings() -> list[dict]:
    return [
        {"kind": "hardcoded_password", "severity": "high", "snippet": "password='x'", "line": 1},
        {"kind": "eval_call", "severity": "high", "snippet": "eval(x)", "line": 2},
    ]


async def test_persist_uses_insert_ignore_and_counts_inserted(session: AsyncSession) -> None:
    """Non-duplicate rows are inserted; insert count reflects actual writes."""
    scanner = SecurityScanner(session, "repo-1")
    inserted = await scanner.persist("a.py", _findings())
    assert inserted == 2


async def test_persist_is_idempotent_on_rerun(session: AsyncSession) -> None:
    """Re-running the same findings does not create duplicate rows."""
    scanner = SecurityScanner(session, "repo-1")
    first = await scanner.persist("a.py", _findings())
    second = await scanner.persist("a.py", _findings())
    assert first == 2
    # Same provenance (no commit_sha -> "" key) -> nothing new inserted.
    assert second == 0
    rows = (await session.execute(Base.metadata.tables["security_findings"].select())).all()
    assert len(rows) == 2


async def test_persist_continues_past_row_failure(session: AsyncSession) -> None:
    """A bad row is skipped (continue) and the rest still insert.

    We force a failure by inserting a row whose ``severity`` is too long for the
    String(20) column on SQLite (which is lenient) — instead we exercise the
    exception path by monkeypatching execute to raise once. This proves the loop
    isolates failures rather than aborting the batch.
    """
    scanner = SecurityScanner(session, "repo-1")
    real_execute = session.execute

    calls = {"n": 0}

    async def _boom(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated DB error on first row")
        return await real_execute(*args, **kwargs)

    session.execute = _boom  # type: ignore[assignment]
    inserted = await scanner.persist("a.py", _findings())
    # One row failed, one succeeded -> at least 1 inserted, no crash.
    assert inserted >= 1


async def test_history_gate_excludes_code_smells_by_default(session: AsyncSession) -> None:
    """History mode keeps only secret kinds when secrets_only (default) is set."""
    assert HistorySecurityScanner._passes_gate("hardcoded_password", secrets_only=True)
    assert HistorySecurityScanner._passes_gate("hardcoded_secret", secrets_only=True)
    assert not HistorySecurityScanner._passes_gate("eval_call", secrets_only=True)
    assert not HistorySecurityScanner._passes_gate("os_system", secrets_only=True)
    # With secrets_only=False the full registry is reported.
    assert HistorySecurityScanner._passes_gate("eval_call", secrets_only=False)


async def test_history_secrets_only_scan_skips_non_secret_patterns(session: AsyncSession) -> None:
    """scan_history with defaults only persists secret findings."""
    content = "password = 'hunter2'\neval(open('x'))\nos.system('ls')\n"
    scanner = HistorySecurityScanner(session, "repo-1")
    # Stub the git layer: a single blob introduced at a commit.
    scanner._list_commits = lambda *a, **k: [("abc123", "2026-01-01T00:00:00+00:00")]  # type: ignore[assignment]
    scanner._unique_blobs = lambda *a, **k: {"blob1": "src/secret.py"}  # type: ignore[assignment]
    scanner._read_blob = lambda *a, **k: content  # type: ignore[assignment]
    scanner._blobs_in_commit = lambda *a, **k: ["blob1"]  # type: ignore[assignment]
    scanner._is_source = staticmethod(lambda p: True)  # type: ignore[assignment]

    summary = await scanner.scan_history(Path("/tmp/repo"), secrets_only=True)
    # Only hardcoded_password survives the gate.
    assert summary.findings_inserted == 1
    assert summary.by_kind == {"hardcoded_password": 1}
    assert summary.by_severity == {"high": 1}

    row = (await session.execute(Base.metadata.tables["security_findings"].select())).first()
    assert row is not None
    assert row._mapping["commit_sha"] == "abc123"


async def test_history_unique_blob_scanned_once(session: AsyncSession) -> None:
    """Identical content across two commits is scanned once, attributed to first."""
    content = "api_key = 'LEAKED'\n"
    scanner = HistorySecurityScanner(session, "repo-1")
    reads = {"n": 0}

    scanner._list_commits = lambda *a, **k: [  # type: ignore[assignment]
        ("c1", "2026-01-01T00:00:00+00:00"),
        ("c2", "2026-02-01T00:00:00+00:00"),
    ]
    scanner._unique_blobs = lambda *a, **k: {"blob1": "src/key.py"}  # type: ignore[assignment]

    def _read(*a, **k):
        reads["n"] += 1
        return content

    scanner._read_blob = _read  # type: ignore[assignment]
    scanner._blobs_in_commit = lambda *a, **k: ["blob1"]  # type: ignore[assignment]
    scanner._is_source = staticmethod(lambda p: True)  # type: ignore[assignment]

    summary = await scanner.scan_history(Path("/tmp/repo"), secrets_only=True)
    # Blob read exactly once despite being in two commits.
    assert reads["n"] == 1
    # Attributed to the first-introducing commit.
    assert summary.by_kind == {"hardcoded_secret": 1}
    row = (await session.execute(Base.metadata.tables["security_findings"].select())).first()
    assert row._mapping["commit_sha"] == "c1"


def test_secret_kinds_are_the_two_secret_patterns() -> None:
    """Guard against the registry drifting away from the history gate."""
    assert {"hardcoded_password", "hardcoded_secret"} == SECRET_KINDS
