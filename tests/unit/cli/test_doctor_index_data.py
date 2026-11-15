"""Doctor's index-data row: stale stamps and empty tables (#5219).

An index whose git, graph, symbol or health tables were empty and whose stamps
predated the current git history rules, health analyzer or page renderer
passed every doctor row. The row reads the stamps ``repowise update`` reads,
probes each table for a row, and warns with the one command that clears it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.doctor_cmd._checks import index_data, stores
from repowise.cli.commands.doctor_cmd._checks.context import CheckContext
from repowise.cli.commands.doctor_cmd._checks.index_data import (
    CHECK_ID,
    CODE_TABLE_SETS,
    FIX,
    FIX_EMPTY,
    FIX_MODEL_PAGES,
    code_tables_expected,
    empty_sets,
    expected_table_sets,
    fix_for,
    git_indexed,
    index_data_row,
    renderer_part,
    stale_parts,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION
from repowise.core.ingestion.git_indexer import (
    GIT_HISTORY_BACKFILL_REWALK,
    GIT_HISTORY_VERSION,
)

from ._index_rows import CODE_SETS, code_rows
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
        "index_scope": {"file_pages": {"eligible": 1}},
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
        f"security findings from an older scanner (unstamped, current v{SECURITY_SCANNER_VERSION})",
    ]


def test_unstamped_health_is_not_stale() -> None:
    # A legacy store with no health stamp is not re-scored by update either.
    assert stale_parts(_current_state(health_analyzer_version=None)) == []


def test_renderer_change_is_stale_for_a_wiki_with_pages() -> None:
    state = _current_state(docs_mode="deterministic", renderer_fingerprint="old")
    assert renderer_part(state, "new") == "pages rendered by an older page renderer"
    assert renderer_part(state, "old") is None


def test_unstamped_renderer_is_stale_like_update_reads_it() -> None:
    state = _current_state(docs_mode="llm")
    assert renderer_part(state, "new") == "pages rendered by an older page renderer"


def test_renderer_is_not_stale_without_pages_or_a_fingerprint() -> None:
    assert (
        renderer_part(_current_state(docs_mode="none", renderer_fingerprint="old"), "new") is None
    )
    # An empty current fingerprint means computing it failed: no verdict.
    assert renderer_part(_current_state(docs_mode="deterministic"), "") is None


def test_code_tables_are_expected_only_when_files_were_eligible() -> None:
    assert code_tables_expected(_current_state(), 0)
    assert not code_tables_expected(_current_state(index_scope={"file_pages": {"eligible": 0}}), 5)
    # An older state.json without index_scope falls back to the page count.
    assert code_tables_expected(_current_state(index_scope=None), 3)
    assert not code_tables_expected(_current_state(index_scope=None), 0)


def test_expected_sets_skip_git_for_a_plain_folder() -> None:
    assert list(expected_table_sets(_current_state(), 0)) == ["git", *CODE_SETS]
    folder = _current_state(last_sync_commit=None, git_history_version=None)
    assert list(expected_table_sets(folder, 0)) == list(CODE_SETS)


def test_a_set_is_empty_only_when_all_its_tables_are() -> None:
    expected = {"health": CODE_TABLE_SETS["health"], "symbol": CODE_TABLE_SETS["symbol"]}
    # A clean repo has no findings but still has file metrics.
    assert empty_sets(expected, {"health_findings"}) == []
    assert empty_sets(expected, {"health_findings", "health_file_metrics", "wiki_symbols"}) == [
        "health",
        "symbol",
    ]


def test_an_empty_set_makes_init_the_fix_over_any_stale_stamp() -> None:
    assert fix_for(["graph"], model_pages_stale=True) == FIX_EMPTY
    assert fix_for([], model_pages_stale=True) == FIX_MODEL_PAGES
    assert fix_for([], model_pages_stale=False) == FIX


def test_row_is_ok_without_problems() -> None:
    row = index_data_row([], [], ["git", "graph"], _current_state())
    assert (row.id, row.level, row.ok, row.fix, row.detail) == (
        CHECK_ID,
        "ok",
        True,
        None,
        "current; git and graph tables populated",
    )
    assert index_data_row([], [], [], _current_state()).detail == "current"


def test_row_warns_on_empty_git_tables_with_init() -> None:
    row = index_data_row([], ["git"], ["git"], _current_state())
    assert (row.level, row.ok, row.fix) == ("warn", True, FIX_EMPTY)
    assert "git tables are empty though the full git tier was indexed" in row.detail


# -- through the store checks, against a real store ------------------------


async def _build_store(
    repo_path: Path, *, git_rows: int, code_sets: tuple[str, ...] = CODE_SETS
) -> None:
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
        session.add_all(code_rows(repo.id, code_sets))
        await session.commit()
    await engine.dispose()


def _index_data_row(
    tmp_path: Path, state: dict, *, git_rows: int, code_sets: tuple[str, ...] = CODE_SETS
) -> dict:
    import git as gitpython

    repo_path = (tmp_path / "repo").resolve()
    repo_path.mkdir()
    gitpython.Repo.init(repo_path)
    asyncio.run(_build_store(repo_path, git_rows=git_rows, code_sets=code_sets))
    ctx = CheckContext(
        repo_path=repo_path,
        repowise_dir=repo_path / ".repowise",
        # The row is stamp reads and one EXISTS per table, so --quick keeps it.
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
    assert row["detail"] == "current; git, graph, symbol and health tables populated"
    assert row["fix"] is None


def test_stale_history_stamp_warns_with_the_fix(tmp_path: Path) -> None:
    row = _index_data_row(tmp_path, _current_state(git_history_version=3), git_rows=2)
    assert row["level"] == "warn"
    assert row["ok"] is True  # a warn never changes doctor's exit code
    assert row["fix"] == "repowise update --index-only"
    assert (
        f"git history written by older rules (v3, current v{GIT_HISTORY_VERSION})"
        in (row["detail"])
    )


@pytest.mark.parametrize(
    ("docs_mode", "fix"),
    [("deterministic", "repowise update --index-only"), ("llm", "repowise update")],
)
def test_stale_renderer_warns_with_the_run_that_re_renders(
    tmp_path: Path, monkeypatch, docs_mode: str, fix: str
) -> None:
    monkeypatch.setattr(index_data, "_current_renderer_fp", lambda _repo: "new")
    state = _current_state(docs_mode=docs_mode, renderer_fingerprint="old")
    row = _index_data_row(tmp_path, state, git_rows=2)
    assert (row["level"], row["fix"]) == ("warn", fix)
    assert row["detail"] == f"pages rendered by an older page renderer. Run: {fix}"


def test_empty_git_tables_at_full_tier_warn_with_init(tmp_path: Path) -> None:
    # update --index-only at an unchanged HEAD is "already up to date" and
    # leaves the tables empty; init is the run that refills them.
    row = _index_data_row(tmp_path, _current_state(), git_rows=0)
    assert row["level"] == "warn"
    assert row["fix"] == "repowise init"
    assert "git tables are empty though the full git tier was indexed" in row["detail"]


@pytest.mark.parametrize("empty", CODE_SETS)
def test_each_empty_code_table_set_warns_with_init(tmp_path: Path, empty: str) -> None:
    kept = tuple(name for name in CODE_SETS if name != empty)
    row = _index_data_row(tmp_path, _current_state(), git_rows=2, code_sets=kept)
    assert (row["level"], row["fix"]) == ("warn", "repowise init")
    assert row["detail"] == f"{empty} tables are empty. Run: repowise init"


def test_empty_tables_take_precedence_over_stale_stamps(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(index_data, "_current_renderer_fp", lambda _repo: "new")
    state = _current_state(git_history_version=3, docs_mode="llm", renderer_fingerprint="old")
    row = _index_data_row(tmp_path, state, git_rows=2, code_sets=("symbol", "health"))
    assert row["fix"] == "repowise init"
    assert row["detail"].startswith("graph tables are empty; git history written by older rules")
    assert "pages rendered by an older page renderer" in row["detail"]
    assert row["detail"].endswith(". Run: repowise init")


def test_folder_without_git_is_not_asked_for_git_rows(tmp_path: Path) -> None:
    state = _current_state(last_sync_commit=None, git_history_version=None)
    row = _index_data_row(tmp_path, state, git_rows=0)
    assert (row["level"], row["detail"]) == (
        "ok",
        "current; graph, symbol and health tables populated",
    )


def test_index_with_no_eligible_files_is_not_asked_for_code_rows(tmp_path: Path) -> None:
    state = _current_state(index_scope={"file_pages": {"eligible": 0}})
    row = _index_data_row(tmp_path, state, git_rows=2, code_sets=())
    assert (row["level"], row["detail"]) == ("ok", "current; git tables populated")
