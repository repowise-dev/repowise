"""Stored build units: bulk replace, read-back, the rehydrated builder and both upgrade paths."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic import command
from sqlalchemy import func, select

from repowise.core.build_units import BuildUnit, BuildUnitIndex
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.persistence.crud import load_build_units, replace_build_units
from repowise.core.persistence.database import create_engine, init_db
from repowise.core.persistence.models import BuildUnitDep, BuildUnitFile, BuildUnitRow
from repowise.core.pipeline.persist import persist_build_units
from tests.unit.persistence.helpers import insert_repo
from tests.unit.persistence.test_wiki_pages_content_hash_migration import _alembic_config

_TABLES = (BuildUnitRow, BuildUnitDep, BuildUnitFile)

_REPO = {
    "package.json": json.dumps({"name": "root", "private": True, "workspaces": ["packages/*"]}),
    "packages/a/package.json": json.dumps({"name": "@x/a"}),
    "packages/a/index.js": "export const a = 1;\n",
    "packages/b/package.json": json.dumps({"name": "@x/b", "dependencies": {"@x/a": "*"}}),
    "packages/b/index.js": "import { a } from '@x/a';\n",
    "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
    "crates/c/Cargo.toml": '[package]\nname = "c"\n',
    "crates/c/src/lib.rs": "pub fn c() {}\n",
}


def _builder(root: Path) -> GraphBuilder:
    """A graph built over *root*'s files, which collects their build units."""
    for rel, text in _REPO.items():
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).write_text(text, encoding="utf-8")
    builder = GraphBuilder(repo_path=root)
    for rel in _REPO:
        if rel.endswith((".js", ".rs")):
            language = "javascript" if rel.endswith(".js") else "rust"
            info = FileInfo(
                path=rel,
                abs_path=str(root / rel),
                language=language,
                size_bytes=1,
                git_hash="",
                last_modified=datetime.now(UTC),
                is_test=False,
                is_config=False,
                is_api_contract=False,
                is_entry_point=False,
            )
            builder.add_file(
                ParsedFile(file_info=info, symbols=[], imports=[], exports=[], docstring=None)
            )
    builder.build()
    return builder


def test_the_graph_build_collects_units_from_its_warmed_context(tmp_path: Path) -> None:
    index = _builder(tmp_path).build_units
    assert index is not None
    assert {"npm:@x/a", "npm:@x/b", "cargo:c"} <= set(index.units)
    assert index.unit("npm:@x/b").deps == ("npm:@x/a",)
    assert index.file_owners["packages/b/index.js"] == ("npm:@x/b",)
    assert index.file_owners["crates/c/src/lib.rs"] == ("cargo:c",)


async def test_read_back_equals_the_collected_index(tmp_path: Path, async_session) -> None:
    builder = _builder(tmp_path)
    repo = await insert_repo(async_session)
    await persist_build_units(async_session, repo.id, builder)
    await async_session.commit()

    stored = await load_build_units(async_session, repo.id)
    assert stored == builder.build_units
    assert stored.units_owning("packages/b/index.js")[0].id == "npm:@x/b"
    assert [u.id for u in stored.dependents("npm:@x/a")] == ["npm:@x/b"]

    rehydrated = GraphBuilder.from_persisted([], [], build_units=stored)
    assert rehydrated.build_units == builder.build_units


def _index(*units: BuildUnit, owners: dict[str, tuple[str, ...]]) -> BuildUnitIndex:
    return BuildUnitIndex(units={u.id: u for u in units}, file_owners=owners)


_A = BuildUnit("npm:a", "npm", "a", "a", "a/package.json", "npm-workspace")
_B = BuildUnit("npm:b", "npm", "b", "b", "b/package.json", "npm-workspace", deps=("npm:a",))


async def test_replace_drops_what_the_new_index_lacks_and_leaves_other_repos(async_session) -> None:
    repo = await insert_repo(async_session)
    other = await insert_repo(async_session, name="other", local_path="/tmp/other")
    first = _index(_A, _B, owners={"a/x.js": ("npm:a",), "b/y.js": ("npm:b",), "b/z.js": ("npm:b",)})
    await replace_build_units(async_session, repo.id, first)
    await replace_build_units(async_session, other.id, first)

    second = _index(_A, owners={"a/x.js": ("npm:a",)})
    assert await replace_build_units(async_session, repo.id, second) == 1
    await async_session.commit()

    assert await load_build_units(async_session, repo.id) == second
    assert await load_build_units(async_session, other.id) == first


async def test_a_file_with_several_owners_and_bulk_sizes_round_trip(async_session) -> None:
    repo = await insert_repo(async_session)
    owners = {f"a/f{i}.js": ("npm:a", "npm:b") for i in range(1100)}
    index = _index(_A, _B, owners=owners)
    await replace_build_units(async_session, repo.id, index)
    await async_session.commit()

    count = (await async_session.execute(select(func.count()).select_from(BuildUnitFile))).scalar_one()
    assert count == 2200
    assert await load_build_units(async_session, repo.id) == index


async def test_nothing_stored_reads_as_an_empty_index(async_session) -> None:
    repo = await insert_repo(async_session)
    assert await load_build_units(async_session, repo.id) == BuildUnitIndex()


async def test_a_builder_without_units_leaves_the_stored_ones(async_session) -> None:
    repo = await insert_repo(async_session)
    index = _index(_A, owners={"a/x.js": ("npm:a",)})
    await replace_build_units(async_session, repo.id, index)
    await persist_build_units(async_session, repo.id, GraphBuilder.from_persisted([], []))
    await async_session.commit()
    assert await load_build_units(async_session, repo.id) == index


def test_keys_are_the_reviewed_ones() -> None:
    keys = {model.__tablename__: [c.name for c in model.__table__.primary_key] for model in _TABLES}
    assert keys == {
        "build_units": ["repository_id", "unit_id"],
        "build_unit_deps": ["repository_id", "unit_id", "dep_unit_id"],
        "build_unit_files": ["repository_id", "path", "unit_id"],
    }
    index = next(iter(BuildUnitFile.__table__.indexes))
    assert [c.name for c in index.columns] == ["repository_id", "unit_id"]


@pytest.mark.parametrize("warm_start", [False, True])
def test_the_migration_creates_and_drops_the_tables(tmp_path: Path, warm_start: bool) -> None:
    path = tmp_path / "wiki.db"
    config = _alembic_config(path)
    command.upgrade(config, "0126")
    if warm_start:
        # init_db's create_all may create the tables before the migration runs.
        async def warm() -> None:
            engine = create_engine(f"sqlite+aiosqlite:///{path}")
            try:
                await init_db(engine)
            finally:
                await engine.dispose()

        asyncio.run(warm())

    command.upgrade(config, "0129")
    with sqlite3.connect(path) as connection:
        for model in _TABLES:
            table = model.__tablename__
            columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
            assert {row[1] for row in columns} == {c.name for c in model.__table__.columns}
            assert [row[1] for row in sorted(columns, key=lambda r: r[5]) if row[5]] == [
                c.name for c in model.__table__.primary_key
            ]
        indexes = {row[1] for row in connection.execute("PRAGMA index_list(build_unit_files)")}
        assert "ix_build_unit_files_repo_unit" in indexes

    command.downgrade(config, "0126")
    with sqlite3.connect(path) as connection:
        for model in _TABLES:
            assert connection.execute(f"PRAGMA table_info({model.__tablename__})").fetchall() == []
