"""Edit-time bug-history notice: fires on a real recent run of fixes, else silent.

The contract is the same "relevance or silence" one the decision notice follows,
with recency added: the copy always carries the last-fix age, and a file whose
fixes have aged past the window goes quiet no matter how many it accumulated.
The wiki.db is built through the real ORM schema so the hook's raw SQL runs
against the true column names.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from repowise.cli.commands.augment_cmd import decision_inject
from repowise.core.persistence.database import init_db
from repowise.core.persistence.models import GitMetadata, Repository

_REPO_ID = "repo1"
_PATH = "src/core/pipeline.py"


async def _build_wiki_db(repo_root: Path, **columns) -> None:
    """Create .repowise/wiki.db with one git_metadata row for :data:`_PATH`."""
    db_path = repo_root / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(repo_root)))
        session.add(
            GitMetadata(
                id="gm1",
                repository_id=_REPO_ID,
                file_path=_PATH,
                **columns,
            )
        )
        await session.commit()
    await engine.dispose()


def _days_ago(days: int) -> datetime:
    # Stored naive-UTC, the way the rollup writes it.
    return datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)


async def test_recent_run_of_fixes_fires_with_age_and_magnet(tmp_path):
    await _build_wiki_db(
        tmp_path,
        prior_defect_count=5,
        bug_magnet=True,
        last_fix_at=_days_ago(14),
        fix_symbol_counts_json=json.dumps({f"{_PATH}::run_pipeline": 4, f"{_PATH}::load": 1}),
    )

    notice = decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1")

    assert notice is not None
    assert "bug-fixed 5x in the last 6 months" in notice
    # Recency is mandatory in the copy, not an optional flourish.
    assert "2 weeks ago" in notice
    assert "(bug magnet)" in notice
    # The most-fixed symbol, named without repeating the path.
    assert "mostly in run_pipeline" in notice
    assert f"{_PATH}::" not in notice


async def test_stale_history_is_silent_however_large(tmp_path):
    """A file fixed a lot two years ago is history, not a warning."""
    await _build_wiki_db(
        tmp_path,
        prior_defect_count=12,
        bug_magnet=True,
        last_fix_at=_days_ago(700),
    )
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is None


async def test_too_few_fixes_is_silent(tmp_path):
    await _build_wiki_db(tmp_path, prior_defect_count=2, last_fix_at=_days_ago(3))
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is None


async def test_no_fix_data_is_silent(tmp_path):
    """A file with git history but no counted fixes adds nothing."""
    await _build_wiki_db(tmp_path, prior_defect_count=0)
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is None


async def test_untracked_file_is_silent(tmp_path):
    await _build_wiki_db(tmp_path, prior_defect_count=5, last_fix_at=_days_ago(3))
    assert decision_inject._edit_fix_history_notice(tmp_path, "other/file.py", "s") is None


async def test_no_index_is_silent(tmp_path):
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is None


async def test_fires_once_per_session_per_file(tmp_path):
    """The ledger claim is the dedup, so a re-edit in the same session is quiet."""
    await _build_wiki_db(tmp_path, prior_defect_count=4, last_fix_at=_days_ago(5))

    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is not None
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-1") is None
    # A new session starts over.
    assert decision_inject._edit_fix_history_notice(tmp_path, _PATH, "sess-2") is not None


async def test_session_cap_stops_the_drumbeat(tmp_path):
    """Past the per-session cap the notice goes quiet even on fresh files."""
    db_path = tmp_path / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    await init_db(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    paths = [f"src/f{i}.py" for i in range(decision_inject._MAX_EDIT_NOTICES + 2)]
    async with factory() as session:
        session.add(Repository(id=_REPO_ID, name="repo", local_path=str(tmp_path)))
        for i, path in enumerate(paths):
            session.add(
                GitMetadata(
                    id=f"gm{i}",
                    repository_id=_REPO_ID,
                    file_path=path,
                    prior_defect_count=4,
                    last_fix_at=_days_ago(5),
                )
            )
        await session.commit()
    await engine.dispose()

    fired = [decision_inject._edit_fix_history_notice(tmp_path, p, "sess-1") for p in paths]
    assert sum(1 for line in fired if line) == decision_inject._MAX_EDIT_NOTICES


def test_humanize_age_reads_like_a_person_wrote_it():
    assert decision_inject._humanize_age(0) == "today"
    assert decision_inject._humanize_age(1) == "yesterday"
    assert decision_inject._humanize_age(5) == "5 days ago"
    assert decision_inject._humanize_age(14) == "2 weeks ago"
    assert decision_inject._humanize_age(90) == "3 months ago"


def test_top_fix_symbol_tolerates_junk():
    assert decision_inject._top_fix_symbol(None) is None
    assert decision_inject._top_fix_symbol("not json") is None
    assert decision_inject._top_fix_symbol("{}") is None
    assert decision_inject._top_fix_symbol('{"a.py::save": 3}') == "save"
