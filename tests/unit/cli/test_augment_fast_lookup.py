"""The stdlib-sqlite3 lookups against a real wiki.db, and their fallbacks.

Every behavioural test here is an *equivalence* test: it runs the fast path
and the ORM path over the same database and asserts the two produce the same
string. That is the only property the port is allowed to have. The speed is
measured elsewhere; what these pin is that nothing else moved, including the
two case-sensitivity semantics that differ between the queries (`IN` is
case-sensitive, `ilike` is not) and the three ways the fast path is required
to hand the work back to the ORM.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from repowise.cli.commands.augment_cmd import fast_lookup
from repowise.cli.commands.augment_cmd.search import (
    _ORM,
    _fast_pagerank_file_order,
    _fast_search_enrich,
    _pagerank_file_order,
    _search_enrich,
)

MATCHED = {"src/a.py": 3, "src/b.py": 1}


async def _build(repo_path: Path, symbols: list[tuple[str, str, str, int]]) -> None:
    """A real indexed repo: one repository row, symbols, and file nodes."""
    from repowise.core.persistence import (
        GraphNode,
        WikiSymbol,
        create_engine,
        create_session_factory,
        get_session,
        init_db,
        upsert_repository,
    )

    db_path = repo_path / ".repowise" / "wiki.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    try:
        await init_db(engine)
        sf = create_session_factory(engine)
        async with get_session(sf) as session:
            repo = await upsert_repository(session, name="repo", local_path=str(repo_path))
            for name, kind, file_path, start_line in symbols:
                session.add(
                    WikiSymbol(
                        repository_id=repo.id,
                        file_path=file_path,
                        symbol_id=f"{file_path}::{name}",
                        name=name,
                        qualified_name=name,
                        kind=kind,
                        start_line=start_line,
                    )
                )
            for node_id, pagerank in (("src/a.py", 0.9), ("src/b.py", 0.1)):
                session.add(
                    GraphNode(
                        repository_id=repo.id,
                        node_id=node_id,
                        node_type="file",
                        pagerank=pagerank,
                    )
                )
    finally:
        await engine.dispose()


@pytest.fixture
def indexed_repo(tmp_path) -> Path:
    """A repo whose index defines ``parseYaml`` in a file grep did not match."""
    asyncio.run(
        _build(
            tmp_path,
            [
                ("parse_yaml", "function", "src/b.py", 42),
                ("parseYaml", "function", "src/other.py", 10),
            ],
        )
    )
    return tmp_path


def _orm(repo_path: Path, pattern: str, mode: str, matched: dict[str, int]) -> str | None:
    return asyncio.run(_search_enrich(repo_path, pattern, mode, 40, matched))


class TestEquivalence:
    def test_triage_says_what_the_orm_says(self, indexed_repo: Path) -> None:
        fast = _fast_search_enrich(indexed_repo, "parse_yaml", "triage", 40, MATCHED)
        assert fast is not _ORM
        assert fast == _orm(indexed_repo, "parse_yaml", "triage", MATCHED)
        assert fast is not None

    def test_widened_rescue_says_what_the_orm_says(self, indexed_repo: Path) -> None:
        fast = _fast_search_enrich(indexed_repo, "parse_yaml", "rescue_wide", 5, MATCHED)
        assert fast == _orm(indexed_repo, "parse_yaml", "rescue_wide", MATCHED)
        assert fast == (
            "[repowise] `parse_yaml` matched 2 files, but not src/other.py:10, "
            "where indexed function `parseYaml` is defined."
        )

    def test_pagerank_order_matches_the_orm(self, indexed_repo: Path) -> None:
        paths = ["src/b.py", "src/a.py"]
        fast = _fast_pagerank_file_order(indexed_repo, paths)
        assert fast == asyncio.run(_pagerank_file_order(indexed_repo, paths))
        assert fast == ["src/a.py", "src/b.py"]

    @pytest.mark.parametrize("pattern", ["parse_yaml", "PARSEYAML", "yaml", "parse%", "e_y"])
    def test_zero_result_rescue_symbol_hit_says_what_the_orm_says(
        self, indexed_repo: Path, pattern: str
    ) -> None:
        fast = _fast_search_enrich(indexed_repo, pattern, "rescue", 0, None)
        assert fast is not _ORM
        assert fast == _orm(indexed_repo, pattern, "rescue", None)

    def test_zero_result_rescue_without_a_symbol_hit_keeps_the_orm(
        self, indexed_repo: Path
    ) -> None:
        """No symbol means the FTS leg, which only the ORM path serves."""
        assert _fast_search_enrich(indexed_repo, "subcommand dispatch", "rescue", 0, None) is _ORM


class TestLockedIndex:
    """Another process holding wiki.db: one bounded wait, then silence."""

    @pytest.fixture
    def locked(self, indexed_repo: Path):
        import sqlite3

        holder = sqlite3.connect(
            indexed_repo / ".repowise" / "wiki.db", timeout=0, isolation_level=None
        )
        holder.execute("PRAGMA locking_mode=EXCLUSIVE")
        holder.execute("BEGIN EXCLUSIVE")
        try:
            yield indexed_repo
        finally:
            holder.execute("ROLLBACK")
            holder.close()

    def test_probe_reports_the_lock(self, locked: Path, monkeypatch) -> None:
        monkeypatch.setattr(fast_lookup, "sqlite_wait_s", lambda cap: 0.0)
        assert fast_lookup.index_locked(locked)

    def test_probe_is_false_when_unlocked_or_unindexed(
        self, indexed_repo: Path, tmp_path: Path
    ) -> None:
        assert not fast_lookup.index_locked(indexed_repo)
        assert not fast_lookup.index_locked(tmp_path / "nowhere")

    def test_wait_budget_is_shared_by_the_process(self, monkeypatch) -> None:
        from repowise.cli.commands.augment_cmd import _shared

        monkeypatch.setattr(_shared, "_elapsed_ms", lambda: 0)
        assert _shared.sqlite_wait_s(1) == 1
        monkeypatch.setattr(_shared, "_elapsed_ms", lambda: _shared._SQLITE_WAIT_BUDGET_MS - 600)
        assert _shared.sqlite_wait_s(1) == pytest.approx(0.6)
        # A spent budget still leaves the floor, so a brief commit is waited out.
        monkeypatch.setattr(_shared, "_elapsed_ms", lambda: _shared._SQLITE_WAIT_BUDGET_MS + 1)
        assert _shared.sqlite_wait_s(30) == _shared._SQLITE_WAIT_FLOOR_S


class TestFallsBackToTheOrm:
    def test_a_configured_db_url_is_not_a_local_file(
        self, indexed_repo: Path, monkeypatch
    ) -> None:
        """The one way a naive version breaks in production.

        ``resolve_db_url`` honours these, so a hosted or postgres setup must
        reach the ORM rather than quietly read a stale local sqlite file.
        """
        for var in ("REPOWISE_DB_URL", "REPOWISE_DATABASE_URL"):
            monkeypatch.setenv(var, "postgresql://user@host/db")
            assert _fast_search_enrich(indexed_repo, "parse_yaml", "triage", 40, MATCHED) is _ORM
            assert _fast_pagerank_file_order(indexed_repo, ["src/a.py"]) is _ORM
            monkeypatch.delenv(var)

    def test_schema_drift_degrades_to_todays_behaviour(self, tmp_path: Path) -> None:
        """A wiki.db without the tables must not lose the surface."""
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "wiki.db").write_bytes(b"")
        assert _fast_search_enrich(tmp_path, "parse_yaml", "triage", 40, MATCHED) is _ORM
        assert _fast_pagerank_file_order(tmp_path, ["src/a.py"]) is _ORM

    def test_no_index_is_silence_not_a_fallback(self, tmp_path: Path) -> None:
        """No wiki.db: the ORM would import a second to answer None."""
        (tmp_path / ".repowise").mkdir()
        assert _fast_search_enrich(tmp_path, "parse_yaml", "triage", 40, MATCHED) is None
        assert _fast_pagerank_file_order(tmp_path, ["src/a.py"]) is None

    def test_unindexed_repo_row_is_silence(self, tmp_path: Path) -> None:
        """The db exists and is well-formed, but describes another checkout."""
        asyncio.run(_build(tmp_path, [("parse_yaml", "function", "src/b.py", 42)]))
        elsewhere = tmp_path / "nested"
        (elsewhere / ".repowise").mkdir(parents=True)
        (elsewhere / ".repowise" / "wiki.db").write_bytes(
            (tmp_path / ".repowise" / "wiki.db").read_bytes()
        )
        assert _fast_search_enrich(elsewhere, "parse_yaml", "triage", 40, MATCHED) is None


class TestCaseSemantics:
    """The ported queries differ from each other, and both had to survive."""

    def test_exact_name_lookup_stays_case_sensitive(self, indexed_repo: Path) -> None:
        """``IN`` compares TEXT with BINARY collation; the rescue relies on it.

        Its variants are generated case by case, so a case-insensitive match
        would make every variant fire on every other one.
        """
        conn = fast_lookup.connect(indexed_repo)
        assert conn is not None
        try:
            repo_id = fast_lookup.repo_id(conn, indexed_repo)
            assert fast_lookup.symbols_named(conn, repo_id, ["parseYaml"], 8)
            assert fast_lookup.symbols_named(conn, repo_id, ["PARSEYAML"], 8) == []
        finally:
            conn.close()

    def test_name_contains_lookup_stays_case_insensitive(self, indexed_repo: Path) -> None:
        """``ilike`` compiles to ``lower(name) LIKE lower(?)``; triage needs that."""
        conn = fast_lookup.connect(indexed_repo)
        assert conn is not None
        try:
            repo_id = fast_lookup.repo_id(conn, indexed_repo)
            paths = ["src/b.py", "src/other.py"]
            assert fast_lookup.symbols_matching(conn, repo_id, paths, "PARSE_YAML")
            assert fast_lookup.symbols_matching(conn, repo_id, paths, "yaml")
        finally:
            conn.close()

    def test_like_metacharacters_are_escaped(self, indexed_repo: Path) -> None:
        """``_`` is a wildcard unescaped, and it is in most Python names."""
        conn = fast_lookup.connect(indexed_repo)
        assert conn is not None
        try:
            repo_id = fast_lookup.repo_id(conn, indexed_repo)
            # parse_yaml is indexed; parseXyaml is not, and only an unescaped
            # underscore would let this pattern reach it.
            assert fast_lookup.symbols_matching(conn, repo_id, ["src/b.py"], "parse_yaml")
            assert fast_lookup.symbols_matching(conn, repo_id, ["src/b.py"], "parsexyaml") == []
        finally:
            conn.close()
