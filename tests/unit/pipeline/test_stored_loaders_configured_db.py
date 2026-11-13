"""The incremental loaders read a configured database, not only a local ``wiki.db``.

Under ``REPOWISE_DB_URL`` the repo-local file is absent by design, so a guard
that tests for it skipped every stored read and each incremental update scored
health as if nothing had been indexed (issue #2406). Each loader below gets its
stored rows seeded into a database outside the repo and must return them.
"""

from __future__ import annotations

import json
import uuid

import pytest

from repowise.core.persistence import create_engine, create_session_factory, get_session
from repowise.core.persistence.crud import save_coverage_files, upsert_repository
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitMetadata, HealthFinding
from repowise.core.pipeline.incremental import (
    load_stored_coverage_map,
    load_stored_function_mod_p80,
    load_stored_git_meta,
    load_stored_performance_callers,
)


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A repo with no ``.repowise`` dir, whose store is a database configured elsewhere."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    db_file = tmp_path / "shared" / "repowise.db"
    db_file.parent.mkdir()
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)
    monkeypatch.setenv("REPOWISE_DB_URL", f"sqlite+aiosqlite:///{db_file.as_posix()}")
    return repo_path


async def _seed(repo_path, *, function_mod_p80=None, rows=(), coverage=()) -> None:
    from repowise.core.persistence.database import resolve_db_url

    engine = create_engine(resolve_db_url(repo_path))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            stored = await upsert_repository(
                session,
                name="configured-db-test",
                local_path=str(repo_path),
                url="https://example.test/configured",
            )
            if function_mod_p80 is not None:
                stored.function_mod_p80 = function_mod_p80
            for row in rows:
                row.repository_id = stored.id
                session.add(row)
            if coverage:
                await save_coverage_files(session, stored.id, list(coverage), source_format="lcov")
            await session.commit()
    finally:
        await engine.dispose()


async def test_git_meta_is_read_from_a_configured_database(repo):
    await _seed(
        repo,
        rows=[GitMetadata(file_path="a.py", commit_count_90d=7, age_days=400)],
    )

    meta = await load_stored_git_meta(repo)

    assert meta is not None
    assert meta["a.py"]["commit_count_90d"] == 7
    assert meta["a.py"]["age_days"] == 400


async def test_performance_callers_are_read_from_a_configured_database(repo):
    await _seed(
        repo,
        rows=[
            HealthFinding(
                id=uuid.uuid4().hex,
                file_path="caller.py",
                biomarker_type="io_in_loop",
                severity="medium",
                function_name="run",
                line_start=4,
                line_end=4,
                details_json=json.dumps({"path": ["caller.py::run", "deleted.py::fetch"]}),
                health_impact=0.0,
                reason="old N+1",
                dimension="performance",
                status="open",
            )
        ],
    )

    assert await load_stored_performance_callers(repo, {"deleted.py"}) == {"caller.py"}


async def test_function_mod_p80_is_read_from_a_configured_database(repo):
    await _seed(repo, function_mod_p80=4)

    assert await load_stored_function_mod_p80(repo) == 4


async def test_coverage_map_is_read_from_a_configured_database(repo):
    await _seed(
        repo,
        coverage=[
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 0.8,
                "branch_coverage_pct": None,
                "covered_lines": [1, 2],
                "total_coverable_lines": 3,
            }
        ],
    )

    coverage_map = await load_stored_coverage_map(repo)

    assert coverage_map["src/a.py"]["line_coverage_pct"] == 0.8


async def test_no_configured_database_and_no_local_store_creates_nothing(tmp_path, monkeypatch):
    """With nothing configured, the guard still keeps a read from creating ``wiki.db``."""
    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    monkeypatch.delenv("REPOWISE_DATABASE_URL", raising=False)

    assert await load_stored_git_meta(tmp_path) is None
    assert await load_stored_performance_callers(tmp_path, {"a.py"}) is None
    assert await load_stored_function_mod_p80(tmp_path) is None
    assert await load_stored_coverage_map(tmp_path) == {}
    assert not (tmp_path / ".repowise").exists()
