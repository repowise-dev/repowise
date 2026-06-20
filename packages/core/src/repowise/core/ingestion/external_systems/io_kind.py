"""I/O-boundary classifier: tag a dependency by the *kind* of side effect it
performs at a process boundary.

``io_kind in {db, network, filesystem, subprocess, lock}`` upgrades each entry
in the dependency registry from "a library" to a *database* / *network API* /
*filesystem* / *subprocess* / *lock* boundary. This is a **shared** primitive:
the C4 architecture view types its external nodes from it today, and a future
performance / security / conformance layer reuses the same table (resolve a
call site to its imported origin, then classify that origin here).

Design rules (mirror :mod:`.classifier`):
    - Conservative. An unknown name returns ``None`` (not a guess) so every
      downstream consumer degrades gracefully: a null ``io_kind`` must never
      break rendering or analysis.
    - The seed tables cover third-party packages *and* the stdlib modules a
      future import-resolution pass will hand us (``subprocess``, ``socket``,
      ``node:fs``, ``child_process``). Registry rows only ever carry declared
      third-party deps, so the stdlib entries are dormant until that consumer
      lands; they cost nothing and keep the classifier complete.
    - Cross-ecosystem by name: Python and the TS/Node ecosystem share one
      table, keyed on the lowercased dependency name.

``IO_KINDS`` is the canonical set of values. It is mirrored in
``packages/types/src/external-systems.ts`` (``C4_IO_KINDS``) and guarded by a
cross-language parity test (``packages/types/__tests__/contracts.test.ts`` +
``tests/unit/ingestion/test_io_kind.py``).
"""

from __future__ import annotations

#: Canonical, frozen set of boundary kinds. If this changes, the TS mirror
#: (``C4_IO_KINDS``) and both parity tests must change too.
IO_KINDS: tuple[str, ...] = ("db", "network", "filesystem", "subprocess", "lock")

# ---------------------------------------------------------------------------
# Seed tables: name (lowercased) -> io_kind. Python + TS/Node ecosystems.
# ---------------------------------------------------------------------------

_DB_NAMES: frozenset[str] = frozenset({
    # Python
    "sqlalchemy", "psycopg", "psycopg2", "psycopg2-binary", "asyncpg",
    "aiomysql", "aiosqlite", "mysqlclient", "pymysql", "mysql-connector-python",
    "redis", "aioredis", "pymongo", "motor", "mongoengine", "cassandra-driver",
    "elasticsearch", "neo4j", "sqlmodel", "peewee", "tortoise-orm", "databases",
    "duckdb", "clickhouse-driver", "pyodbc",
    # TS / Node (cassandra-driver above is published in both ecosystems)
    "ioredis", "mongoose", "mongodb", "@prisma/client", "prisma",
    "drizzle-orm", "knex", "pg", "mysql", "mysql2", "sequelize", "typeorm",
    "better-sqlite3", "@elastic/elasticsearch", "@planetscale/database", "kysely",
})

_NETWORK_NAMES: frozenset[str] = frozenset({
    # Python (socket is the canonical network boundary, not filesystem)
    "httpx", "requests", "aiohttp", "urllib3", "websockets", "websocket-client",
    "grpcio", "httpcore", "tornado", "treq", "niquests", "socket",
    # TS / Node
    "axios", "node-fetch", "got", "superagent", "undici", "ky", "needle",
    "request", "@grpc/grpc-js", "ws", "socket.io-client", "graphql-request",
})

_FILESYSTEM_NAMES: frozenset[str] = frozenset({
    # Python (mostly stdlib, dormant until an import-resolution consumer lands)
    "open", "aiofiles", "watchdog", "pathlib", "shutil", "fsspec",
    # TS / Node
    "node:fs", "fs", "fs-extra", "graceful-fs", "chokidar", "node:path",
})

_SUBPROCESS_NAMES: frozenset[str] = frozenset({
    # Python (stdlib + popular wrappers)
    "subprocess", "sh", "pexpect", "plumbum", "invoke",
    # TS / Node
    "child_process", "node:child_process", "execa", "cross-spawn", "shelljs",
})

_LOCK_NAMES: frozenset[str] = frozenset({
    # Python (stdlib threading/async primitives + distributed locks).
    # ``redlock`` is published in both the Python and Node ecosystems.
    "threading", "filelock", "fasteners", "redlock", "python-redis-lock",
    # TS / Node
    "async-mutex", "proper-lockfile",
})

# Name -> io_kind, built once. A name appearing in two tables would be a bug;
# later tables do not silently win because we assert disjointness below.
_BY_NAME: dict[str, str] = {}
for _kind, _names in (
    ("db", _DB_NAMES),
    ("network", _NETWORK_NAMES),
    ("filesystem", _FILESYSTEM_NAMES),
    ("subprocess", _SUBPROCESS_NAMES),
    ("lock", _LOCK_NAMES),
):
    for _name in _names:
        # ``redlock`` legitimately appears under lock in both ecosystems; keep
        # the first assignment and skip any duplicate of the *same* kind.
        if _name in _BY_NAME and _BY_NAME[_name] != _kind:
            raise AssertionError(
                f"io_kind seed collision: {_name!r} is both "
                f"{_BY_NAME[_name]!r} and {_kind!r}"
            )
        _BY_NAME[_name] = _kind


def classify_io_kind(name: str) -> str | None:
    """Return the :data:`IO_KINDS` boundary for ``name``, or ``None``.

    ``name`` is a dependency / import name as it appears in a manifest or
    import statement (e.g. ``"httpx"``, ``"@prisma/client"``, ``"node:fs"``).
    Unknown names return ``None``; callers must treat that as "untyped".
    """
    if not name:
        return None
    return _BY_NAME.get(name.strip().lower())
