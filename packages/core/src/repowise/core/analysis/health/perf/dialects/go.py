"""Go ``PerfDialect``.

Flagship: ``database/sql`` / GORM queries inside a ``for … range`` (the Go N+1).
Two Go-specific markers ride along:

* ``defer_in_loop`` — a ``defer`` inside a loop holds the deferred handle until
  the *enclosing function* returns, not the loop iteration; the classic Go
  file-handle / row leak. Very high precision (it is a pure syntactic shape).
* ``regex_compile_in_loop`` — ``regexp.MustCompile`` / ``regexp.Compile``
  recompiled every iteration instead of hoisted.

Go has no ``async``/``await`` (concurrency is goroutines), so
``blocking_sync_in_async`` is intentionally not in :attr:`markers`.

Go's callee grammar (``call_expression`` -> ``selector_expression`` with
``operand`` / ``field``) is already handled by the generic base extraction, so
this dialect only supplies the lexicon + the Go-specific predicates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect
from .python import HTTP_VERBS

if TYPE_CHECKING:
    from tree_sitter import Node

# ``database/sql`` round-trip methods. Generic enough (``Query`` / ``Exec``) to
# collide with non-db receivers, so gated on db-import evidence.
GO_SQL_METHODS: frozenset[str] = frozenset(
    {
        "Query",
        "QueryRow",
        "QueryContext",
        "QueryRowContext",
        "Exec",
        "ExecContext",
        "Prepare",
        "PrepareContext",
    }
)
# GORM finisher methods (the ones that actually hit the database, not the
# builder chain ``.Where`` / ``.Preload`` / ``.Joins``). All collide with
# ordinary method names, so gated on a GORM/db import in the file.
# ``Scan`` is deliberately EXCLUDED: ``*sql.Rows.Scan`` (decoding an
# already-fetched cursor row inside ``for rows.Next()``) is far more common than
# GORM's ``db.Scan`` finisher and is NOT a round-trip — it FP'd ``io_in_loop``
# on every cursor-read loop (Phase-7c syft corpus). Dropping it costs ~0
# measured recall (no corpus GORM-``Scan``-in-loop) and removes the FP class.
GO_GORM_METHODS: frozenset[str] = frozenset(
    {
        "Find",
        "First",
        "Take",
        "Last",
        "Save",
        "Create",
        "Updates",
        "Delete",
        "Count",
        "Pluck",
    }
)
# ``os`` / ``io/ioutil`` filesystem round-trips, keyed on the package receiver.
GO_OS_FS_METHODS: frozenset[str] = frozenset(
    {
        "Open",
        "Create",
        "ReadFile",
        "WriteFile",
        "OpenFile",
        "ReadDir",
        "Mkdir",
        "MkdirAll",
        "Remove",
    }
)
GO_IOUTIL_METHODS: frozenset[str] = frozenset({"ReadFile", "WriteFile", "ReadAll", "ReadDir"})
GO_REGEX_COMPILE: frozenset[str] = frozenset(
    {"MustCompile", "Compile", "MustCompilePOSIX", "CompilePOSIX"}
)
_GO_STRING_KINDS: frozenset[str] = frozenset({"interpreted_string_literal", "raw_string_literal"})

# Heavy connection / client constructors, keyed ``(package, func)``. Opening one
# per ``for ... range`` iteration is the connection-churn anti-pattern.
GO_RESOURCE_CTORS: frozenset[tuple[str, str]] = frozenset(
    {
        ("sql", "Open"),
        ("sqlx", "Open"),
        ("sqlx", "Connect"),
        ("pgx", "Connect"),
        ("pgxpool", "New"),
        ("redis", "NewClient"),
        ("mongo", "Connect"),
    }
)
# ``sync.Mutex`` / ``sync.RWMutex`` acquisition (the contention side only).
GO_LOCK_METHODS: frozenset[str] = frozenset({"Lock", "RLock"})


class GoPerfDialect(BasePerfDialect):
    language = "go"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "defer_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            # Phase 7b — centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
        }
    )

    def sink_kind(
        self,
        root: str,
        method: str,
        *,
        awaited: bool,
        is_attribute: bool,
        io_names: dict[str, str],
        has_db_import: bool,
    ) -> str | None:
        root_kind = io_names.get(root)
        db_ev = has_db_import or root_kind == "db"
        net_ev = root_kind == "network" or "network" in io_names.values()
        sub_ev = root_kind == "subprocess" or "subprocess" in io_names.values()

        # net/http: ``http.Get`` (root binds to net/http) or ``client.Do``.
        if root_kind == "network" and method.lower() in HTTP_VERBS:
            return "network"
        if method == "Do" and net_ev:
            return "network"
        # os / ioutil filesystem.
        if root == "os" and method in GO_OS_FS_METHODS:
            return "filesystem"
        if root == "ioutil" and method in GO_IOUTIL_METHODS:
            return "filesystem"
        # os/exec subprocess: ``exec.Command`` is distinctive; ``cmd.Run`` etc.
        # are ambiguous and gated on an os/exec import.
        if root == "exec" and method == "Command":
            return "subprocess"
        if method in ("Run", "Output", "CombinedOutput", "Start") and sub_ev:
            return "subprocess"
        # database/sql + GORM, gated on a db import (the verbs are generic).
        if is_attribute and db_ev and (method in GO_SQL_METHODS or method in GO_GORM_METHODS):
            return "db"
        return None

    def is_constant_loop(self, node: Node) -> bool:
        """Only a three-clause ``for i := 0; i < <int>; i++`` is constant-bound.

        Go ``for … range`` and the clause-less infinite ``for { }`` are ALWAYS
        real loops (a range over a slice is the textbook N+1 site), so they are
        never skipped — the opposite of treating every ``for`` as constant.
        """
        if node.type != "for_statement":
            return False
        clause = next((c for c in node.children if c.type == "for_clause"), None)
        if clause is None:
            return False  # range / for{} ⇒ real loop
        cond = clause.child_by_field_name("condition")
        if cond is None or cond.type != "binary_expression":
            return False
        right = cond.child_by_field_name("right")
        return right is not None and right.type in ("int_literal", "float_literal")

    def is_string_concat(self, node: Node) -> bool:
        """Go ``s += "x"`` — the RHS is wrapped in an ``expression_list``."""
        if node.type != "assignment_statement":
            return False
        if not any(c.type == "+=" for c in node.children):
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        targets = right.named_children if right.type == "expression_list" else [right]
        return any(c.type in _GO_STRING_KINDS for c in targets)

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        if root == "regexp" and method in GO_REGEX_COMPILE:
            return "regex_compile_in_loop"
        if (root, method) in GO_RESOURCE_CTORS:
            return "resource_construction_in_loop"
        # ``mu.Lock()`` / ``mu.RLock()`` — a method on a receiver (the package-
        # level ``sync.Lock`` does not exist, so the attribute gate is a guard).
        if method in GO_LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        return None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        if node.type == "defer_statement":
            return "defer_in_loop"
        return None


DIALECT = GoPerfDialect()
