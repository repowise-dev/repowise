"""A SQLite store made while the score columns were NOT NULL is rebuilt.

Local stores never run Alembic and the reconciler is additive, so without the
rebuild such a store would reject the unscored row of a file in a language
health has no dialect for, and the whole metrics write would fail.
"""

from __future__ import annotations

from sqlalchemy import text

from repowise.core.analysis.health.models import HealthFileMetricData
from repowise.core.persistence.crud import (
    clear_unanalysed_scores,
    get_health_metrics,
    save_health_metrics,
)
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GraphNode
from tests.unit.persistence.helpers import insert_repo


def _row(path: str, score: float | None) -> HealthFileMetricData:
    return HealthFileMetricData(
        file_path=path,
        score=score,
        max_ccn=None if score is None else 4,
        max_nesting=None if score is None else 2,
        nloc=12,
        has_test_file=False,
    )


async def _not_null(session, column: str) -> bool:
    rows = (await session.execute(text("PRAGMA table_info(health_file_metrics)"))).all()
    return next(bool(r[3]) for r in rows if r[1] == column)


async def test_a_not_null_store_is_rebuilt_and_keeps_its_rows(async_engine, async_session):
    ddl = (
        await async_session.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'health_file_metrics'")
        )
    ).scalar_one()
    for column, kind in (("score", "FLOAT"), ("max_ccn", "INTEGER"), ("max_nesting", "INTEGER")):
        # Anchored on the leading tab: ``score`` is also the tail of ``defect_score``.
        assert f"\t{column} {kind}," in ddl
        ddl = ddl.replace(f"\t{column} {kind},", f"\t{column} {kind} NOT NULL,")
    await async_session.execute(text("DROP TABLE health_file_metrics"))
    await async_session.execute(text(ddl))
    repo = await insert_repo(async_session)
    await save_health_metrics(async_session, repo.id, [_row("a.py", 7.5)])
    await async_session.commit()
    assert await _not_null(async_session, "score")

    await init_db(async_engine)

    assert not await _not_null(async_session, "score")
    assert not await _not_null(async_session, "max_ccn")
    await save_health_metrics(async_session, repo.id, [_row("a.py", 7.5), _row("B.php", None)])
    await async_session.commit()
    by_path = {m.file_path: m for m in await get_health_metrics(async_session, repo.id)}
    assert by_path["a.py"].score == 7.5 and by_path["a.py"].max_ccn == 4
    assert by_path["B.php"].score is None and by_path["B.php"].max_ccn is None
    # Still one row per file: the unique constraint came back with the table.
    ddl_after = (
        await async_session.execute(
            text("SELECT sql FROM sqlite_master WHERE name = 'health_file_metrics'")
        )
    ).scalar_one()
    assert "uq_health_file_metrics" in ddl_after


async def test_a_current_store_is_left_alone(async_engine, async_session):
    """Control: nothing to relax means no rebuild, and the rows are untouched."""
    repo = await insert_repo(async_session)
    await save_health_metrics(async_session, repo.id, [_row("a.py", 7.5)])
    await async_session.commit()
    before = (await async_session.execute(text("SELECT id FROM health_file_metrics"))).all()
    await init_db(async_engine)
    after = (await async_session.execute(text("SELECT id FROM health_file_metrics"))).all()
    assert before == after
    assert not await _not_null(async_session, "score")


async def _with_languages(session, repo_id: str, languages: dict[str, str]) -> None:
    session.add_all(
        GraphNode(repository_id=repo_id, node_id=p, node_type="file", language=lang)
        for p, lang in languages.items()
    )
    await session.flush()


async def test_an_update_clears_a_stored_ten_for_a_language_with_no_dialect(async_session):
    """A store written before this kept 10.0 / CCN 1 for every PHP file."""
    repo = await insert_repo(async_session)
    await save_health_metrics(
        async_session,
        repo.id,
        [_row("Big.php", 10.0), _row("ok.py", 7.5), _row("orphan.py", 6.0)],
    )
    # ``orphan.py`` has no graph node: no language, so it is left alone.
    await _with_languages(async_session, repo.id, {"Big.php": "php", "ok.py": "python"})
    assert await clear_unanalysed_scores(async_session, repo.id) == 1
    by_path = {m.file_path: m for m in await get_health_metrics(async_session, repo.id)}
    assert by_path["Big.php"].score is None and by_path["Big.php"].max_ccn is None
    assert by_path["ok.py"].score == 7.5
    assert by_path["orphan.py"].score == 6.0
    assert await clear_unanalysed_scores(async_session, repo.id) == 0


async def test_the_badge_says_no_data_when_no_file_is_scored(async_session):
    from repowise.server.routers.code_health.badge import _badge_average_health

    repo = await insert_repo(async_session)
    # No rows at all is no data too: a repo of only data and config files has none.
    assert await _badge_average_health(async_session, repo.id) is None
    await save_health_metrics(async_session, repo.id, [_row("Big.php", None)])
    assert await _badge_average_health(async_session, repo.id) is None
    await save_health_metrics(async_session, repo.id, [_row("Big.php", None), _row("a.py", 8.0)])
    assert await _badge_average_health(async_session, repo.id) == 8.0
