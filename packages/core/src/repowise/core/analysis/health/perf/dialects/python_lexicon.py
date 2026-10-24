"""Python call, method and node-kind vocabularies the perf dialect matches on."""

from __future__ import annotations

import builtins
import re

# Network round-trip verbs (shared across languages that name HTTP methods).
HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "send", "stream"}
)

# Python DBAPI / SQLAlchemy execution sinks, split into three strata so the
# ambiguous accessors can be gated harder than the unambiguous ones (see
# ``sink_kind``).
PY_DB_UNAMBIGUOUS: frozenset[str] = frozenset({"fetchone", "fetchall", "fetchmany"})
# DB verbs other clients spell too: GitPython's ``repo.commit()``, an HTTP or
# GraphQL SDK's ``request.execute()``, a builder's ``.scalar()``.
PY_DB_NEEDS_EVIDENCE: frozenset[str] = frozenset(
    {"commit", "execute", "executemany", "scalars", "scalar", "scalar_one", "scalar_one_or_none"}
)
# Without a db import, the call itself can still show it runs a query: a
# PostgREST chain (``client.table("t")...execute()``) or a statement argument
# (``session.execute(select(...))``, ``cur.execute("SELECT ...")``).
_PY_QUERY_CHAIN_METHODS: frozenset[str] = frozenset({"table", "from_"})
_PY_STATEMENT_BUILDERS: frozenset[str] = frozenset({"select", "insert", "update", "delete", "text"})
_PY_SQL_LITERAL_RE = re.compile(
    r"^[rRuUbBfF]*[\"']+\s*(?:SELECT|INSERT|UPDATE|DELETE|WITH|CREATE|DROP|ALTER|PRAGMA)\b",
    re.IGNORECASE,
)
# Schema statements run once per table, index or setting the code names, however
# many rows those tables hold: a loop of them is no N+1 and has no batch form.
_PY_SCHEMA_SQL_RE = re.compile(
    r"^[rRuUbBfF]*[\"']+\s*(?:CREATE|DROP|ALTER|TRUNCATE|PRAGMA|VACUUM|REINDEX|ANALYZE"
    r"|COMMENT\s+ON|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
PY_DB_AMBIGUOUS: frozenset[str] = frozenset({"all", "first", "one", "one_or_none"})
PY_SUBPROC_METHODS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)
# Any of these anywhere in a read's call chain proves it is bounded, gating
# ``unbounded_read_reduced_in_memory`` (analysis.health.perf.unbounded_reduction).
PY_UNBOUNDED_READ_BOUND_METHODS: frozenset[str] = frozenset(
    {"limit", "range", "single", "maybe_single", "first", "count", "head", "aggregate"}
)

# Matched on the method name alone: only ``pathlib.Path`` spells these, and the
# receiver is usually a local (``src_path.read_text()``) that ``io_names`` cannot
# classify. Metadata calls (``exists`` / ``stat`` / ``glob``) are cheap and left out.
PY_FS_METHODS: frozenset[str] = frozenset(
    {"read_text", "read_bytes", "write_text", "write_bytes"}
)
# ``os`` / ``shutil`` verbs, gated on the literal module root. Ceiling: the root is
# the leftmost name of the whole chain, so ``os.path.join(a).verb()`` lands here too;
# no verb a ``str`` also has (``replace``) may join. Upgrade: require two segments.
PY_OS_FS_METHODS: frozenset[str] = frozenset(
    {
        "walk",
        "listdir",
        "scandir",
        "remove",
        "unlink",
        "rename",
        "makedirs",
        "mkdir",
        "rmdir",
        "removedirs",
    }
)
PY_SHUTIL_FS_METHODS: frozenset[str] = frozenset(
    {
        "copy",
        "copy2",
        "copyfile",
        "copytree",
        "move",
        "rmtree",
        "make_archive",
        "unpack_archive",
    }
)

# Python string-literal node kinds (f-strings parse as ``string`` too).
_PY_STRING_KINDS: frozenset[str] = frozenset({"string", "concatenated_string"})
_PY_AUG_ASSIGN_KINDS: frozenset[str] = frozenset({"augmented_assignment"})

# Connection / client / pool constructors, as ``(root, method)`` on a distinctive I/O library ...
_PY_RESOURCE_CTORS: frozenset[tuple[str, str]] = frozenset(
    {
        ("sqlite3", "connect"),
        ("psycopg", "connect"),
        ("psycopg2", "connect"),
        ("pymysql", "connect"),
        ("MySQLdb", "connect"),
        ("aiosqlite", "connect"),
        ("httpx", "Client"),
        ("httpx", "AsyncClient"),
        ("requests", "Session"),
        ("aiohttp", "ClientSession"),
        ("boto3", "client"),
        ("boto3", "resource"),
        ("redis", "Redis"),
        ("redis", "StrictRedis"),
        ("pymongo", "MongoClient"),
    }
)
# ... plus a few constructors distinctive enough on their own (the rightmost
# name), so an aliased ``from sqlalchemy import create_engine`` still resolves.
_PY_RESOURCE_METHODS: frozenset[str] = frozenset({"create_engine", "MongoClient"})

# Lock acquisition only; ``release`` would count the same critical section twice.
_PY_LOCK_METHODS: frozenset[str] = frozenset({"acquire"})

# RHS node kinds that prove a name is bound to a list (the membership gate).
_PY_LIST_RHS_KINDS: frozenset[str] = frozenset({"list", "list_comprehension"})
# Builtins whose call result is provably a list.
_PY_LIST_BUILTINS: frozenset[str] = frozenset({"list", "sorted"})
# RHS shapes of a set or dict: a name bound to one anywhere in the file leaves the membership gate.
_PY_NONLIST_RHS_KINDS: frozenset[str] = frozenset(
    {"set", "dictionary", "set_comprehension", "dictionary_comprehension"}
)
_PY_NONLIST_BUILTINS: frozenset[str] = frozenset({"set", "dict", "frozenset"})

# Promotion facts (perf/loop_facts.py). Calls and attributes that read a query
# result out without changing how many rows it holds.
_PY_RESULT_PROJECTIONS: frozenset[str] = frozenset(
    {"all", "scalars", "fetchall", "json", "data", "rows"}
)
# Raw SQL capped in its own text: ``sa.text("... LIMIT 1000")``.
_PY_SQL_LIMIT_RE = re.compile(rb"(?is)\bselect\b.*\blimit\s+\d+\b")
_PY_SQL_COMMENT_RE = re.compile(rb"--[^\n]*|/\*.*?\*/", re.S)
# A per-key call that limits or orders its rows is not the read one IN query makes.
_PY_PER_KEY_LIMITS: frozenset[str] = frozenset(
    {
        "limit", "single", "maybe_single", "first", "one", "scalar_one",
        "scalar_one_or_none", "order", "order_by", "range", "offset",
    }
)
_PY_ONE_ROW: frozenset[str] = _PY_PER_KEY_LIMITS | {"scalar"}
_PY_READ_CAPS: frozenset[str] = _PY_ONE_ROW - {"order", "order_by", "offset"}
_PY_WRITES: frozenset[str] = frozenset({"update", "insert", "upsert", "values"})
# Keys a query yields (``.in_(select(...))``) are as many as that table's rows.
_PY_KEY_READS: frozenset[str] = _PY_STATEMENT_BUILDERS | {
    "subquery", "scalar_subquery", "query", "cte", "alias", "union", "union_all",
    "execute", "table", "from_",
}
_PY_SCOPES: frozenset[str] = frozenset({"function_definition", "lambda", "class_definition"})
_PY_BUILTINS: frozenset[str] = frozenset(dir(builtins))
_PY_SEM_NAME_RE = re.compile(r"(?i)(sem|semaphore|limiter|limit)$")
# A model class is CapWords; an ALL_CAPS constant (``API_URL``) is not.
_PY_MODEL_NAME_RE = re.compile(r"^[A-Z]\w*[a-z]\w*$")
# Directory listings; the recursive ones walk a whole tree.
_PY_LISTINGS: frozenset[str] = frozenset(
    {
        "glob", "iglob", "rglob", "iterdir", "walk", "listdir", "scandir", "traverse",
        "iter_glob", "glob_via", "walk_repo", "_rglob",
    }
)
_PY_RECURSIVE_LISTINGS: frozenset[str] = frozenset(
    {"rglob", "walk", "traverse", "walk_repo", "_rglob"}
)
# Reads that yield lines or chunks as they arrive.
_PY_STREAMS: frozenset[str] = frozenset(
    {
        "readlines", "iter_lines", "iter_content", "iter_bytes", "iter_text",
        "aiter_lines", "aiter_bytes", "aiter_text", "aiter_raw",
    }
)
# Wrappers and chunkers whose first argument is what the loop walks.
_PY_PASS_THROUGH: frozenset[str] = frozenset(
    {
        "enumerate", "sorted", "reversed", "list", "set", "tuple", "iter", "zip",
        "batched", "chunked", "ichunked", "chunks", "iter_chunks", "grouper",
    }
)
_PY_CONSTANT_RHS: frozenset[str] = frozenset({"list", "tuple", "set", "dictionary"})


_PY_DEF_KINDS = frozenset({"function_definition", "lambda", "class_definition"})
# Nodes an identifier sits in when it is the name being bound, not read.
_PY_BINDING_PARENTS = frozenset(
    {
        "global_statement", "nonlocal_statement", "as_pattern_target", "aliased_import",
        "import_from_statement", "import_statement", "parameters", "lambda_parameters",
        "named_expression",
    }
)
_PY_BINDING_WRAPPERS = frozenset(
    {
        "pattern_list", "tuple_pattern", "list_pattern", "typed_parameter", "default_parameter",
        "typed_default_parameter", "list_splat_pattern", "dotted_name",
    }
)
