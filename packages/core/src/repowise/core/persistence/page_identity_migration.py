"""Key wiki pages by ``(repository_id, id)`` on a store keyed by ``id`` alone.

A page id is ``f"{page_type}:{target_path}"``, which names no repository, so a
database several repositories share (``REPOWISE_DB_URL``) held one row per id:
the second repository to write ``file_page:src/main.py`` updated the first
repository's row in place instead of writing its own. The model now keys
``wiki_pages`` by ``(repository_id, id)`` and ``wiki_page_versions`` refers to
a page by the same pair. This module moves an existing store onto that shape.

Two callers share it: ``init_db`` (every local SQLite store, and a PostgreSQL
store that never ran Alembic) and Alembic revision ``0156``. Both run it on a
synchronous connection inside their own transaction.

Rows written before the fix are carried over as they are. A page the old code
overwrote with another repository's content still holds that content under its
original repository: the migration cannot tell whose prose it is, so the
affected repositories need a re-index to get their own pages back.
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import Index, MetaData, PrimaryKeyConstraint, Table, inspect, text
from sqlalchemy.schema import CreateIndex, CreateTable, ForeignKeyConstraint

from .models import Page, PageVersion

log = structlog.get_logger(__name__)

PAGES = Page.__tablename__
VERSIONS = PageVersion.__tablename__
#: The primary key the model declares, in order.
COMPOSITE_KEY = ("repository_id", "id")
#: The name both shapes give the primary key, matching PostgreSQL's default.
PK_NAME = "wiki_pages_pkey"
#: The composite foreign key's name, and the single-column one it replaces.
FK_NAME = "fk_wiki_page_versions_page"
_LEGACY_FK_NAME = "wiki_page_versions_page_id_fkey"
#: Only the composite shape has this index; the legacy shape never declared it.
_ID_INDEX = "ix_wiki_pages_id"


def page_key_columns(connection: Any, inspector: Any = None) -> tuple[str, ...]:
    """The live primary key of ``wiki_pages``; ``()`` when there is no table."""
    inspector = inspector if inspector is not None else inspect(connection)
    if PAGES not in inspector.get_table_names():
        return ()
    return tuple(inspector.get_pk_constraint(PAGES).get("constrained_columns") or ())


def needs_rekey(connection: Any, inspector: Any = None) -> bool:
    """Whether ``wiki_pages`` exists and is still keyed by ``id`` alone."""
    columns = page_key_columns(connection, inspector)
    return bool(columns) and columns != COMPOSITE_KEY


def rekey_pages(connection: Any, inspector: Any = None) -> bool:
    """Move ``wiki_pages`` and ``wiki_page_versions`` to the composite key.

    Returns whether anything changed. Idempotent: a store already on the
    composite key, or one without the tables, is left alone.

    Columns the model declares must already exist on both tables (``init_db``
    adds them first); a column only the live table has, such as PostgreSQL's
    ``embedding``, is kept on PostgreSQL, where the change happens in place,
    and copied when present on SQLite.

    *inspector* is the caller's, when it already has one for *connection*.
    """
    if not needs_rekey(connection, inspector):
        return False
    _align_version_owners(connection)
    if connection.dialect.name == "sqlite":
        _rebuild_sqlite(connection, composite=True)
    else:
        _alter_postgres(connection, composite=True)
    log.info("wiki_pages_rekeyed", key=",".join(COMPOSITE_KEY))
    return True


def unkey_pages(connection: Any) -> None:
    """Return to the legacy ``id`` primary key (Alembic downgrade only).

    Refuses when two repositories hold the same page id, because the legacy
    key cannot hold both and dropping either is data loss nobody asked for.
    """
    if page_key_columns(connection) != COMPOSITE_KEY:
        return
    shared = connection.execute(
        text(f"SELECT id FROM {PAGES} GROUP BY id HAVING count(*) > 1 LIMIT 5")
    ).scalars().all()
    if shared:
        raise RuntimeError(
            "Cannot downgrade: several repositories hold the same page id "
            f"(for example {', '.join(map(repr, shared))}); the id-only key cannot hold them."
        )
    if connection.dialect.name == "sqlite":
        _rebuild_sqlite(connection, composite=False)
    else:
        _alter_postgres(connection, composite=False)


def _align_version_owners(connection: Any) -> None:
    """Give every version the repository of the page it belongs to.

    Run while ids are still unique, so the page an old version names is
    unambiguous. The old writer took the owner from the page row, so this is
    normally a no-op; it covers rows another tool wrote. A version whose page
    is gone cannot satisfy any foreign key and could never be read through
    the page, so it is dropped (the old key forbade it; only a store written
    with enforcement off can hold one).
    """
    fixed = connection.execute(
        text(
            f"UPDATE {VERSIONS} SET repository_id = "
            f"(SELECT p.repository_id FROM {PAGES} p WHERE p.id = {VERSIONS}.page_id) "
            f"WHERE EXISTS (SELECT 1 FROM {PAGES} p WHERE p.id = {VERSIONS}.page_id "
            f"AND p.repository_id != {VERSIONS}.repository_id)"
        )
    ).rowcount
    orphans = connection.execute(
        text(
            f"DELETE FROM {VERSIONS} WHERE NOT EXISTS "
            f"(SELECT 1 FROM {PAGES} p WHERE p.id = {VERSIONS}.page_id)"
        )
    ).rowcount
    if fixed or orphans:
        log.warning("wiki_page_versions_aligned", realigned=fixed or 0, dropped_orphans=orphans or 0)


def _shaped_tables(connection: Any, composite: bool) -> tuple[Table, Table]:
    """The two live tables as they are on disk, keyed one way or the other.

    Reflected rather than taken from the models: the rebuild changes the keys
    and nothing else, so a store keeps exactly the columns, types, defaults
    and indexes it has (a later model column belongs to a later migration),
    and a column only the store has is carried over, not dropped.
    """
    live = MetaData()
    reflected = {
        name: Table(name, live, autoload_with=connection) for name in (PAGES, VERSIONS)
    }
    shaped = MetaData()
    # Referenced by the page table's foreign key, so its DDL can compile; never created.
    for table in live.tables.values():
        if table.name not in reflected:
            table.to_metadata(shaped)
    return (
        _reshape(reflected[PAGES], shaped, composite=composite),
        _reshape(reflected[VERSIONS], shaped, composite=composite),
    )


def _reshape(source: Table, metadata: MetaData, *, composite: bool) -> Table:
    columns = []
    for column in source.columns:
        copy = column._copy()
        copy.primary_key = False
        columns.append(copy)
    args: list[Any] = []
    for constraint in source.constraints:
        if isinstance(constraint, PrimaryKeyConstraint):
            continue
        if isinstance(constraint, ForeignKeyConstraint) and constraint.referred_table.name == PAGES:
            continue
        args.append(constraint._copy())
    if source.name == PAGES:
        key = COMPOSITE_KEY if composite else ("id",)
        args.append(PrimaryKeyConstraint(*key, name=PK_NAME))
    else:
        args.append(PrimaryKeyConstraint("id"))
        if composite:
            args.append(
                ForeignKeyConstraint(
                    ["repository_id", "page_id"],
                    [f"{PAGES}.repository_id", f"{PAGES}.id"],
                    name=FK_NAME,
                )
            )
        else:
            args.append(ForeignKeyConstraint(["page_id"], [f"{PAGES}.id"], name=_LEGACY_FK_NAME))
    table = Table(source.name, metadata, *columns, *args)
    for index in source.indexes:
        if index.name == _ID_INDEX:
            continue
        Index(index.name, *(table.c[c.name] for c in index.columns), unique=index.unique)
    if composite and source.name == PAGES:
        Index(_ID_INDEX, table.c.id)
    return table


def _rebuild_sqlite(connection: Any, *, composite: bool) -> None:
    """SQLite cannot change a key in place: rename, recreate, copy, drop.

    The old tables are renamed out of the way first (SQLite rewrites the
    version table's reference to follow the rename), the new ones are created
    under the real names, rows are copied parent first so an enforced key
    accepts every child row, and only then are the old tables dropped, child
    first. Indexes keep their names across a rename, so the new ones are
    created after the old tables (and their indexes) are gone.
    """
    pages, versions = _shaped_tables(connection, composite)
    run = connection.execute
    shared = {table.name: [c.name for c in table.columns] for table in (pages, versions)}
    old = {table.name: f"_rekey_old_{table.name}" for table in (pages, versions)}
    for name in old.values():
        run(text(f'DROP TABLE IF EXISTS "{name}"'))
    run(text(f'ALTER TABLE "{VERSIONS}" RENAME TO "{old[VERSIONS]}"'))
    run(text(f'ALTER TABLE "{PAGES}" RENAME TO "{old[PAGES]}"'))
    for table in (pages, versions):
        run(CreateTable(table))
        columns = ", ".join(f'"{c}"' for c in shared[table.name])
        run(
            text(
                f'INSERT INTO "{table.name}" ({columns}) '
                f'SELECT {columns} FROM "{old[table.name]}"'
            )
        )
    run(text(f'DROP TABLE "{old[VERSIONS]}"'))
    run(text(f'DROP TABLE "{old[PAGES]}"'))
    for table in (pages, versions):
        for index in table.indexes:
            run(CreateIndex(index))


def _alter_postgres(connection: Any, *, composite: bool) -> None:
    """PostgreSQL changes both keys in place, inside the caller's transaction."""
    inspector = inspect(connection)
    run = connection.execute
    for fk in inspector.get_foreign_keys(VERSIONS):
        if fk.get("referred_table") == PAGES and fk.get("name"):
            run(text(f'ALTER TABLE "{VERSIONS}" DROP CONSTRAINT "{fk["name"]}"'))
    pk_name = inspector.get_pk_constraint(PAGES).get("name") or PK_NAME
    run(text(f'ALTER TABLE "{PAGES}" DROP CONSTRAINT "{pk_name}"'))
    if composite:
        run(text(f'ALTER TABLE "{PAGES}" ADD CONSTRAINT "{PK_NAME}" PRIMARY KEY (repository_id, id)'))
        run(text(f'CREATE INDEX IF NOT EXISTS "{_ID_INDEX}" ON "{PAGES}" (id)'))
        run(
            text(
                f'ALTER TABLE "{VERSIONS}" ADD CONSTRAINT "{FK_NAME}" '
                f"FOREIGN KEY (repository_id, page_id) REFERENCES {PAGES} (repository_id, id)"
            )
        )
    else:
        run(text(f'ALTER TABLE "{PAGES}" ADD CONSTRAINT "{PK_NAME}" PRIMARY KEY (id)'))
        run(text(f'DROP INDEX IF EXISTS "{_ID_INDEX}"'))
        run(
            text(
                f'ALTER TABLE "{VERSIONS}" ADD CONSTRAINT "{_LEGACY_FK_NAME}" '
                f"FOREIGN KEY (page_id) REFERENCES {PAGES} (id)"
            )
        )
