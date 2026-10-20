"""``repowise decision status`` — what capture did, and what it cost.

Nothing records a capture run, so every figure the command reports has to come
off a durable trace: the records, the review rows, the staging queues and the
``decision_extraction`` cost rows. These tests pin it to those, and pin the
absent ones to being absent rather than reported as zero.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.main import cli
from repowise.core.analysis.decisions.status import CAPTURE_COST_OPERATION
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import LlmCost

from .test_decision_cmd import _REPO_ID, _seed_wiki_db


def _add_cost_rows(repo: Path, rows: list[dict]) -> None:
    async def _build() -> None:
        db_path = repo / ".repowise" / "wiki.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
        await init_db(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            for row in rows:
                session.add(LlmCost(repository_id=_REPO_ID, **row))
            await session.commit()
        await engine.dispose()

    asyncio.run(_build())


@pytest.fixture
def status_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [
            {"id": "a" * 32, "title": "Use JWT", "status": "proposed", "source": "pr"},
            {"id": "b" * 32, "title": "Use Postgres", "status": "proposed", "source": "pr"},
            {"id": "c" * 32, "title": "Typed", "status": "proposed", "source": "git_archaeology"},
        ],
    )
    return repo


def _status(repo: Path) -> dict:
    result = CliRunner().invoke(cli, ["decision", "status", str(repo), "--format", "json"])
    assert result.exit_code == 0, result.output
    return json.loads(result.output)


def test_status_reports_the_effective_policy_and_preset(status_repo: Path) -> None:
    policy = _status(status_repo)["policy"]

    assert policy["preset"] == "default"
    assert policy["enabled"] is True
    # The per-source rows live under `sources`, not here: one list, so the two
    # cannot drift apart.
    assert "sources" not in policy


def test_status_names_why_a_source_made_no_call(status_repo: Path) -> None:
    (status_repo / ".repowise" / "config.yaml").write_text(
        yaml.safe_dump({"decisions": {"preset": "off"}}), encoding="utf-8"
    )

    report = _status(status_repo)

    assert report["policy"]["preset"] == "off"
    by_key = {source["key"]: source for source in report["sources"]}
    assert by_key["pr"]["status"] == "disabled"
    assert by_key["pr"]["reason"] == "Decision capture is off for this repository."
    # Manual entry is an authority route, not capture, so it has nothing to
    # switch off and says so rather than reading as collateral damage.
    assert by_key["cli"]["status"] == "always_on"


def test_status_counts_what_each_source_captured(status_repo: Path) -> None:
    by_key = {source["key"]: source for source in _status(status_repo)["sources"]}

    assert by_key["pr"]["records"] == 2
    assert by_key["pr"]["candidates"] == 2
    assert by_key["pr"]["accepted"] == 0
    assert by_key["pr"]["last_captured"] is not None
    assert by_key["git_archaeology"]["records"] == 1
    assert by_key["adr"]["records"] == 0
    assert by_key["adr"]["last_captured"] is None


def test_status_lanes_agree_with_acceptance_not_the_status_column(status_repo: Path) -> None:
    report = _status(status_repo)

    assert report["lanes"]["candidates"] == 3
    assert report["lanes"]["governing"] == 0
    assert report["review"]["unreviewed"] == 3

    accepted = CliRunner().invoke(
        cli, ["decision", "confirm", "aaaa", str(status_repo), "--format", "json"]
    )
    assert accepted.exit_code == 0, accepted.output

    after = _status(status_repo)
    assert after["lanes"]["governing"] == 1
    assert after["lanes"]["candidates"] == 2
    assert after["review"]["unreviewed"] == 2
    assert after["review"]["states"]["accepted"] == 1
    by_key = {source["key"]: source for source in after["sources"]}
    assert by_key["pr"]["accepted"] == 1


def test_status_reports_capture_spend_and_the_last_call(status_repo: Path) -> None:
    _add_cost_rows(
        status_repo,
        [
            {
                "model": "gpt-5.6-luna",
                "operation": CAPTURE_COST_OPERATION,
                "input_tokens": 1000,
                "output_tokens": 200,
                "cost_usd": 0.01,
            },
            {
                "model": "gpt-5.6-luna",
                "operation": CAPTURE_COST_OPERATION,
                "input_tokens": 500,
                "output_tokens": 100,
                "cost_usd": 0.005,
            },
            # Another operation's spend is not decision capture's.
            {
                "model": "gpt-5.6-luna",
                "operation": "doc_generation",
                "input_tokens": 9_999,
                "output_tokens": 9_999,
                "cost_usd": 5.0,
            },
        ],
    )

    cost = _status(status_repo)["cost"]

    assert cost["calls"] == 2
    assert cost["input_tokens"] == 1500
    assert cost["output_tokens"] == 300
    assert cost["cost_usd"] == pytest.approx(0.015)
    assert cost["last_call"]["model"] == "gpt-5.6-luna"


def test_status_says_the_backlog_is_absent_rather_than_empty(status_repo: Path) -> None:
    backlog = _status(status_repo)["backlog"]

    assert backlog["available"] is False
    assert "reason" in backlog
    assert "discovery_spans_pending" not in backlog


def test_status_reads_the_backlog_without_migrating_it(status_repo: Path) -> None:
    """A store predating a queue reports the queues it has, and gains no table."""
    sessions_db = status_repo / ".repowise" / "sessions" / "sessions.db"
    sessions_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(sessions_db)
    conn.execute("CREATE TABLE decisions (key TEXT PRIMARY KEY, promoted_at REAL)")
    conn.execute("INSERT INTO decisions VALUES ('one', NULL), ('two', 1.0)")
    conn.commit()
    conn.close()

    backlog = _status(status_repo)["backlog"]

    assert backlog["available"] is True
    assert backlog["session_decisions_unpromoted"] == 1
    assert "discovery_spans_pending" not in backlog

    conn = sqlite3.connect(sessions_db)
    tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert tables == {"decisions"}


def test_a_retired_source_keeps_the_row_shape_every_source_has(tmp_path: Path) -> None:
    """The API's source model requires every field a registry-backed row carries."""
    repo = tmp_path / "retired"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [{"id": "d" * 32, "title": "Old", "status": "proposed", "source": "code_comment"}],
    )

    sources = _status(repo)["sources"]
    registry = {source["key"] for source in sources if source["key"] != "code_comment"}
    retired = next(source for source in sources if source["key"] == "code_comment")
    reference = next(source for source in sources if source["key"] == "pr")

    assert registry, "the registry rows must still be present"
    assert set(retired) == set(reference)
    assert retired["records"] == 1
    assert retired["status"] == "disabled"


def test_the_source_census_counts_the_same_records_the_lanes_do(status_repo: Path) -> None:
    """Otherwise the Sources column sums to more than the total beside it."""
    dismissed = CliRunner().invoke(
        cli, ["decision", "dismiss", "aaaa", str(status_repo), "--yes", "--format", "json"]
    )
    assert dismissed.exit_code == 0, dismissed.output

    report = _status(status_repo)
    counted = sum(source["records"] for source in report["sources"])

    assert counted == report["lanes"]["total"] == 2
    assert report["review"]["unreviewed"] == report["lanes"]["candidates"] == 2


def test_status_separates_acceptable_from_blocked_backlog(status_repo: Path) -> None:
    """A backlog size means little without saying how much of it is workable."""
    review = _status(status_repo)["review"]

    # All three name a scope, a rationale and an evidence file, so `confirm`
    # would take them and `decision candidates` renders them acceptable. The
    # two commands have to agree about that.
    assert review["unreviewed"] == 3
    assert review["acceptable"] == 3
    assert review["blocked"] == 0
    assert review["acceptable"] + review["blocked"] == review["unreviewed"]
    # Seeded straight into the store, so no capture has written them a row.
    assert review["no_review_row"] == 3


def test_status_counts_a_blocked_candidate_as_blocked(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".repowise").mkdir()
    _seed_wiki_db(
        repo,
        [
            {"id": "d" * 32, "title": "Scoped", "status": "proposed", "source": "pr"},
            {
                "id": "e" * 32,
                "title": "Unscoped",
                "status": "proposed",
                "source": "pr",
                "affected_files": [],
            },
        ],
    )

    review = _status(repo)["review"]

    assert review["acceptable"] == 1
    assert review["blocked"] == 1
