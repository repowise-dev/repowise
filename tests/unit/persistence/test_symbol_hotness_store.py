"""``symbol_hotness`` in the store: whole rebuilds, the empty case and the readers."""

from __future__ import annotations

from sqlalchemy import func, insert, select

from repowise.core.evidence.hotness import hotness_rules, rebuild_symbol_hotness
from repowise.core.persistence.crud import (
    hotness_for_symbols,
    ranked_by_workload,
    replace_symbol_hotness,
    save_evidence_run,
)
from repowise.core.persistence.models import FunctionFact, SymbolHotness
from tests.unit.persistence.helpers import insert_repo

HEAD = "a" * 40
RULES = hotness_rules(None)


def _run(key: str, *, cls: str = "entry_point", partial: bool = False) -> dict:
    return {
        "run_key": key, "kind": "profile", "format": "repowise-evidence-v1", "source": "cli",
        "workload_name": f"w-{key}", "workload_class": cls, "value_kind": "wall",
        "unit": "samples", "commit_sha": HEAD, "dirty": False, "checkout_root": "/r",
        "total_value": 1000.0, "explained_value": 500.0, "mapping_partial": partial,
    }


def _costs(*named: tuple[str, float]) -> list[dict]:
    return [
        {"symbol_id": sid, "total_value": total, "self_value": total / 2, "body_digest": "d"}
        for sid, total in named
    ]


async def _facts(session, repo_id: str, *ids: str) -> None:
    await session.execute(
        insert(FunctionFact.__table__),
        [{"repository_id": repo_id, "symbol_id": sid, "execution_role": "unknown",
          "body_digest": "d"} for sid in ids],
    )


async def _count(session) -> int:
    return (await session.execute(select(func.count()).select_from(SymbolHotness))).scalar_one()


async def test_no_evidence_means_no_rows_and_no_work(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await rebuild_symbol_hotness(
        async_session, repo.id, head_commit=HEAD, rules=RULES
    ) is None
    assert await _count(async_session) == 0
    assert await hotness_for_symbols(async_session, repo.id, ["src/m.py::f"]) == {}
    assert await ranked_by_workload(async_session, repo.id) == []


async def test_a_rebuild_replaces_the_repositorys_rows_whole(async_session) -> None:
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/other")
    await replace_symbol_hotness(async_session, other.id, [{"symbol_id": "x.py::k"}])
    await replace_symbol_hotness(
        async_session, repo.id, [{"symbol_id": f"src/m.py::f{i}"} for i in range(1200)]
    )
    assert await _count(async_session) == 1201
    await replace_symbol_hotness(async_session, repo.id, [{"symbol_id": "src/m.py::g"}])
    rows = (await async_session.execute(select(SymbolHotness))).scalars().all()
    assert sorted((r.repository_id == repo.id, r.symbol_id) for r in rows) == [
        (False, "x.py::k"), (True, "src/m.py::g"),
    ]
    assert all(r.built_at is not None for r in rows)


async def test_hotness_for_symbols_reads_whole_files_by_key_range(async_session) -> None:
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/other")
    ids = ["src/m.py::f", "src/m.py::C::g", "src/m.pyx::h", "src/m.py.bak::i", "src/n.py::j"]
    await replace_symbol_hotness(async_session, repo.id, [{"symbol_id": sid} for sid in ids])
    await replace_symbol_hotness(async_session, other.id, [{"symbol_id": "src/m.py::k"}])
    found = await hotness_for_symbols(async_session, repo.id, ["src/n.py::j"], files=["src/m.py"])
    assert sorted(found) == ["src/m.py::C::g", "src/m.py::f", "src/n.py::j"]
    assert await hotness_for_symbols(async_session, repo.id, [], files=[]) == {}


async def test_hotness_for_symbols_chunks_files_and_ids_past_one_statement(async_session) -> None:
    from sqlalchemy import event

    from repowise.core.persistence.crud.analysis.function_facts import RANGES_PER_STATEMENT

    repo = await insert_repo(async_session)
    n_files = RANGES_PER_STATEMENT * 2 + 1
    files = [f"src/f{i}.py" for i in range(n_files)]
    ids = [f"src/g{i}.py::x" for i in range(1201)]
    await replace_symbol_hotness(
        async_session, repo.id, [{"symbol_id": f"{p}::f"} for p in files] + [{"symbol_id": s} for s in ids]
    )
    selects: list[int] = []
    engine = async_session.bind.sync_engine

    def _count(conn, cursor, statement, params, context, executemany):
        if statement.lstrip().upper().startswith("SELECT"):
            selects.append(len(params))

    event.listen(engine, "before_cursor_execute", _count)
    try:
        found = await hotness_for_symbols(async_session, repo.id, ids, files=files)
    finally:
        event.remove(engine, "before_cursor_execute", _count)
    assert len(found) == n_files + len(ids)
    # Three id chunks and three range chunks, paired: three statements, each
    # under SQLite's oldest 999 bound-variable ceiling.
    assert len(selects) == 3
    assert max(selects) < 999


async def test_rebuild_reads_the_profile_runs_and_the_readers_serve_them(async_session) -> None:
    repo = await insert_repo(async_session)
    await _facts(async_session, repo.id, "src/m.py::big", "src/m.py::small", "src/t.py::thin")
    await save_evidence_run(
        async_session, repo.id, _run("e"),
        _costs(("src/m.py::big", 300), ("src/m.py::small", 70), ("tests/test_m.py::t", 900)),
        history_runs=5, max_runs=50,
    )
    await save_evidence_run(
        async_session, repo.id, _run("u", cls="unit_test"), _costs(("src/t.py::thin", 80)),
        history_runs=5, max_runs=50,
    )
    # A mostly unmapped run is stored but no basis.
    await save_evidence_run(
        async_session, repo.id, _run("p", partial=True), _costs(("src/m.py::gone", 900)),
        history_runs=5, max_runs=50,
    )
    written = await rebuild_symbol_hotness(async_session, repo.id, head_commit=HEAD, rules=RULES)
    assert written == 3

    found = await hotness_for_symbols(
        async_session, repo.id, ["src/m.py::big", "src/m.py::gone", "tests/test_m.py::t"]
    )
    assert list(found) == ["src/m.py::big"]
    big = found["src/m.py::big"]
    assert (big.tier, big.rank, big.freshness, big.run_unlisted) == (
        "lead_candidate", 1, "current", 1,
    )
    # Samples are the cost's: self, not inclusive.
    assert (big.samples, big.run_samples, big.explained_share) == (150, 1000, 0.5)
    assert big.share_low < 0.15 < big.share_high

    by_workload = await ranked_by_workload(async_session, repo.id)
    # One block per workload's newest run, its functions best rank first; the
    # partial run is no basis.
    assert [(run.workload_name, [r.symbol_id for r in rows]) for run, rows in by_workload] in (
        [("w-e", ["src/m.py::big", "src/m.py::small"]), ("w-u", ["src/t.py::thin"])],
        [("w-u", ["src/t.py::thin"]), ("w-e", ["src/m.py::big", "src/m.py::small"])],
    )


async def test_rows_left_without_a_profile_run_are_cleared(async_session) -> None:
    repo = await insert_repo(async_session)
    await replace_symbol_hotness(async_session, repo.id, [{"symbol_id": "src/m.py::f"}])
    assert await rebuild_symbol_hotness(
        async_session, repo.id, head_commit=HEAD, rules=RULES
    ) is None
    assert await _count(async_session) == 0


async def test_a_failed_refresh_keeps_the_stored_rows(async_session, monkeypatch) -> None:
    from repowise.core.evidence import hotness

    repo = await insert_repo(async_session)
    await replace_symbol_hotness(async_session, repo.id, [{"symbol_id": "src/m.py::f"}])

    async def broken(*_a, **_k):
        await replace_symbol_hotness(async_session, repo.id, [])
        raise RuntimeError("boom")

    monkeypatch.setattr(hotness, "rebuild_symbol_hotness", broken)
    await hotness.refresh_symbol_hotness(async_session, repo.id, head_commit=HEAD, repo_path=None)
    assert await _count(async_session) == 1


async def test_an_update_that_analyses_no_file_still_refreshes_hotness(
    async_session, monkeypatch
) -> None:
    from types import SimpleNamespace

    from repowise.core.evidence import hotness
    from repowise.core.pipeline.incremental.health import persist_partial_health

    repo = await insert_repo(async_session)
    calls: list[str] = []

    async def record(_session, repo_id, **_k):
        calls.append(repo_id)

    monkeypatch.setattr(hotness, "refresh_symbol_hotness", record)
    report = SimpleNamespace(authoritative_paths=(), metrics=[], performance_authoritative_paths=())
    await persist_partial_health(async_session, repo.id, report)
    assert calls == [repo.id]
