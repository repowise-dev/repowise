"""Python ``PerfDialect``.

Extracted verbatim from the original ``_py_sink_kind`` (``io_boundaries.py``)
and the Python branches of the walker (``_blocking_sync_api`` / ``_is_constant_for``
/ the ``_PY_STRING_KINDS`` string-concat predicate). The 6a refactor changes
zero Python behavior — the defect golden + perf suite lock that.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# Network round-trip verbs (shared across languages that name HTTP methods).
HTTP_VERBS: frozenset[str] = frozenset(
    {"get", "post", "put", "delete", "patch", "head", "options", "request", "send", "stream"}
)

# Python DBAPI / SQLAlchemy execution sinks, split into three strata so the
# ambiguous accessors can be gated harder than the unambiguous ones (see
# ``sink_kind``).
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

# Python string-literal node kinds (f-strings parse as ``string`` too).
_PY_STRING_KINDS: frozenset[str] = frozenset({"string", "concatenated_string"})
_PY_AUG_ASSIGN_KINDS: frozenset[str] = frozenset({"augmented_assignment"})


class PythonPerfDialect(BasePerfDialect):
    language = "python"
    markers = frozenset({"io_in_loop", "string_concat_in_loop", "blocking_sync_in_async"})

    string_literal_kinds = _PY_STRING_KINDS
    aug_assign_kinds = _PY_AUG_ASSIGN_KINDS

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
        db_evidence = has_db_import or root_kind == "db"

        if method in PY_DB_UNAMBIGUOUS:
            # A real DB sink is always a method on a session/cursor/result
            # object; a bare identifier call (the builtin ``all(...)``) is not.
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

    def is_constant_loop(self, node: Node) -> bool:
        """True if a Python for-loop iterates a compile-time-constant bound.

        Catches ``for _ in range(<int literals>)``, ``for x in (<literal>)``,
        and ``for x in ALL_CAPS`` (a named module constant by convention).
        ``while`` loops are never constant.
        """
        if node.type != "for_statement":
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type in ("list", "tuple", "set"):
            return True
        # A bare ALL_CAPS identifier is a named constant by convention.
        if right.type == "identifier" and right.text is not None:
            name = right.text.decode("utf-8", "replace")
            if name.isupper() and len(name) > 1:
                return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is None or (fn.text or b"").decode("utf-8", "replace") != "range":
                return False
            args = right.child_by_field_name("arguments")
            if args is None:
                return False
            for a in args.children:
                if not a.is_named:
                    continue
                if a.type == "integer":
                    continue
                if a.type == "unary_operator" and any(c.type == "integer" for c in a.children):
                    continue
                return False  # a non-literal arg (e.g. len(x)) ⇒ data-dependent
            return True
        return False

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        """The offending API name if ``root.method`` is a known blocking sync call.

        A small, high-precision allowlist (mirrors ruff ASYNC210/230/251):
        always-synchronous stdlib / ``requests`` calls that block the event
        loop when run inside an ``async def``.
        """
        if root == "time" and method == "sleep":
            return "time.sleep"
        if root == "requests" and method in HTTP_VERBS:
            return f"requests.{method}"
        if root == "subprocess" and method in PY_SUBPROC_METHODS:
            return f"subprocess.{method}"
        if root == "os" and method == "system":
            return "os.system"
        if root == "open" and method == "open":
            return "open"
        return None


DIALECT = PythonPerfDialect()
