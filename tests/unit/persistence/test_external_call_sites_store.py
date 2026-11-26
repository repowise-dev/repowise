"""The package-call table: full and scoped replaces, the stamp, the reader filters."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from repowise.core.ingestion.external_calls import EXTERNAL_CALL_SITES_VERSION, ExternalCallSite
from repowise.core.persistence.crud import (
    external_call_sites_complete,
    external_call_sites_version,
    list_external_call_sites,
    replace_external_call_sites,
)
from repowise.core.pipeline.persist import persist_external_call_sites
from tests.unit.persistence.helpers import insert_repo


def _site(path: str, line: int, module: str = "requests", symbol: str = "get") -> ExternalCallSite:
    eco = "npm" if path.endswith(".ts") else "pypi"
    return ExternalCallSite(
        file_path=path,
        line=line,
        caller_symbol_id=f"{path}::f",
        ecosystem=eco,
        module=module,
        package=module.split(".")[0],
        external_node=f"external:{module}",
        local_name=module,
        symbol_path=symbol,
        binding="module",
    )


async def _paths(session, repo_id: str) -> list[tuple[str, int]]:
    return [(s.file_path, s.line) for s in await list_external_call_sites(session, repo_id)]


async def test_a_full_replace_writes_every_row_and_stamps(async_session) -> None:
    repo = await insert_repo(async_session)
    assert not await external_call_sites_complete(async_session, repo.id)
    sites = {"a.py": [_site("a.py", 3), _site("a.py", 3)], "b.py": [_site("b.py", 1)]}
    written = await replace_external_call_sites(
        async_session, repo.id, sites, scope=None, live_paths={"a.py", "b.py"}
    )
    assert written == 2  # the duplicate call on one line is one row
    assert await external_call_sites_version(async_session, repo.id) == EXTERNAL_CALL_SITES_VERSION
    assert await external_call_sites_complete(async_session, repo.id)
    assert await _paths(async_session, repo.id) == [("a.py", 3), ("b.py", 1)]

    await replace_external_call_sites(
        async_session, repo.id, {"b.py": [_site("b.py", 9)]}, scope=None, live_paths={"b.py"}
    )
    assert await _paths(async_session, repo.id) == [("b.py", 9)]


async def test_a_scoped_replace_keeps_other_files_and_drops_gone_ones(async_session) -> None:
    repo = await insert_repo(async_session)
    full = {p: [_site(p, 1)] for p in ("a.py", "b.py", "gone.py")}
    await replace_external_call_sites(
        async_session, repo.id, full, scope=None, live_paths=set(full)
    )
    await replace_external_call_sites(
        async_session,
        repo.id,
        {"a.py": [_site("a.py", 7)]},
        scope=frozenset({"a.py"}),
        live_paths={"a.py", "b.py"},
    )
    assert await _paths(async_session, repo.id) == [("a.py", 7), ("b.py", 1)]


async def test_a_scope_file_with_no_calls_left_loses_its_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    await replace_external_call_sites(
        async_session, repo.id, {"a.py": [_site("a.py", 1)]}, scope=None, live_paths={"a.py"}
    )
    await replace_external_call_sites(
        async_session, repo.id, {}, scope=frozenset({"a.py"}), live_paths={"a.py"}
    )
    assert await _paths(async_session, repo.id) == []


async def test_reader_filters(async_session) -> None:
    repo = await insert_repo(async_session)
    sites = {
        "a.py": [_site("a.py", 1), _site("a.py", 2, "yaml", "safe_load")],
        "b.ts": [_site("b.ts", 3, "lodash", "merge")],
    }
    await replace_external_call_sites(
        async_session, repo.id, sites, scope=None, live_paths=set(sites)
    )
    by_package = await list_external_call_sites(
        async_session, repo.id, packages=[("pypi", "yaml"), ("npm", "lodash")]
    )
    assert [(s.package, s.symbol_path) for s in by_package] == [
        ("yaml", "safe_load"),
        ("lodash", "merge"),
    ]
    assert [
        s.line
        for s in await list_external_call_sites(
            async_session, repo.id, external_nodes=["external:requests"]
        )
    ] == [1]
    assert [
        s.line for s in await list_external_call_sites(async_session, repo.id, file_paths=["b.ts"])
    ] == [3]
    assert [
        s.line
        for s in await list_external_call_sites(
            async_session, repo.id, caller_symbol_ids=["a.py::f"]
        )
    ] == [1, 2]


async def test_a_builder_with_no_build_leaves_the_rows(async_session) -> None:
    repo = await insert_repo(async_session)
    await replace_external_call_sites(
        async_session, repo.id, {"a.py": [_site("a.py", 1)]}, scope=None, live_paths={"a.py"}
    )
    await persist_external_call_sites(
        async_session, repo.id, SimpleNamespace(external_call_sites=None)
    )
    assert await _paths(async_session, repo.id) == [("a.py", 1)]


async def test_a_failed_write_keeps_the_rows_and_clears_the_stamp(async_session) -> None:
    repo = await insert_repo(async_session)
    await replace_external_call_sites(
        async_session, repo.id, {"a.py": [_site("a.py", 1)]}, scope=None, live_paths={"a.py"}
    )
    broken = SimpleNamespace(
        external_call_sites={"a.py": ["not a site"]},
        external_call_scope=None,
        _parsed_files={"a.py": None},
    )
    with pytest.raises(AttributeError):
        await persist_external_call_sites(async_session, repo.id, broken)
    assert await _paths(async_session, repo.id) == [("a.py", 1)]
    assert await external_call_sites_version(async_session, repo.id) is None
    assert not await external_call_sites_complete(async_session, repo.id)
