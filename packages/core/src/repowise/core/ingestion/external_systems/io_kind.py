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
    # JVM (dotted-prefix or bare-segment keys; the import classifier emits both
    # progressive dotted prefixes and interior path segments).
    "java.sql", "javax.sql", "org.hibernate", "jakarta.persistence",
    "javax.persistence", "org.springframework.data", "com.zaxxer.hikari",
    "org.jooq", "org.mybatis", "org.jdbi",
    # Go (module paths resolve via their interior segment, e.g.
    # github.com/go-redis/redis -> redis, already a db name above).
    "database/sql", "gorm", "sqlx", "pgx", "mongo-driver",
    # .NET (progressive dotted prefixes of the namespace).
    "microsoft.entityframeworkcore", "system.data.sqlclient",
    "microsoft.data.sqlclient", "dapper", "npgsql", "mongodb.driver",
    "stackexchange.redis",
    # Rust (the crate root as imported, ``use sqlx::...`` -> ``sqlx``; both the
    # hyphenated Cargo name and the underscore import alias are seeded). ``sqlx``
    # is already listed under the Go section above.
    "diesel", "sea-orm", "sea_orm", "tokio-postgres", "tokio_postgres",
    "rusqlite", "deadpool-postgres", "deadpool_postgres", "scylla", "bb8",
    # Dart / Flutter (pub package names; ``package:sqflite/...`` resolves via
    # the interior segment).
    "sqflite", "postgres", "mysql1", "mongo_dart", "drift",
    # Scala (root package as imported: ``import slick.jdbc...`` -> ``slick``,
    # ``import doobie._`` -> ``doobie``; Quill imports as ``io.getquill``).
    "slick", "doobie", "scalikejdbc", "anorm", "io.getquill",
    # Ruby (require-feature names; ``pg`` / ``mysql2`` / ``redis`` above are
    # shared with the Node ecosystem).
    "activerecord", "active_record", "sequel", "mongoid", "sqlite3", "mongo",
})

_NETWORK_NAMES: frozenset[str] = frozenset({
    # Python (socket is the canonical network boundary, not filesystem)
    "httpx", "requests", "aiohttp", "urllib3", "websockets", "websocket-client",
    "grpcio", "httpcore", "tornado", "treq", "niquests", "socket",
    # TS / Node
    "axios", "node-fetch", "got", "superagent", "undici", "ky", "needle",
    "request", "@grpc/grpc-js", "ws", "socket.io-client", "graphql-request",
    # JVM
    "java.net.http", "okhttp3", "retrofit2", "feign", "org.apache.http",
    "org.apache.hc", "org.springframework.web",
    # Go (interior segments / module names)
    "net/http", "grpc", "resty", "fasthttp",
    # .NET
    "system.net.http", "grpc.net.client", "restsharp", "flurl",
    # Rust (HTTP / gRPC client crates as imported).
    "reqwest", "hyper", "isahc", "surf", "ureq", "awc", "tonic",
    # Dart / Flutter (``package:http`` doubles as Node's stdlib ``http`` —
    # both are network boundaries).
    "http", "dio", "chopper",
    # Scala (root packages / progressive dotted prefixes: ``import
    # sttp.client3._`` -> ``sttp``; ``import org.http4s...`` -> ``org.http4s``;
    # ``import akka.http.scaladsl...`` -> ``akka.http``).
    "sttp", "org.http4s", "akka.http", "play.api.libs.ws",
    # Ruby (require-feature names; ``require "net/http"`` resolves via its
    # ``http`` segment above).
    "httparty", "faraday", "rest-client", "rest_client", "typhoeus", "excon",
})

_FILESYSTEM_NAMES: frozenset[str] = frozenset({
    # Python (mostly stdlib, dormant until an import-resolution consumer lands)
    "open", "aiofiles", "watchdog", "pathlib", "shutil", "fsspec",
    # TS / Node
    "node:fs", "fs", "fs-extra", "graceful-fs", "chokidar", "node:path",
    # JVM
    "java.nio.file", "java.io",
    # Go (interior segment of golang.org/x/... etc.; os/exec is subprocess)
    "io/ioutil",
    # .NET
    "system.io",
    # Dart (``dart:io`` hosts File/Directory; Process is method-gated in the
    # perf dialect rather than classified wholesale as subprocess).
    "dart:io",
    # Scala (``import scala.io.Source`` -> the ``scala.io`` dotted prefix;
    # lihaoyi's os-lib root package ``os`` is deliberately NOT seeded; the
    # bare name would collide with Python's stdlib ``os`` cross-ecosystem, so
    # the Scala perf dialect method-gates it instead).
    "scala.io",
    # Ruby (require-feature names)
    "fileutils", "tempfile",
})

_SUBPROCESS_NAMES: frozenset[str] = frozenset({
    # Python (stdlib + popular wrappers)
    "subprocess", "sh", "pexpect", "plumbum", "invoke",
    # TS / Node
    "child_process", "node:child_process", "execa", "cross-spawn", "shelljs",
    # Go (os/exec resolves via the interior segment ``exec``)
    "os/exec",
    # Ruby (require-feature names)
    "open3", "pty",
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
