"""Doctor's index-data row: stale stamps and empty git tables (#5219).

An index whose git tables were empty and whose stamps predated the current
git history rules and health analyzer passed every doctor row. The row reads
the stamps ``repowise update`` reads, counts the git rows, and warns with the
command that redoes the work without model calls.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd._checks import stores
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.index_data import (
    CHECK_ID,
    FIX,
    git_indexed,
    index_data_row,
    stale_parts,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_BACKFILL_REWALK,
    GIT_HISTORY_VERSION,
)

from ._real_call_guard import guard_real_calls


@pytest.fixture(autouse=True)
def _no_real_calls(monkeypatch) -> None:
    guard_real_calls(monkeypatch, allow_git=True)


def _current_state(**overrides: object) -> dict:
    state: dict = {
        "last_sync_commit": "abcdef0123456789",
        "git_tier": "full",
        "git_history_version": GIT_HISTORY_VERSION,
        "health_analyzer_version": HEALTH_ANALYZER_VERSION,
        "security_scanner_version": SECURITY_SCANNER_VERSION,
    }
    state.update(overrides)
    return {k: v for k, v in state.items() if v is not None}


# -- pure helpers ---------------------------------------------------------


def test_current_stamps_are_not_stale() -> None:
    assert stale_parts(_current_state()) == []


def test_old_history_stamp_names_both_versions() -> None:
    parts = stale_parts(_current_state(git_history_version=GIT_HISTORY_VERSION - 1))
    assert parts == [
        f"git history written by older rules "
        f"(v{GIT_HISTORY_VERSION - 1}, current v{GIT_HISTORY_VERSION})"
    ]


def test_missing_history_stamp_is_stale_like_update_reads_it() -> None:
    parts = stale_parts(_current_state(git_history_version=None))
    assert parts == [
        f"git history written by older rules (unstamped, current v{GIT_HISTORY_VERSION})"
    ]


def test_attribution_backfill_stamp_says_why() -> None:
    parts = stale_parts(_current_state(git_history_version=GIT_HISTORY_BACKFILL_REWALK))
    assert parts == ["git history awaits a re-walk after attribution backfill"]


def test_folder_without_git_has_no_history_to_be_stale() -> None:
    state = _current_state(last_sync_commit=None, git_history_version=None)
    assert not git_indexed(state)
    assert stale_parts(state) == []


def test_old_health_and_unstamped_security_are_stale() -> None:
    state = _current_state(
        health_analyzer_version=HEALTH_ANALYZER_VERSION - 1, security_scanner_version=None
    )
    assert stale_parts(state) == [
        f"health scored by an older analyzer "
        f"(v{HEALTH_ANALYZER_VERSION - 1}, current v{HEALTH_ANALYZER_VERSION})",
        f"security findings from an older scanner "
        f"(unstamped, current v{SECURITY_SCANNER_VERSION})",
    ]


def test_unstamped_health_is_not_stale() -> None:
    # A legacy store with no health stamp is not re-scored by update either.
    assert stale_parts(_current_state(health_analyzer_version=None)) == []


def test_row_is_ok_without_problems() -> None:
    row = index_data_row([], None, None)
    assert (row.id, row.level, row.ok, row.fix, row.detail) == (
        CHECK_ID,
        "ok",
        True,
        None,
        "current",
    )


def test_row_warns_on_empty_git_tables() -> None:
    row = index_data_row([], 0, "full")
    assert (row.level, row.ok, row.fix) == ("warn", True, FIX)
    assert "git tables are empty though the full git tier was indexed" in row.detail


# -- through the store checks, against a real store ------------------------


async def _build_store(repo_path: Path, *, git_rows: int) -> None:
    from repowise.core.persistence import create_engine, create_session_factory, get_session
    from repowise.core.persistence.crud import upsert_repository
    from repowise.core.persistence.crud.git import upsert_git_metadata
    from repowise.core.persistence.database import init_db

    rw = repo_path / ".repowise"
    rw.mkdir(parents=True)
    engine = create_engine(f"sqlite+aiosqlite:///{rw / 'wiki.db'}")
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        repo = await upsert_repository(
            session, name="repo", local_path=str(repo_path), url="https://example.test/repo"
        )
        for index in range(git_rows):
            await upsert_git_metadata(
                session, repository_id=repo.id, file_path=f"pkg/file{index}.py"
            )
        await session.commit()
    await engine.dispose()


def _index_data_row(tmp_path: Path, state: dict, *, git_rows: int) -> dict:
    import git as gitpython

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    asyncio.run(_build_store(repo_path, git_rows=git_rows))
    ctx = CheckContext(
        repo_path=repo_path,
        repowise_dir=repo_path / ".repowise",
        # The row is stamp reads and row counts, so --quick keeps it.
        quick=True,
        db_ok=True,
        state=state,
        state_ok=True,
    )
    rows = [row for row in stores.store_checks(ctx) if row.id == CHECK_ID]
    assert len(rows) == 1
    return rows[0]._asdict()


def test_current_index_is_ok(tmp_path: Path) -> None:
    row = _index_data_row(tmp_path, _current_state(), git_rows=2)
    assert row["level"] == "ok"
    assert row["detail"] == "current; git tables hold 2 rows"
    assert row["fix"] is None


def test_stale_history_stamp_warns_with_the_fix(tmp_path: Path) -> None:
    row = _index_data_row(tmp_path, _current_state(git_history_version=3), git_rows=2)
    assert row["level"] == "warn"
    assert row["ok"] is True  # a warn never changes doctor's exit code
    assert row["fix"] == "repowise update --index-only"
    assert f"git history written by older rules (v3, current v{GIT_HISTORY_VERSION})" in (
        row["detail"]
    )


def test_empty_git_tables_at_full_tier_warn(tmp_path: Path) -> None:
    row = _index_data_row(tmp_path, _current_state(), git_rows=0)
    assert row["level"] == "warn"
    assert row["fix"] == "repowise update --index-only"
    assert "git tables are empty though the full git tier was indexed" in row["detail"]


def test_folder_without_git_is_not_asked_for_git_rows(tmp_path: Path) -> None:
    state = _current_state(last_sync_commit=None, git_history_version=None)
    row = _index_data_row(tmp_path, state, git_rows=0)
    assert (row["level"], row["detail"]) == ("ok", "current")
