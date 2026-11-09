"""Plugin storage revisions: tables a plugin owns, versioned apart from core's Alembic chain.

``init_db`` applies each registered revision not yet in ``plugin_storage_revisions``
after the core schema, in its own transaction under the writer lock. A changed
checksum fails every open; a store that cannot take the write leaves the plugin
``pending``. The contract for plugin authors is in ``docs/reference/PLUGINS.md``
under "Storage revisions".
"""

from __future__ import annotations

import logging
import re
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import Column, MetaData, String, Table, inspect, select, text
from sqlalchemy.exc import DBAPIError

from repowise.core.registry.plugins import PluginRecord, note_registration

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

_log = logging.getLogger("repowise.plugins")
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}")
_CHECKSUM = re.compile(r"[0-9a-f]{64}")
#: PostgreSQL advisory lock key held while a transaction applies a revision.
_PG_LOCK_KEY = 0x7277_706C_7374_6F72  # "rwplstor"

ledger = Table(
    "plugin_storage_revisions",
    MetaData(),
    Column("namespace", String(128), primary_key=True),
    Column("revision", String(128), primary_key=True),
    Column("checksum", String(64), nullable=False),
)


class StorageMigrationError(RuntimeError):
    """A plugin storage revision failed, or changed after it was applied."""


@dataclass(frozen=True)
class StorageMigration:
    """One revision of a plugin's tables. ``checksum`` is a lowercase SHA-256 hex digest."""

    namespace: str
    revision: str
    checksum: str
    apply: Callable[[AsyncConnection], Awaitable[None]]


def _write_refused(exc: DBAPIError) -> str | None:
    """Why the store could not take the write, matched by error code; None otherwise."""
    orig = exc.orig
    code = getattr(orig, "sqlite_errorcode", None)
    if code is not None:
        primary = code & 0xFF
        if primary == sqlite3.SQLITE_READONLY:
            return "the store is read-only"
        if primary == sqlite3.SQLITE_BUSY:
            return "another writer holds the store"
        return None
    if getattr(orig, "sqlstate", None) == "25006":  # PostgreSQL read-only transaction
        return "the store is read-only"
    return None


def _store(conn: AsyncConnection) -> str:
    """The store behind *conn*, as its URL with any password hidden."""
    return conn.engine.url.render_as_string(hide_password=True)


async def applied_revisions(conn: AsyncConnection) -> dict[tuple[str, str], str]:
    """``(namespace, revision) -> checksum`` from the ledger; empty when it is absent."""
    if not await conn.run_sync(lambda c: inspect(c).has_table(ledger.name)):
        return {}
    rows = await conn.execute(select(ledger))
    return {(row.namespace, row.revision): row.checksum for row in rows}


class StorageMigrationRegistry:
    """Registered revisions, in registration order within each namespace."""

    def __init__(self) -> None:
        self._revisions: dict[tuple[str, str], StorageMigration] = {}
        self._owners: dict[str, PluginRecord] = {}
        #: ``(store, namespace)`` to why its revisions could not apply there.
        self._pending: dict[tuple[str, str], str] = {}

    def register(self, migration: StorageMigration) -> None:
        """Add *migration*. Registering an identical revision again is a no-op."""
        key = (migration.namespace, migration.revision)
        name = f"{key[0]}:{key[1]}"
        for value in key:
            if not _NAME.fullmatch(value):
                raise ValueError(f"invalid storage revision name {value!r}")
        if not _CHECKSUM.fullmatch(migration.checksum):
            raise ValueError(f"storage revision {name} needs a lowercase SHA-256 hex checksum")
        existing = self._revisions.get(key)
        if existing is not None:
            if existing.checksum != migration.checksum:
                raise ValueError(
                    f"storage revision {name} is already registered with another checksum"
                )
            return
        self._revisions[key] = migration
        owner = note_registration("storage-migration", name)
        if owner is not None:
            self._owners.setdefault(migration.namespace, owner)

    def revisions(self) -> list[StorageMigration]:
        """Every revision, namespaces sorted, registration order within each."""
        return sorted(self._revisions.values(), key=lambda m: m.namespace)

    def owner(self, namespace: str) -> PluginRecord | None:
        return self._owners.get(namespace)

    def signature(self) -> str:
        """The registered revisions as text, the same whatever order plugins loaded in."""
        return ";".join(f"{m.namespace}:{m.revision}:{m.checksum}" for m in self.revisions())

    async def behind(self, conn: AsyncConnection) -> bool:
        """Whether a revision changed after it applied, or is unapplied and not known pending."""
        if not self._revisions:
            return False
        applied = await applied_revisions(conn)
        store = _store(conn)
        for key, migration in self._revisions.items():
            recorded = applied.get(key)
            if recorded is not None and recorded != migration.checksum:
                return True
            if recorded is None and (store, key[0]) not in self._pending:
                return True
        return False

    async def apply_pending(self, engine: AsyncEngine) -> bool:
        """Apply every unapplied revision; return False when one is left pending."""
        if not self._revisions:
            return True
        async with engine.connect() as conn:
            applied = await applied_revisions(conn)
            await conn.rollback()  # end the read before taking the writer lock
            for key, checksum in applied.items():
                self._check(key, checksum)
            dialect, store = conn.dialect.name, _store(conn)
            blocked: set[str] = set()
            for migration in self.revisions():
                key = (migration.namespace, migration.revision)
                if key in applied or migration.namespace in blocked:
                    continue
                try:
                    async with conn.begin():
                        if dialect == "sqlite":
                            await conn.exec_driver_sql("BEGIN IMMEDIATE")
                        elif dialect == "postgresql":
                            await conn.execute(
                                text("SELECT pg_advisory_xact_lock(:key)"), {"key": _PG_LOCK_KEY}
                            )
                        await conn.run_sync(ledger.metadata.create_all)
                        # Another process may have applied it while this one waited.
                        recorded = await conn.scalar(
                            select(ledger.c.checksum).where(
                                ledger.c.namespace == key[0], ledger.c.revision == key[1]
                            )
                        )
                        if recorded is not None:
                            self._check(key, recorded)
                            continue
                        await migration.apply(conn)
                        await conn.execute(
                            ledger.insert().values(
                                namespace=key[0], revision=key[1], checksum=migration.checksum
                            )
                        )
                except StorageMigrationError:
                    raise
                except DBAPIError as exc:
                    reason = _write_refused(exc)
                    if reason is None:
                        raise self._failed(key, exc) from exc
                    blocked.add(migration.namespace)
                    self._mark_pending(store, key, reason)
                except Exception as exc:
                    raise self._failed(key, exc) from exc
            return not blocked

    def _label(self, key: tuple[str, str]) -> str:
        owner = self._owners.get(key[0])
        return f"{key[0]}:{key[1]}" + (f" of plugin {owner.name}" if owner else "")

    def _check(self, key: tuple[str, str], recorded: str) -> None:
        migration = self._revisions.get(key)
        if migration is not None and migration.checksum != recorded:
            raise StorageMigrationError(
                f"storage revision {self._label(key)} changed after it was applied "
                f"(recorded checksum {recorded}, registered {migration.checksum})"
            )

    def _failed(self, key: tuple[str, str], exc: Exception) -> StorageMigrationError:
        return StorageMigrationError(f"storage revision {self._label(key)} failed: {exc}")

    def _mark_pending(self, store: str, key: tuple[str, str], reason: str) -> None:
        message = f"storage revision {key[0]}:{key[1]} pending: {reason}"
        self._pending[(store, key[0])] = message
        owner = self._owners.get(key[0])
        if owner is not None and owner.status == "loaded":
            owner.status = "pending"
            owner.error = message
        _log.warning("repowise plugin %s: %s (%s)", owner.name if owner else key[0], message, store)


storage_migrations = StorageMigrationRegistry()

__all__ = [
    "StorageMigration",
    "StorageMigrationError",
    "StorageMigrationRegistry",
    "applied_revisions",
    "ledger",
    "storage_migrations",
]
