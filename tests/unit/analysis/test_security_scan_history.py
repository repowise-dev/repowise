"""Unit tests for security scanning: working-tree persist + full-history scan.

Covers the maintainer's review points for issue #818:
* idempotent re-runs via the unique provenance constraint (no duplicate rows);
* secret-oriented gating for history mode (code smells excluded by default);
* per-row failure isolation (``continue`` rather than aborting the batch);
* unique-blob dedup so identical content is scanned once;
* alembic migration runs on SQLite;
* Postgres persist SQL places ``ON CONFLICT`` after ``VALUES``.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text
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
    """A bad row is skipped (continue) and the rest still insert."""
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
    assert inserted >= 1


async def test_persist_postgres_sql_places_on_conflict_after_values() -> None:
    """Postgres branch must emit valid INSERT ... VALUES ... ON CONFLICT SQL."""
    captured: list[str] = []

    async def _capture(stmt, params=None):
        captured.append(str(stmt))
        result = MagicMock()
        result.rowcount = 1
        return result

    mock_session = MagicMock()
    mock_session.execute = AsyncMock(side_effect=_capture)
    mock_session.bind.dialect.name = "postgresql"

    scanner = SecurityScanner(mock_session, "repo-1")
    await scanner.persist("a.py", [_findings()[0]])

    assert len(captured) == 1
    sql = captured[0]
    assert "INSERT INTO security_findings" in sql
    assert "VALUES" in sql
    assert "ON CONFLICT ON CONSTRAINT uq_security_finding_provenance DO NOTHING" in sql
    assert sql.index("VALUES") < sql.index("ON CONFLICT")


def test_migration_0041_upgrades_sqlite() -> None:
    """Migration 0041 must run on SQLite (batch_alter_table, not raw ALTER)."""
    core_root = Path("packages/core")
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "test.db"
        url = f"sqlite+aiosqlite:///{db_path}"
        prev_url = os.environ.get("DATABASE_URL")
        os.environ["DATABASE_URL"] = url
        prev_cwd = Path.cwd()
        try:
            os.chdir(core_root)
            alembic_cfg = Config("alembic.ini")
            alembic_cfg.set_main_option("sqlalchemy.url", url)
            # Alembic's env.py calls fileConfig(), which resets stdlib logging
            # and breaks pytest caplog for later tests on Linux CI.
            with patch("logging.config.fileConfig"):
                command.upgrade(alembic_cfg, "0040")
                command.upgrade(alembic_cfg, "0041")
        finally:
            os.chdir(prev_cwd)
            if prev_url is None:
                os.environ.pop("DATABASE_URL", None)
            else:
                os.environ["DATABASE_URL"] = prev_url

        engine = create_async_engine(url, connect_args={"check_same_thread": False})

        async def _verify() -> None:
            async with engine.connect() as conn:
                cols = await conn.run_sync(
                    lambda sync_conn: {
                        c["name"] for c in inspect(sync_conn).get_columns("security_findings")
                    }
                )
                assert "commit_sha" in cols
                assert "commit_at" in cols
                uqs = await conn.run_sync(
                    lambda sync_conn: inspect(sync_conn).get_unique_constraints("security_findings")
                )
                assert any(uq.get("name") == "uq_security_finding_provenance" for uq in uqs)

        import asyncio

        asyncio.run(_verify())
        asyncio.run(engine.dispose())


async def test_history_gate_excludes_code_smells_by_default(session: AsyncSession) -> None:
    """History mode keeps only secret kinds when secrets_only (default) is set."""
    assert HistorySecurityScanner._passes_gate("hardcoded_password", secrets_only=True)
    assert HistorySecurityScanner._passes_gate("hardcoded_secret", secrets_only=True)
    assert not HistorySecurityScanner._passes_gate("eval_call", secrets_only=True)
    assert not HistorySecurityScanner._passes_gate("os_system", secrets_only=True)
    assert HistorySecurityScanner._passes_gate("eval_call", secrets_only=False)


async def test_history_secrets_only_scan_skips_non_secret_patterns(session: AsyncSession) -> None:
    """scan_history with defaults only persists secret findings."""
    content = "password = 'hunter2'\neval(open('x'))\nos.system('ls')\n"
    scanner = HistorySecurityScanner(session, "repo-1")
    scanner._list_commits = lambda *a, **k: [("abc123", "2026-01-01T00:00:00+00:00")]  # type: ignore[assignment]
    scanner._unique_blobs = lambda *a, **k: {"blob1": "src/secret.py"}  # type: ignore[assignment]
    scanner._blob_introductions = lambda *a, **k: (  # type: ignore[assignment]
        {"blob1": "abc123"},
        {"abc123": "2026-01-01T00:00:00+00:00"},
    )
    scanner._read_blobs_batch = lambda *a, **k: {"blob1": content}  # type: ignore[assignment]
    scanner._is_source = staticmethod(lambda p: True)  # type: ignore[assignment]

    summary = await scanner.scan_history(Path("/tmp/repo"), secrets_only=True)
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
    scanner._blob_introductions = lambda *a, **k: (  # type: ignore[assignment]
        {"blob1": "c1"},
        {
            "c1": "2026-01-01T00:00:00+00:00",
            "c2": "2026-02-01T00:00:00+00:00",
        },
    )

    def _read(*a, **k):
        reads["n"] += 1
        return {"blob1": content}

    scanner._read_blobs_batch = _read  # type: ignore[assignment]
    scanner._is_source = staticmethod(lambda p: True)  # type: ignore[assignment]

    summary = await scanner.scan_history(Path("/tmp/repo"), secrets_only=True)
    assert reads["n"] == 1
    assert summary.by_kind == {"hardcoded_secret": 1}
    row = (await session.execute(Base.metadata.tables["security_findings"].select())).first()
    assert row._mapping["commit_sha"] == "c1"


async def test_history_progress_fires_for_clean_repo(session: AsyncSession) -> None:
    """Progress callbacks fire even when no findings are kept."""
    scanner = HistorySecurityScanner(session, "repo-1")
    scanner._list_commits = lambda *a, **k: [("abc123", "2026-01-01T00:00:00+00:00")]  # type: ignore[assignment]
    scanner._unique_blobs = lambda *a, **k: {"blob1": "README.md"}  # type: ignore[assignment]
    scanner._blob_introductions = lambda *a, **k: ({}, {})  # type: ignore[assignment]
    scanner._read_blobs_batch = lambda *a, **k: {"blob1": "# docs\n"}  # type: ignore[assignment]
    scanner._is_source = staticmethod(lambda p: True)  # type: ignore[assignment]

    messages: list[str] = []
    summary = await scanner.scan_history(
        Path("/tmp/repo"), secrets_only=True, progress=messages.append
    )
    assert summary.findings_inserted == 0
    assert messages == ["scanned blob 1/1"]
    assert summary.files_scanned == 1


def test_secret_kinds_are_the_two_secret_patterns() -> None:
    """Guard against the registry drifting away from the history gate."""
    assert {"hardcoded_password", "hardcoded_secret"} == SECRET_KINDS


# ---------------------------------------------------------------------------
# End-to-end against a real git repo.
#
# Every test above stubs `_is_source` to `lambda p: True` and `_blob_
# introductions` to a literal, which is precisely why two bugs lived here
# undetected for the feature's whole life: `_is_source` stripped the leading
# dot off a suffix while EXTENSION_TO_LANGUAGE is keyed with it, so no blob was
# ever scanned; and `_blob_introductions` keyed the *pre-image* blob from an
# abbreviated `--raw` SHA, so no lookup could ever match and every history
# finding was written with an empty commit. These run the real helpers.
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    import subprocess

    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture
def secret_repo() -> Path:
    """A real one-commit git repo carrying a hardcoded secret in a .py file."""
    with tempfile.TemporaryDirectory() as tmp:
        repo = Path(tmp)
        _git(repo, "init")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "T")
        (repo / "leak.py").write_text('password = "hunter2-not-a-real-secret"\n')
        (repo / "notes.md").write_text("# just prose\n")
        _git(repo, "add", ".")
        _git(repo, "commit", "-m", "add leak")
        yield repo


def test_is_source_accepts_real_source_paths() -> None:
    """The extension gate must match EXTENSION_TO_LANGUAGE's dotted keys."""
    assert HistorySecurityScanner._is_source("pkg/mod.py")
    assert HistorySecurityScanner._is_source("src/app.tsx")
    assert not HistorySecurityScanner._is_source("docs")


async def test_history_scan_on_real_repo_records_commit_provenance(
    session: AsyncSession, secret_repo: Path
) -> None:
    """A real scan finds the secret and dates it to the commit that introduced it."""
    scanner = HistorySecurityScanner(session, "repo-1")
    summary = await scanner.scan_history(secret_repo, secrets_only=True)

    assert summary.files_scanned > 0, "the extension gate rejected every blob"
    assert summary.findings_inserted > 0, "the secret in leak.py was not found"

    rows = (
        await session.execute(
            text("SELECT file_path, line_number, commit_sha, commit_at FROM security_findings")
        )
    ).all()
    assert rows
    for row in rows:
        m = row._mapping
        assert m["file_path"] == "leak.py"
        assert m["line_number"] == 1
        assert m["commit_sha"], "no introducing commit recorded"
        assert m["commit_at"] is not None, "introducing commit has no date"


async def test_history_default_skips_test_fixtures_and_placeholders(
    session: AsyncSession, secret_repo: Path
) -> None:
    """Secrets mode reports credentials, not fixtures and docs.

    Unfiltered, this repo's own history produced 832 hits of which 730 were
    ``api_key="sk-test"`` in unit tests and the rest were ``api_key="sk-..."``
    in docstrings. A report that is 100% noise is worse than no report.
    """
    _git(secret_repo, "config", "user.email", "t@example.com")
    (secret_repo / "tests").mkdir(exist_ok=True)
    (secret_repo / "tests" / "test_client.py").write_text('password = "fixture-value"\n')
    (secret_repo / "docs.py").write_text('# example: password = "..."\n')
    _git(secret_repo, "add", ".")
    _git(secret_repo, "commit", "-m", "add fixture and docs")

    scanner = HistorySecurityScanner(session, "repo-1")
    await scanner.scan_history(secret_repo, secrets_only=True)

    paths = {
        r._mapping["file_path"]
        for r in (await session.execute(text("SELECT file_path FROM security_findings"))).all()
    }
    assert "leak.py" in paths, "the real secret must still be reported"
    assert "tests/test_client.py" not in paths, "test fixture leaked into a secrets report"
    assert "docs.py" not in paths, "elided placeholder reported as a credential"


async def test_all_patterns_lifts_the_noise_gate(session: AsyncSession, secret_repo: Path) -> None:
    """The filtering is the default's promise, not a hard exclusion."""
    (secret_repo / "tests").mkdir(exist_ok=True)
    (secret_repo / "tests" / "test_client.py").write_text('password = "fixture-value"\n')
    _git(secret_repo, "add", ".")
    _git(secret_repo, "commit", "-m", "add fixture")

    scanner = HistorySecurityScanner(session, "repo-1")
    await scanner.scan_history(secret_repo, secrets_only=False)

    paths = {
        r._mapping["file_path"]
        for r in (await session.execute(text("SELECT file_path FROM security_findings"))).all()
    }
    assert "tests/test_client.py" in paths


async def test_history_all_patterns_uses_call_aware_eval_semantics(
    session: AsyncSession, secret_repo: Path
) -> None:
    path = secret_repo / "calls.py"
    path.write_text(
        "# eval(commented)\n"
        'text = "eval(in_string)"\n'
        "retrieval (payload)\n"
        "my_eval(payload)\n"
        "eval (payload)\n",
        encoding="utf-8",
    )
    _git(secret_repo, "add", ".")
    _git(secret_repo, "commit", "-m", "add eval corpus")

    scanner = HistorySecurityScanner(session, "repo-1")
    await scanner.scan_history(secret_repo, secrets_only=False)

    rows = (
        await session.execute(
            text(
                "SELECT kind, line_number FROM security_findings "
                "WHERE file_path = 'calls.py' AND kind IN ('eval_call', 'exec_call')"
            )
        )
    ).all()
    assert [tuple(row) for row in rows] == [("eval_call", 5)]


async def test_blob_introductions_resolves_real_blobs(secret_repo: Path) -> None:
    """Every source blob rev-list reports must resolve to an introducing commit."""
    scanner = HistorySecurityScanner.__new__(HistorySecurityScanner)
    blobs = scanner._unique_blobs(secret_repo, None, None)
    intro, dates = scanner._blob_introductions(secret_repo, None, None)

    source = [sha for sha, path in blobs.items() if HistorySecurityScanner._is_source(path)]
    assert source, "no source blobs found in the fixture repo"
    for sha in source:
        assert sha in intro, f"blob {sha} has no introducing commit (SHA width mismatch?)"
        assert dates.get(intro[sha]), "introducing commit has no author date"
