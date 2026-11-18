"""``symbol_hotness`` in the store: whole rebuilds, the empty case and the readers."""

from __future__ import annotations

from sqlalchemy import func, insert, select

from repowise.core.evidence.hotness import hotness_rules, rebuild_symbol_hotness
from repowise.core.persistence.crud import (
    hotness_for_symbols,
    ranked_hotness,
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
    assert await ranked_hotness(async_session, repo.id) == []


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


async def test_rebuild_reads_the_profile_runs_and_the_readers_serve_them(async_session) -> None:
    repo = await insert_repo(async_session)
    await _facts(async_session, repo.id, "src/m.py::big", "src/m.py::small", "src/t.py::thin")
    await save_evidence_run(
        async_session, repo.id, _run("e"),
        _costs(("src/m.py::big", 300), ("src/m.py::small", 40), ("tests/test_m.py::t", 900)),
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
    assert (big.samples, big.run_samples, big.explained_share) == (300, 1000, 0.5)
    assert big.share_low < 0.3 < big.share_high

    ranked = await ranked_hotness(async_session, repo.id)
    # Lead candidates first, then by rank within their runs.
    assert [r.symbol_id for r in ranked] == ["src/m.py::big", "src/t.py::thin", "src/m.py::small"]
    in_tests = await ranked_hotness(async_session, repo.id, workload_classes={"unit_test"})
    assert [r.symbol_id for r in in_tests] == ["src/t.py::thin"]
    assert len(await ranked_hotness(async_session, repo.id, limit=1)) == 1
