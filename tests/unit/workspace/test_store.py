"""The workspace store: rebuild, staleness, corrupt-file recovery."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from repowise.core.persistence.models import (
    Change,
    GraphNode,
    HealthSnapshot,
    Repository,
    WikiSymbol,
)
from repowise.core.workspace.config import RepoEntry, WorkspaceConfig
from repowise.core.workspace.registry import open_repo_db
from repowise.core.workspace.store import (
    SCHEMA_VERSION,
    lookup_symbols,
    refresh_workspace_store,
    store_path,
)
from repowise.core.workspace.update import run_cross_repo_hooks


async def write_repo_index(repo: Path, alias: str, symbols: dict[str, list[str]]) -> None:
    """A real ``wiki.db`` for *repo*: file nodes and symbols by file, one change,
    one health snapshot, and a ``state.json`` stamp."""
    (repo / ".repowise").mkdir(parents=True, exist_ok=True)
    engine, factory = await open_repo_db(repo)
    rid = f"repo-{alias}"
    async with factory() as session:
        session.add(Repository(id=rid, name=alias, url="", local_path=str(repo)))
        await session.flush()
        for path, names in symbols.items():
            session.add(GraphNode(repository_id=rid, node_id=path, node_type="file"))
            for name in names:
                session.add(
                    WikiSymbol(
                        repository_id=rid,
                        file_path=path,
                        symbol_id=f"{path}::{name}",
                        name=name.rsplit(".", 1)[-1],
                        qualified_name=name,
                        kind="function",
                    )
                )
        session.add(
            Change(
                repository_id=rid,
                change_id="pr-7",
                identity_kind="pr",
                identity_value="7",
                shape="squash",
                title=f"{alias} change",
                merged_at=datetime(2026, 10, 1, tzinfo=UTC),
                head_sha="a" * 40,
                files_changed=2,
            )
        )
        session.add(HealthSnapshot(repository_id=rid, average_health=7.5))
        await session.commit()
    await engine.dispose()
    touch_state(repo)


def touch_state(repo: Path) -> None:
    """What an index run does: rewrite ``state.json``."""
    state = repo / ".repowise" / "state.json"
    n = json.loads(state.read_text())["n"] + 1 if state.exists() else 0
    state.write_text(json.dumps({"n": n}))
    os.utime(state, ns=(state.stat().st_atime_ns, state.stat().st_mtime_ns + n + 1))


async def add_symbol(repo: Path, alias: str, path: str, name: str) -> None:
    engine, factory = await open_repo_db(repo)
    async with factory() as session:
        session.add(
            WikiSymbol(
                repository_id=f"repo-{alias}",
                file_path=path,
                symbol_id=f"{path}::{name}",
                name=name,
                qualified_name=name,
                kind="function",
            )
        )
        await session.commit()
    await engine.dispose()


def make_config(*aliases: str) -> WorkspaceConfig:
    return WorkspaceConfig(repos=[RepoEntry(path=a, alias=a) for a in aliases])


async def make_workspace(tmp_path: Path) -> tuple[WorkspaceConfig, Path]:
    await write_repo_index(tmp_path / "api", "api", {"src/app.py": ["serve", "App.run"]})
    await write_repo_index(tmp_path / "web", "web", {"src/ui.ts": ["render", "serve"]})
    return make_config("api", "web"), tmp_path


def symbols(path: Path, names: list[str]) -> dict:
    found = lookup_symbols(path, names)
    assert found is not None
    return found[0]


def rows(path: Path, sql: str) -> list[tuple]:
    conn = sqlite3.connect(path)
    try:
        return conn.execute(sql).fetchall()
    finally:
        conn.close()


async def test_refresh_builds_every_table_from_the_repo_indexes(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)

    assert path == store_path(root)
    assert rows(path, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert sorted(rows(path, "SELECT alias, path FROM files")) == [
        ("api", "src/app.py"),
        ("web", "src/ui.ts"),
    ]
    repos = {r[0]: r for r in rows(path, "SELECT alias, indexed, health_json, error FROM repos")}
    assert repos["api"][1] == 1 and repos["api"][3] is None
    assert json.loads(repos["api"][2])["average_health"] == 7.5
    assert rows(path, "SELECT alias, change_id, title FROM changes WHERE alias='web'") == [
        ("web", "pr-7", "web change")
    ]
    # Derived components (top-level directories here) per repo.
    assert ("api", "src") in rows(path, "SELECT alias, name FROM components")
    assert lookup_symbols(path, [], components_of="api") == ({}, {"src"})

    assert symbols(path, ["serve", "App.run", "run", "missing"]) == {
        "serve": [
            ("api", "src/app.py::serve", "src/app.py"),
            ("web", "src/ui.ts::serve", "src/ui.ts"),
        ],
        "App.run": [("api", "src/app.py::App.run", "src/app.py")],
        "run": [("api", "src/app.py::App.run", "src/app.py")],
    }


async def test_a_deleted_store_is_rebuilt(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    path.unlink()
    assert await refresh_workspace_store(config, root) == path
    assert symbols(path, ["render"]) == {
        "render": [("web", "src/ui.ts::render", "src/ui.ts")]
    }


async def test_only_repos_whose_index_moved_are_reread(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    before = dict(rows(path, "SELECT alias, refreshed_at FROM repos"))

    # A database write alone (a serving read, a cache) leaves the stamp alone.
    await add_symbol(root / "api", "api", "src/app.py", "late")
    await refresh_workspace_store(config, root)
    assert symbols(path, ["late"]) == {}

    # An index run rewrites state.json: that repo, and only it, is re-read.
    touch_state(root / "api")
    await refresh_workspace_store(config, root)
    assert symbols(path, ["late"]) == {"late": [("api", "src/app.py::late", "src/app.py")]}
    after = dict(rows(path, "SELECT alias, refreshed_at FROM repos"))
    assert after["web"] == before["web"]
    assert after["api"] != before["api"]


async def test_a_repo_leaving_the_workspace_drops_its_rows(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    await refresh_workspace_store(make_config("web"), root)
    assert {r[0] for r in rows(path, "SELECT DISTINCT alias FROM symbols")} == {"web"}
    assert rows(path, "SELECT alias FROM repos") == [("web",)]


async def test_an_unindexed_repo_is_listed_without_rows(tmp_path):
    config, root = await make_workspace(tmp_path)
    (root / "docs").mkdir()
    config.repos.append(RepoEntry(path="docs", alias="docs"))
    path = await refresh_workspace_store(config, root)
    assert rows(path, "SELECT indexed FROM repos WHERE alias='docs'") == [(0,)]
    assert not (root / "docs" / ".repowise" / "wiki.db").exists()


async def test_a_corrupt_store_is_rebuilt_never_fatal(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = store_path(root)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"not a database at all" * 100)

    assert await refresh_workspace_store(config, root) == path
    assert "render" in symbols(path, ["render"])


async def test_a_corrupt_store_found_by_a_reader_is_discarded(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    path.write_bytes(b"garbage" * 1000)
    assert lookup_symbols(path, ["render"]) is None
    assert not path.exists()
    await refresh_workspace_store(config, root)
    assert "render" in symbols(path, ["render"])


async def test_an_older_schema_is_rebuilt(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version=0")
    conn.execute("CREATE TABLE leftover (x)")
    conn.close()
    await refresh_workspace_store(config, root)
    assert rows(path, "PRAGMA user_version") == [(SCHEMA_VERSION,)]
    assert not rows(path, "SELECT name FROM sqlite_master WHERE name='leftover'")
    assert "render" in symbols(path, ["render"])


async def test_workspace_update_hooks_refresh_the_store(tmp_path):
    config, root = await make_workspace(tmp_path)
    await run_cross_repo_hooks(config, root, ["api", "web"])
    assert "serve" in symbols(store_path(root), ["serve"])


async def test_a_single_repo_workspace_gets_no_store(tmp_path):
    await write_repo_index(tmp_path / "api", "api", {"src/app.py": ["serve"]})
    await run_cross_repo_hooks(make_config("api"), tmp_path, ["api"])
    assert not store_path(tmp_path).exists()


async def test_a_store_missing_its_tables_is_rebuilt(tmp_path):
    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE repos")
    conn.close()
    assert await refresh_workspace_store(config, root) is None  # discarded, not served
    assert await refresh_workspace_store(config, root) == path
    assert "render" in symbols(path, ["render"])


async def test_an_unmoved_workspace_refreshes_without_opening_the_store(tmp_path, monkeypatch):
    from repowise.core.workspace import store as store_mod

    config, root = await make_workspace(tmp_path)
    path = await refresh_workspace_store(config, root)

    def refuse(*_args):
        raise AssertionError("opened the store")

    monkeypatch.setattr(store_mod, "WorkspaceStore", refuse)
    assert await refresh_workspace_store(config, root) == path
