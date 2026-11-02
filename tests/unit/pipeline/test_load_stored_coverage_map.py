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


def _newer_report(repo_path, source: str) -> None:
    """A report written now, after the ingest :func:`_seed` recorded."""
    report = repo_path / "coverage" / "lcov.info"
    report.parent.mkdir()
    report.write_text(f"SF:{source}\nDA:1,1\nDA:2,0\nend_of_record\n", encoding="utf-8")


async def test_loader_rereads_a_report_written_after_the_last_ingest(tmp_path, monkeypatch):
    from types import SimpleNamespace

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    await _seed(repo_path, [{"file_path": "src/a.py", "line_coverage_pct": 0.0, "total_coverable_lines": 2}])
    _newer_report(repo_path, "src/a.py")

    async def _metrics(_session, _repo_id):
        return [SimpleNamespace(file_path="src/a.py")]

    monkeypatch.setattr("repowise.core.persistence.crud.get_health_metrics", _metrics)
    from repowise.core.analysis.health import coverage

    reads: list = []
    real_build = coverage.build_coverage_map

    def _counted(*args, **kwargs):
        reads.append(args[1])
        return real_build(*args, **kwargs)

    monkeypatch.setattr(coverage, "build_coverage_map", _counted)

    cm = await load_stored_coverage_map(repo_path)
    assert cm["src/a.py"]["line_coverage_pct"] == 50.0
    # Stored with a newer ingest, so the next update keeps it without a re-read.
    again = await load_stored_coverage_map(repo_path)
    assert again["src/a.py"]["line_coverage_pct"] == 50.0
    assert len(reads) == 1


async def test_a_reread_report_older_than_the_source_is_reported(tmp_path, monkeypatch):
    from types import SimpleNamespace

    repo_path = tmp_path / "repo"
    (repo_path / "src").mkdir(parents=True)
    # Lines 2-5 of the report now land on blank lines.
    (repo_path / "src" / "a.py").write_text("x = 1\n\n\n\n\n", encoding="utf-8")
    await _seed(repo_path, [{"file_path": "src/a.py", "line_coverage_pct": 0.0, "total_coverable_lines": 2}])
    report = repo_path / "coverage" / "lcov.info"
    report.parent.mkdir()
    report.write_text("SF:src/a.py\n" + "".join(f"DA:{n},1\n" for n in range(1, 6)) + "end_of_record\n")

    async def _metrics(_session, _repo_id):
        return [SimpleNamespace(file_path="src/a.py")]

    monkeypatch.setattr("repowise.core.persistence.crud.get_health_metrics", _metrics)
    logged: list[str] = []

    await load_stored_coverage_map(repo_path, log=logged.append)
    assert any("no longer match the source" in line for line in logged)


async def test_loader_keeps_stored_rows_when_reingest_is_off(tmp_path, monkeypatch):
    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    await _seed(repo_path, [{"file_path": "src/a.py", "line_coverage_pct": 0.0, "total_coverable_lines": 2}])
    _newer_report(repo_path, "src/a.py")
    monkeypatch.setattr(
        "repowise.core.repo_config.load_repo_config",
        lambda _p: {"coverage": {"reingest_on_update": False}},
    )

    cm = await load_stored_coverage_map(repo_path)
    assert cm["src/a.py"]["line_coverage_pct"] == 0.0


async def test_a_failed_reread_still_loads_the_stored_rows(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from repowise.core.analysis.health import coverage

    repo_path = tmp_path / "repo"
    repo_path.mkdir()
    await _seed(repo_path, [{"file_path": "src/a.py", "line_coverage_pct": 0.0, "total_coverable_lines": 2}])
    _newer_report(repo_path, "src/a.py")

    async def _metrics(_session, _repo_id):
        return [SimpleNamespace(file_path="src/a.py")]

    def _broken(*_args, **_kwargs):
        raise OSError("report vanished")

    monkeypatch.setattr("repowise.core.persistence.crud.get_health_metrics", _metrics)
    monkeypatch.setattr(coverage, "build_coverage_map", _broken)
    logged: list[str] = []

    cm = await load_stored_coverage_map(repo_path, log=logged.append)
    assert cm["src/a.py"]["line_coverage_pct"] == 0.0
    assert any("Coverage re-read skipped" in line for line in logged)
