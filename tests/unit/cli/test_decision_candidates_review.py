"""``repowise decision candidates`` is a queue, so it has to lead with work.

Ordering by confidence put the candidates nobody could accept at the top, which
is the opposite of what a reviewer opening the list needs.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.core.persistence.models import DecisionCandidateMeta

from .test_decision_cmd import _REPO_ID, _seed_wiki_db


def _add_meta(repo: Path, rows: list[dict]) -> None:
    async def _build() -> None:
        db_path = repo / ".repowise" / "wiki.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for row in rows:
                session.add(DecisionCandidateMeta(repository_id=_REPO_ID, **row))
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())


@pytest.fixture
def review_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [
            # High confidence, but names no scope: `confirm` will refuse it.
            {
                "id": "a" * 32,
                "title": "Unscoped",
                "status": "proposed",
                "source": "session",
                "confidence": 0.99,
                "affected_files": [],
            },
            {
                "id": "b" * 32,
                "title": "Acceptable",
                "status": "proposed",
                "source": "session",
                "confidence": 0.20,
            },
        ],
    )
    # The priorities capture writes for these two records: the contract refuses
    # the unscoped one and would take the other.
    _add_meta(
        repo,
        [
            {"decision_id": "a" * 32, "review_priority": 0.0, "lane": "session_discovery"},
            {"decision_id": "b" * 32, "review_priority": 1.0, "lane": "session"},
        ],
    )
    return repo


def _candidates(repo: Path, *args: str) -> dict:
    result = CliRunner().invoke(
        cli, ["decision", "candidates", str(repo), *args, "--format", "json"]
    )
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_acceptable_candidates_come_first(review_repo: Path) -> None:
    """Ahead of the higher-confidence row, which is the point of the change."""
    rows = _candidates(review_repo)["candidates"]

    assert [row["title"] for row in rows] == ["Acceptable", "Unscoped"]
    assert rows[0]["confidence"] < rows[1]["confidence"]
    assert rows[0]["blockers"] == [] and rows[1]["blockers"]


def test_each_row_names_what_would_refuse_it(review_repo: Path) -> None:
    by_title = {row["title"]: row for row in _candidates(review_repo)["candidates"]}

    assert by_title["Acceptable"]["blockers"] == []
    assert by_title["Unscoped"]["blockers"] == [
        "no scope: name the files or modules it governs"
    ]


def test_lane_filters_to_the_lane_that_raised_it(review_repo: Path) -> None:
    rows = _candidates(review_repo, "--lane", "session_discovery")["candidates"]

    assert [row["title"] for row in rows] == ["Unscoped"]


def test_the_table_says_which_rows_are_acceptable(review_repo: Path) -> None:
    result = CliRunner().invoke(cli, ["decision", "candidates", str(review_repo)])

    assert result.exit_code == 0, result.output
    lines = result.output.splitlines()
    acceptable = next(line for line in lines if "bbbbbbbb" in line)
    unscoped = next(line for line in lines if "aaaaaaaa" in line)
    assert "yes" in acceptable and "no scope" not in acceptable
    assert "no scope" in unscoped and "yes" not in unscoped
