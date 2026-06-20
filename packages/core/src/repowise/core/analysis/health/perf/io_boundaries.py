"""Resolve a loop-nested call site to a typed I/O boundary.

The performance pass needs to answer one question per call: *is this an
execution of an I/O boundary (db / network / filesystem / subprocess), or just
ordinary computation?* That decomposes into two pieces, both here:

1. **Dependency classification** — which imported names in a file originate from
   an I/O library, and of what kind. This reuses the shared
   :func:`...ingestion.external_systems.io_kind.classify_io_kind` table
   (Primitive 1) rather than a perf-private list, so the same maintained
   classification powers C4 typing, the perf pass, and a future security layer.
   :func:`collect_io_names` walks a file's import nodes and binds each imported
   identifier to its ``io_kind``.

2. **Execution-sink gating** — a *query builder* (`select().where()`) is not a
   round-trip; only *executing* it is. :func:`classify_call_sink` returns a
   boundary kind ONLY for the execution sinks (``.execute`` / ``.scalars`` /
   ``.commit`` / ``subprocess.run`` / awaited HTTP / ``fetch`` / prisma
   verbs...), never for builder chaining. This is the refinement that took the
   Phase-0 probe from a noisy lexicon to a high-precision signal.

The riskiest stratum is the ambiguous DBAPI result accessors (``.all`` /
``.first`` / ``.one``) and ``.commit`` — they collide with ordinary collection
methods (``list.commit`` does not exist, but ``results.all`` and a GitPython
``repo.commit()`` do). Those are gated on **DB-typed-receiver evidence**: the
file must import a db library, or the call's receiver root must bind to one.
This drops the GitPython ``repo.commit()`` false positives the probe flagged.

This module is pure: it takes already-extracted ``(root, method, awaited,
is_attribute)`` tuples plus the resolved ``io_names`` map. The AST extraction
lives in the walker. Unknown inputs return ``None`` ("not an I/O sink") so the
detector degrades to "no signal", never a false positive.
"""

from __future__ import annotations

import re
from typing import Protocol

from ....ingestion.external_systems.io_kind import classify_io_kind

# Languages whose call grammar + import shape this module understands. The perf
# pass is opt-in elsewhere (an empty boundary map ⇒ no hits).
TS_LIKE: frozenset[str] = frozenset({"typescript", "javascript"})

# ---------------------------------------------------------------------------
# Execution-sink lexicons (the gate). Method-name based.
# ---------------------------------------------------------------------------
HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "send", "stream"}
)

# Python DBAPI / SQLAlchemy execution sinks. Split into three strata so the
# ambiguous accessors can be gated harder than the unambiguous ones:
#   * UNAMBIGUOUS — names that essentially only exist on a cursor / session /
#     result object. Trusted on any attribute call.
#   * COMMIT — ``.commit`` is unambiguous as a DB verb but collides with
#     GitPython's ``repo.commit()``; gated on db evidence.
#   * AMBIGUOUS — ``.all`` / ``.first`` / ``.one`` collide with ordinary
#     collection/query helpers; the riskiest stratum, gated on db evidence.
PY_DB_UNAMBIGUOUS: frozenset[str] = frozenset(
    {
        "execute",
        "executemany",
        "scalars",
        "scalar",
        "scalar_one",
        "scalar_one_or_none",
        "fetchone",
        "fetchall",
        "fetchmany",
    }
)
PY_DB_COMMIT: frozenset[str] = frozenset({"commit"})
PY_DB_AMBIGUOUS: frozenset[str] = frozenset({"all", "first", "one", "one_or_none"})
PY_SUBPROC_METHODS: frozenset[str] = frozenset(
    {"run", "call", "check_call", "check_output", "Popen"}
)

# TypeScript / JavaScript. Only DISTINCTIVE method names are trusted without
# import resolution: bare create/update/delete/count/exec collide hard with
# Map/Set/Array/RegExp and are pure noise. Generic fs/subprocess verbs are only
# trusted when the root binds to an imported node:fs / child_process.
PRISMA_METHODS: frozenset[str] = frozenset(
    {
        "findMany",
        "findUnique",
        "findFirst",
        "findUniqueOrThrow",
        "findFirstOrThrow",
        "createMany",
        "updateMany",
        "deleteMany",
        "upsert",
        "aggregate",
        "groupBy",
    }
)
TS_FS_METHODS: frozenset[str] = frozenset(
    {"readFileSync", "writeFileSync", "appendFileSync", "readdirSync"}
)
# Synchronous execution-sink verbs across the TS I/O ecosystems. Used to gate a
# call on an imported I/O package: a *round-trip* method (or an awaited call),
# never a sync helper like ``axios.isCancel`` / ``axios.create`` / a query
# *builder* like ``.where()``. Async sinks (``axios.get``, prisma queries,
# ``fs/promises`` reads) are caught by the ``awaited`` arm instead.
TS_SINK_METHODS: frozenset[str] = (
    HTTP_VERBS
    | PRISMA_METHODS
    | TS_FS_METHODS
    | frozenset(
        {
            "query",
            "execute",
            "exec",
            "execSync",
            "spawn",
            "spawnSync",
            "execFile",
            "execFileSync",
            "raw",
            "$queryRaw",
            "$executeRaw",
        }
    )
)

# Tokens that are syntax, not bindable import names.
_IMPORT_KW: frozenset[str] = frozenset(
    {"import", "from", "as", "require", "const", "let", "var", "default", "type", "typeof"}
)


class _NodeLike(Protocol):
    """Duck type of a tree-sitter node (avoids importing tree_sitter here)."""

    type: str
    text: bytes | None

    @property
    def children(self) -> list[_NodeLike]: ...


def _decode(node: _NodeLike) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _classify_import(node: _NodeLike) -> tuple[str | None, list[str]]:
    """``(io_kind, bound_names)`` for an import node, else ``(None, [])``.

    The module is classified through the shared :func:`classify_io_kind` table:
    every candidate token (a quoted TS source like ``"node:fs"`` /
    ``"@prisma/client"``, or a Python dotted module's full path + top-level
    package) is tried until one resolves. When it does, every importable
    identifier in the statement is bound to that kind. Over-binding is
    deliberate and harmless: an imported name only becomes a finding when it is
    later *called as an execution sink*, which a non-I/O symbol never is.
    """
    text = _decode(node)
    candidates: set[str] = set()
    # TS / JS module sources are quoted string literals.
    for m in re.findall(r"""["']([^"']+)["']""", text):
        candidates.add(m)
        candidates.add(m.split("/")[0])
    # Python dotted modules / bare identifiers.
    for tok in re.findall(r"[A-Za-z0-9_.:@/]+", text):
        candidates.add(tok)
        candidates.add(tok.split(".")[0])

    kind: str | None = None
    for cand in candidates:
        resolved = classify_io_kind(cand)
        if resolved:
            kind = resolved
            break
    if kind is None:
        return None, []

    bound = [
        t for t in re.split(r"[^A-Za-z0-9_]+", text) if t.isidentifier() and t not in _IMPORT_KW
    ]
    return kind, bound


def collect_io_names(tree_root: _NodeLike, language: str) -> dict[str, str]:
    """Map every imported identifier that resolves to an I/O library → io_kind.

    A whole-tree scan of import nodes (import statements live at module scope
    but a defensive full walk also catches function-local imports). Names that
    do not originate from a classified I/O library are simply absent.
    """
    names: dict[str, str] = {}
    stack: list[_NodeLike] = [tree_root]
    while stack:
        node = stack.pop()
        if "import" in node.type:
            kind, bound = _classify_import(node)
            if kind is not None:
                for name in bound:
                    names.setdefault(name, kind)
        for child in node.children:
            stack.append(child)
    return names


def _py_sink_kind(
    root: str,
    method: str,
    awaited: bool,
    is_attribute: bool,
    io_names: dict[str, str],
    has_db_import: bool,
) -> str | None:
    root_kind = io_names.get(root)
    db_evidence = has_db_import or root_kind == "db"

    if method in PY_DB_UNAMBIGUOUS:
        # A real DB sink is always a method on a session/cursor/result object;
        # a bare identifier call (the builtin ``all(...)``) is not.
        return "db" if is_attribute else None
    if method in PY_DB_COMMIT:
        # ``.commit`` is a DB verb but GitPython exposes ``repo.commit()``.
        # Require db evidence in the file / on the receiver.
        return "db" if (is_attribute and db_evidence) else None
    if method in PY_DB_AMBIGUOUS:
        # ``.all`` / ``.first`` / ``.one`` collide with ordinary collection
        # helpers — the riskiest stratum. Gate on db evidence.
        return "db" if (is_attribute and db_evidence) else None
    if root == "subprocess" and method in PY_SUBPROC_METHODS:
        return "subprocess"
    if method == "Popen":
        return "subprocess"
    if root == "open" and method == "open":  # bare ``open(...)`` builtin
        return "filesystem"
    if method == "urlopen":
        return "network"
    if root_kind == "network" and method in HTTP_VERBS:
        return "network"
    if awaited and root_kind == "network":
        return "network"
    return None


def _ts_sink_kind(
    root: str,
    method: str,
    awaited: bool,
    io_names: dict[str, str],
) -> str | None:
    if method == "fetch":  # the ``fetch(...)`` global — no import to resolve
        return "network"
    root_kind = io_names.get(root)
    if root_kind is not None:
        # A call on an imported I/O package (``axios.get`` / ``db.query``) is a
        # sink only when it is a known round-trip verb or is awaited — NOT a
        # sync helper (``axios.isCancel`` / ``axios.create``) or a query builder
        # (``.where()`` / ``.select()``), which over-fired before this gate.
        if method in TS_SINK_METHODS or awaited:
            return root_kind
        return None
    if method in PRISMA_METHODS:  # distinctive prisma-client verbs
        return "db"
    if method in TS_FS_METHODS:  # distinctive sync-fs verbs
        return "filesystem"
    return None


def classify_call_sink(
    language: str,
    root: str,
    method: str,
    *,
    awaited: bool,
    is_attribute: bool,
    io_names: dict[str, str],
    has_db_import: bool,
) -> str | None:
    """Boundary kind for a call site if it is an execution sink, else ``None``.

    ``root`` is the callee's root identifier (``a.b.c()`` → ``"a"``); ``method``
    is the rightmost member (``x.execute()`` → ``"execute"``); ``awaited`` is
    whether the call is the operand of an ``await``; ``is_attribute`` is whether
    the callee is a member access (vs a bare-identifier call). ``io_names`` maps
    imported names to their ``io_kind`` (from :func:`collect_io_names`);
    ``has_db_import`` is whether the file imports any db library.
    """
    if language == "python":
        return _py_sink_kind(root, method, awaited, is_attribute, io_names, has_db_import)
    if language in TS_LIKE:
        return _ts_sink_kind(root, method, awaited, io_names)
    return None
