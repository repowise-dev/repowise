"""``load_stored_coverage_map`` reloads persisted coverage rows so the
incremental health pass re-scores changed files against the coverage they
already had, instead of nulling ``line_coverage_pct`` for exactly the files
under active development (issue #1739).
"""

from __future__ import annotations

from repowise.core.persistence import (
    create_engine,
    create_session_factory,
    get_session,
)
from repowise.core.persistence.crud import (
    get_repository_by_path,
    save_coverage_files,
)
from repowise.core.persistence.database import init_db, resolve_db_url
from repowise.core.pipeline.incremental import load_stored_coverage_map


async def _seed(repo_path, files: list[dict]) -> None:
    engine = create_engine(resolve_db_url(repo_path))
    try:
        await init_db(engine)
        async with get_session(create_session_factory(engine)) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                from repowise.core.persistence.crud import upsert_repository

                repo = await upsert_repository(
                    session,
                    name="coverage-loader-test",
                    local_path=str(repo_path),
                    url="https://example.test/coverage",
                )
                await session.commit()
            await save_coverage_files(
                session, repo.id, files, source_format="lcov"
            )
            await session.commit()
    finally:
        await engine.dispose()


async def test_loader_reloads_persisted_coverage(tmp_path):
    """Rows saved by coverage add must come back as a health-coverage map."""
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    await _seed(
        repo_path,
        [
            {
                "file_path": "src/a.py",
                "line_coverage_pct": 0.8,
                "branch_coverage_pct": 0.5,
                "covered_lines": [1, 2, 3, 4],
                "total_coverable_lines": 5,
            },
            {
                "file_path": "src/b.py",
                "line_coverage_pct": 0.0,
                "branch_coverage_pct": None,
                "covered_lines": [],
                "total_coverable_lines": 10,
            },
        ],
    )

    cm = await load_stored_coverage_map(repo_path)
    assert cm["src/a.py"]["line_coverage_pct"] == 0.8
    assert cm["src/a.py"]["covered_lines"] == [1, 2, 3, 4]
    assert cm["src/b.py"]["line_coverage_pct"] == 0.0
    assert cm["src/b.py"]["covered_lines"] == []


async def test_loader_no_store_returns_empty(tmp_path):
    """Reading must never conjure a store, and an absent repo is no coverage."""
    assert await load_stored_coverage_map(tmp_path) == {}
    assert not (tmp_path / ".repowise").exists()
