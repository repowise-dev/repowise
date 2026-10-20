"""`repowise update` recounts ``index_scope.file_pages`` (#3905)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.cli.commands.update_cmd import persistence
from repowise.core.generation.selection.selector import eligible_file_page_paths
from repowise.core.index_scope import file_page_counts, file_page_scope


def _parsed(path: str, *, is_test: bool = False, symbols: tuple[str, ...] = ("f",)):
    return SimpleNamespace(
        file_info=SimpleNamespace(
            path=path,
            language="python",
            size_bytes=5_000,
            is_test=is_test,
            is_api_contract=False,
            is_entry_point=False,
        ),
        symbols=[SimpleNamespace(name=n, kind="function") for n in symbols],
    )


class _Builder:
    def pagerank(self) -> dict[str, float]:
        return {}


def _stale_state() -> dict:
    pages = {"configured_cap": None, "effective_cap": None, "eligible": 1, "generated": 1}
    return {"index_scope": {"file_pages": {**pages, "omitted": 0}}}


@pytest.fixture
def no_config(monkeypatch):
    monkeypatch.setattr(persistence, "load_config", lambda _repo: {})
    monkeypatch.setattr("repowise.core.repo_config.load_repo_config", lambda _repo: {})


def _stored(page_paths: set[str], hotspots: set[str]):
    async def load(_repo):
        return set(page_paths), set(hotspots)

    return load


def test_recounts_from_parse_and_stored_pages(monkeypatch, no_config) -> None:
    # The test file's leftover page is not counted as generated.
    monkeypatch.setattr(
        persistence,
        "_load_page_paths_and_hotspots",
        _stored({"a.py", "b.py", "t.py"}, set()),
    )
    state = _stale_state()
    parsed = [
        _parsed("a.py", symbols=("fa",)),
        _parsed("b.py", symbols=("fb",)),
        _parsed("c.py", symbols=("fc",)),
        _parsed("t.py", is_test=True),
    ]

    persistence.stamp_file_page_scope("repo", state, parsed, _Builder(), {})

    pages = state["index_scope"]["file_pages"]
    assert (pages["eligible"], pages["generated"], pages["omitted"]) == (3, 2, 1)


def test_this_runs_hotspot_rows_win(monkeypatch, no_config) -> None:
    monkeypatch.setattr(
        persistence, "_load_page_paths_and_hotspots", _stored(set(), {"shim.py"})
    )
    state = _stale_state()
    parsed = [_parsed("shim.py", symbols=()), _parsed("other.py", symbols=())]

    rows = {"shim.py": {"is_hotspot": False}, "other.py": {"is_hotspot": True}}
    persistence.stamp_file_page_scope("repo", state, parsed, _Builder(), rows)

    pages = state["index_scope"]["file_pages"]
    assert (pages["eligible"], pages["omitted"]) == (1, 1)


def test_explicit_unlimited_cap_is_kept(monkeypatch, no_config) -> None:
    monkeypatch.setattr(persistence, "_load_page_paths_and_hotspots", _stored({"a.py"}, set()))
    state = _stale_state()
    state["index_scope"]["file_pages"]["configured_cap"] = 0

    persistence.stamp_file_page_scope("repo", state, [_parsed("a.py")], _Builder(), {})

    pages = state["index_scope"]["file_pages"]
    assert (pages["configured_cap"], pages["effective_cap"], pages["omitted"]) == (0, None, 0)


def test_docs_update_stamps_before_the_editor_files_quote_it() -> None:
    import inspect

    from repowise.cli.commands.update_cmd.command import _docs_update

    source = inspect.getsource(_docs_update)
    assert source.index("stamp_file_page_scope") < source.index("_finish_outcome")


def test_unreadable_store_keeps_previous_figures(monkeypatch, no_config) -> None:
    async def load(_repo):
        return None

    monkeypatch.setattr(persistence, "_load_page_paths_and_hotspots", load)
    state = _stale_state()

    persistence.stamp_file_page_scope("repo", state, [_parsed("a.py")], _Builder(), {})

    assert state == _stale_state()


def test_no_parse_is_a_no_op(no_config) -> None:
    state = _stale_state()
    persistence.stamp_file_page_scope("repo", state, [], _Builder(), {})
    assert state == _stale_state()


def test_near_clone_losers_are_not_eligible() -> None:
    clones = [_parsed(f"gen/c{i}.py", symbols=("run",)) for i in range(3)]
    pagerank = {"gen/c0.py": 1.0}
    assert eligible_file_page_paths(clones, pagerank, hotspots=set()) == {"gen/c0.py"}
    every = eligible_file_page_paths(clones, pagerank, hotspots=set(), dedupe_near_clones=False)
    assert len(every) == 3


def test_counts_match_the_page_list_form() -> None:
    pages = [SimpleNamespace(page_type="file_page")] * 3 + [
        SimpleNamespace(page_type="symbol_spotlight")
    ]
    assert file_page_scope(configured_cap=None, eligible=5, generated_pages=pages) == (
        file_page_counts(configured_cap=None, eligible=5, generated=3)
    )


@pytest.mark.asyncio
async def test_store_read_skips_tombstones(tmp_path, monkeypatch) -> None:
    from repowise.core.persistence import (
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )
    from repowise.core.persistence.crud import upsert_page
    from tests.unit.persistence.helpers import make_page_kwargs

    url = f"sqlite+aiosqlite:///{(tmp_path / 'wiki.db').as_posix()}"
    monkeypatch.setattr("repowise.cli.helpers.get_db_url_for_repo", lambda _repo: url)
    engine = create_engine(url)
    await init_db(engine)
    async with get_session(create_session_factory(engine)) as session:
        repo = await upsert_repository(session, name="r", local_path=str(tmp_path))
        for path, status in (("a.py", "fresh"), ("b.py", "stale"), ("c.py", "tombstone")):
            await upsert_page(
                session,
                **make_page_kwargs(
                    repo.id, page_id=f"file_page:{path}", target_path=path, freshness_status=status
                ),
            )
    await engine.dispose()

    assert await persistence._load_page_paths_and_hotspots(tmp_path) == ({"a.py", "b.py"}, set())
