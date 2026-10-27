"""Python ``PerfDialect``.

Vocabularies live in :mod:`.python_lexicon`, syntax helpers in
:mod:`.python_nodes`, loop sources in :mod:`.python_sources` and batched forms
in :mod:`.python_batch`. Their names are re-exported here.
"""

# ruff: noqa: F401

from __future__ import annotations

import builtins
import re
from typing import TYPE_CHECKING, ClassVar

from .. import loop_lexicon as lex
from ..loop_facts import BatchForm, LoopSource
from .base import BasePerfDialect
from .iterables import IterableSources
from .python_batch import PythonBatchForms
from .python_lexicon import (
    _PY_AUG_ASSIGN_KINDS,
    _PY_BINDING_PARENTS,
    _PY_BINDING_WRAPPERS,
    _PY_BUILTINS,
    _PY_CONSTANT_RHS,
    _PY_DEF_KINDS,
    _PY_KEY_READS,
    _PY_LIST_BUILTINS,
    _PY_LIST_RHS_KINDS,
    _PY_LISTINGS,
    _PY_LOCK_METHODS,
    _PY_MODEL_NAME_RE,
    _PY_NONLIST_BUILTINS,
    _PY_NONLIST_RHS_KINDS,
    _PY_ONE_ROW,
    _PY_PASS_THROUGH,
    _PY_PER_KEY_LIMITS,
    _PY_QUERY_CHAIN_METHODS,
    _PY_READ_CAPS,
    _PY_RECURSIVE_LISTINGS,
    _PY_RESOURCE_CTORS,
    _PY_RESOURCE_METHODS,
    _PY_RESULT_PROJECTIONS,
    _PY_SCOPES,
    _PY_SEM_NAME_RE,
    _PY_SQL_COMMENT_RE,
    _PY_SQL_LIMIT_RE,
    _PY_SQL_LITERAL_RE,
    _PY_STATEMENT_BUILDERS,
    _PY_STREAMS,
    _PY_STRING_KINDS,
    _PY_WRITES,
    HTTP_VERBS,
    PY_DB_AMBIGUOUS,
    PY_DB_NEEDS_EVIDENCE,
    PY_DB_UNAMBIGUOUS,
    PY_FS_METHODS,
    PY_OS_FS_METHODS,
    PY_SHUTIL_FS_METHODS,
    PY_SUBPROC_METHODS,
    PY_UNBOUNDED_READ_BOUND_METHODS,
)
from .python_nodes import (
    _PY_SCALARS,
    _bindings,
    _binds,
    _field_text,
    _first_arg,
    _fixed_set,
    _in_filter,
    _literal_range,
    _module_int_constant,
    _runs_schema_statement,
    _scalar_literal,
    _splits_constant,
    _string_value,
    _text,
    _value_refs,
)
from .python_paths import PythonPathReceivers
from .python_sources import PythonLoopSources

if TYPE_CHECKING:
    from tree_sitter import Node


_PY_DB_METHODS: frozenset[str] = PY_DB_UNAMBIGUOUS | PY_DB_NEEDS_EVIDENCE | PY_DB_AMBIGUOUS
_PY_ROOTED_PROCESS_VERBS: dict[str, frozenset[str]] = {"subprocess": PY_SUBPROC_METHODS}
_PY_ROOTED_FILE_VERBS: dict[str, frozenset[str]] = {
    "open": frozenset({"open"}),
    "os": PY_OS_FS_METHODS,
    "shutil": PY_SHUTIL_FS_METHODS,
}


def _db_sink(method: str, is_attribute: bool, evidence: bool) -> str | None:
    """A DB sink is a member call on a session or cursor (never the builtin
    ``all(...)``); verbs other clients also spell need db evidence."""
    if not is_attribute:
        return None
    return "db" if method in PY_DB_UNAMBIGUOUS or evidence else None


class PythonPerfDialect(
    PythonBatchForms, PythonLoopSources, PythonPathReceivers, IterableSources
):
    language = "python"
    lock_acquire_functions = frozenset({"acquire", "__enter__"})
    spin_loop_header = re.compile(r"while\s+True\s*:")
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "blocking_sync_in_async",
            "resource_construction_in_loop",
            "lock_in_loop",
            "serial_await_in_loop",
            "membership_test_against_list_in_loop",
            # Centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # Python-specific quadratic anti-patterns.
            "list_insert_zero_in_loop",
            "pd_concat_in_loop",
            # Header-call marker: the slow iterable IS the loop driver.
            "pandas_iterrows_in_loop",
        }
    )

    string_literal_kinds = _PY_STRING_KINDS
    aug_assign_kinds = _PY_AUG_ASSIGN_KINDS
    # One-inode operations are ordinary work outside a loop; only unbounded ones
    # (tree walks, recursive copy/delete, archives) and bare ``open`` stay hot-path I/O.
    hot_path_excluded_methods = (
        PY_FS_METHODS
        | (PY_OS_FS_METHODS - {"walk"})
        | (PY_SHUTIL_FS_METHODS - {"copytree", "rmtree", "make_archive", "unpack_archive"})
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
        # ``sleep`` is a cooperative yield, never a round-trip, even when its
        # module is import-classified as network.
        if method == "sleep":
            return None
        root_kind = io_names.get(root)
        if method in _PY_DB_METHODS:
            return _db_sink(method, is_attribute, has_db_import or root_kind == "db")
        if method == "Popen" or method in _PY_ROOTED_PROCESS_VERBS.get(root, ()):
            return "subprocess"
        # A verb under its literal module root (bare ``open`` too). Pathlib member
        # calls need their receiver, so ``call_sink_kind`` decides those.
        if method in _PY_ROOTED_FILE_VERBS.get(root, ()):
            return "filesystem"
        # An HTTP verb only as a member call on an imported client: a bare ``get(...)`` is not one.
        if method == "urlopen" or (
            root_kind == "network" and (awaited or (is_attribute and method in HTTP_VERBS))
        ):
            return "network"
        return None

    def call_sink_kind(
        self, call: Node, *, awaited: bool, io_names: dict[str, str], has_db_import: bool
    ) -> str | None:
        kind = super().call_sink_kind(
            call,
            awaited=awaited,
            io_names=io_names,
            has_db_import=has_db_import or self.shows_a_query(call),
        )
        if kind == "db" and (
            self._reads_a_registry(call, io_names) or _runs_schema_statement(call, self.reaching_value)
        ):
            return None
        if kind is None and self.callee_method_name(call) in PY_FS_METHODS and self.on_path(call):
            return "filesystem"
        orm_get = self._orm_get(call) if kind is None else None
        if orm_get is not None:
            # ``get`` alone is ``dict.get`` / ``requests.get``; the model-class shape
            # plus db evidence in the file is what makes it ``Session.get``.
            db = has_db_import or io_names.get(self.callee_root_name(call) or "") == "db"
            return "db" if db and not self._key_used_earlier(call, orm_get[1]) else None
        return kind

    def shows_a_query(self, call: Node) -> bool:
        """An evidence-gated DB verb whose call runs a query by its own shape,
        whatever the file imports."""
        if self.callee_method_name(call) not in PY_DB_NEEDS_EVIDENCE:
            return False
        fn = call.child_by_field_name("function")
        if fn is None or fn.type != "attribute":
            return False
        receiver = fn.child_by_field_name("object")
        return self._chains_a_query(receiver) or self._passes_a_statement(call)

    def _chains_a_query(self, receiver: Node | None) -> bool:
        """A PostgREST-style ``table(...)`` / ``from_(...)`` call on the receiver chain."""
        while receiver is not None and receiver.type in ("call", "attribute"):
            if receiver.type == "call":
                if self.callee_method_name(receiver) in _PY_QUERY_CHAIN_METHODS:
                    return True
                receiver = receiver.child_by_field_name("function")
            else:
                receiver = receiver.child_by_field_name("object")
        return False

    def _passes_a_statement(self, call: Node) -> bool:
        """The first argument is a statement builder call or a SQL string literal."""
        first = _first_arg(call)
        if first is None:
            return False
        if first.type == "call":
            return self.callee_method_name(first) in _PY_STATEMENT_BUILDERS
        if first.type in ("string", "concatenated_string"):
            return bool(_PY_SQL_LITERAL_RE.match((first.text or b"").decode("utf-8", "replace")))
        return False

    def _reads_a_registry(self, call: Node, io_names: dict[str, str]) -> bool:
        """``plugins.all()`` on an imported or module-global name reads a registry, not a
        query result: a result is bound in the function that reads it."""
        if self.callee_method_name(call) not in PY_DB_AMBIGUOUS:
            return False
        fn = call.child_by_field_name("function")
        receiver = fn.child_by_field_name("object") if fn is not None else None
        if receiver is None or receiver.type != "identifier":
            return False
        name = receiver.text or b""
        return io_names.get(name.decode()) != "db" and not self._bound_locally(call, name)

    def _bound_locally(self, call: Node, name: bytes) -> bool:
        """*name* is assigned before *call* in its function, or is a parameter of it."""
        if self.bindings_before(call, name):
            return True
        scope = call.parent
        while scope is not None and scope.type != "function_definition":
            scope = scope.parent
        params = scope.child_by_field_name("parameters") if scope is not None else None
        return params is not None and name.decode() in self._param_names(params)

    def _key_used_earlier(self, call: Node, key: Node) -> bool:
        """An earlier call in this loop body gave the same session the same key, so the
        row may already sit in the session and ``get`` answers from memory."""
        loop = call.parent
        while loop is not None and loop.type not in ("for_statement", "while_statement"):
            if loop.type in _PY_SCOPES:
                return False
            loop = loop.parent
        body = loop.child_by_field_name("body") if loop is not None else None
        if body is None:
            return False
        session = (self.callee_root_name(call) or "").encode()
        return any(
            self._passes_key(node, call, key, session)
            for node in self._walk(body, prune=_PY_SCOPES)
        )

    def _passes_key(self, node: Node, call: Node, key: Node, session: bytes) -> bool:
        """*node* is a call before *call* passing *key* on the same *session*."""
        args = node.child_by_field_name("arguments") if node.type == "call" else None
        if args is None or node.end_byte > call.start_byte:
            return False
        texts = {arg.text for arg in args.children}
        same_session = session in texts or self.callee_root_name(node) == session.decode()
        return key.text in texts and same_session

    def _orm_get(self, call: Node) -> tuple[str, Node] | None:
        """``(Model, key)`` of a ``x.get(Model, key)`` primary-key lookup."""
        if self.callee_method_name(call) != "get" or not self.callee_is_attribute(call):
            return None
        args = call.child_by_field_name("arguments")
        named = [c for c in args.children if c.is_named] if args is not None else []
        if any(c.type in ("list_splat", "dictionary_splat") for c in named):
            return None
        positional = [c for c in named if c.type != "keyword_argument"]
        model = self._dotted_path(positional[0]) if len(positional) == 2 else None
        if model is None or not _PY_MODEL_NAME_RE.match(model.rsplit(".", 1)[-1]):
            return None
        return model, positional[1]

    def is_constant_loop(self, node: Node) -> bool:
        """``for _ in range(<int literals>)``, ``for x in (<literal>)``,
        ``for x in ALL_CAPS`` (a module constant by convention) or a split of a
        string the source spells out (``for stmt in SCHEMA.split(";")``)."""
        if node.type != "for_statement":
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type in ("list", "tuple", "set"):
            return True
        if right.type == "identifier" and right.text is not None:
            name = right.text.decode("utf-8", "replace")
            if name.isupper() and len(name) > 1:
                return True
        return right.type == "call" and (_literal_range(right) or _splits_constant(right))

    # Builtins that wrap a collection without changing what is iterated, so the
    # same-collection O(n^2) shape sees through them: ``for x in enumerate(items)``
    # iterates ``items``.
    _ITER_WRAPPERS: frozenset[str] = frozenset({"enumerate", "sorted", "reversed", "list", "set"})

    def defers_body(self, fn: Node) -> bool:
        """A function containing its own ``yield`` is a generator: calling it
        runs none of the body, so a read there happens per item, not per call."""
        if fn.type != "function_definition":
            return False
        stack = list(fn.children)
        while stack:
            node = stack.pop()
            if node.type in ("function_definition", "lambda", "class_definition"):
                continue
            if node.type == "yield":
                return True
            stack.extend(node.children)
        return False

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type != "for_statement":
            return None
        right = node.child_by_field_name("right")
        if right is None:
            return None
        if right.type in ("identifier", "attribute"):
            return self._dotted_path(right)
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is None or fn.text is None:
                return None
            if fn.text.decode("utf-8", "replace") not in self._ITER_WRAPPERS:
                return None
            args = right.child_by_field_name("arguments")
            if args is None:
                return None
            first = next((c for c in args.children if c.is_named), None)
            if first is not None and first.type == "identifier" and first.text is not None:
                return first.text.decode("utf-8", "replace")
        return None

    def iterable_node(self, node: Node) -> Node | None:
        return node.child_by_field_name("right") if node.type == "for_statement" else None

    # ``itertools.batched``, ``more_itertools.chunked`` and hand-rolled peers.
    _CHUNKING_CALLS: frozenset[str] = frozenset(
        {"batched", "chunked", "ichunked", "chunks", "iter_chunks", "grouper"}
    )

    def is_chunked_loop(self, node: Node) -> bool:
        """``range(start, stop, step)`` with a non-unit step, or a chunking helper."""
        if node.type != "for_statement":
            return False
        right = node.child_by_field_name("right")
        if right is None or right.type != "call":
            return False
        fn = right.child_by_field_name("function")
        if fn is None or fn.text is None:
            return False
        name = fn.text.decode("utf-8", "replace").rsplit(".", 1)[-1]
        if name in self._CHUNKING_CALLS:
            return True
        if name != "range":
            return False
        args = right.child_by_field_name("arguments")
        named = [c for c in args.children if c.is_named] if args is not None else []
        return len(named) == 3 and named[2].text not in (b"1", b"-1")

    def is_string_concat(self, node: Node) -> bool:
        """``s += "x"`` accumulation, unless an enclosing loop body re-assigns the
        name: then the string is rebuilt per iteration, not accumulated across them."""
        if not super().is_string_concat(node):
            return False
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier" or left.text is None:
            return True  # opaque target -> keep the (precision-first) flag
        name = left.text.decode("utf-8", "replace")
        cur = node.parent
        while cur is not None:
            if cur.type in ("for_statement", "while_statement"):
                body = cur.child_by_field_name("body")
                if body is not None and self._resets_name(body, name, node):
                    return False
            cur = cur.parent
        return True

    @staticmethod
    def _resets_name(body: Node, name: str, exclude: Node) -> bool:
        """True if *body* contains a plain ``name = ...`` assignment (not the
        ``+=`` node *exclude* itself) — i.e. the accumulator is reset here."""
        stack: list[Node] = [body]
        while stack:
            n = stack.pop()
            if n.type == "assignment" and not (
                n.start_byte == exclude.start_byte and n.end_byte == exclude.end_byte
            ):
                lhs = n.child_by_field_name("left")
                if (
                    lhs is not None
                    and lhs.type == "identifier"
                    and lhs.text is not None
                    and lhs.text.decode("utf-8", "replace") == name
                ):
                    return True
            stack.extend(n.children)
        return False

    def unbounded_read_bound_methods(self) -> frozenset[str]:
        return PY_UNBOUNDED_READ_BOUND_METHODS

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

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        if (root, method) in _PY_RESOURCE_CTORS or method in _PY_RESOURCE_METHODS:
            return "resource_construction_in_loop"
        # ``lock.acquire()``: the attribute gate guards against a bare-name collision.
        if method in _PY_LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        # ``lst.insert(0, x)`` shifts the whole list each pass: a literal ``0``, on a
        # list not re-created per iteration, not a ``deque`` (O(1) at the front) and
        # not followed by an exit that ends the loop.
        if method == "insert" and self._front_insert_repeats(node, root):
            return "list_insert_zero_in_loop"
        # ``pd.concat([acc, chunk])`` / ``pandas.concat(...)`` in a loop copies
        # the whole frame each pass -> O(n^2); collect a list and concat once.
        if root in ("pd", "pandas") and method == "concat":
            return "pd_concat_in_loop"
        return None

    def loop_iterable_call_marker(self, node: Node) -> str | None:
        """``for _, row in df.iterrows():``, row-by-row DataFrame iteration in the loop header.

        A member call in a file that imports pandas. Ceiling: still a name match,
        so a user class with an ``iterrows`` method in such a file is flagged too.
        """
        if node.type != "for_statement":
            return None
        right = node.child_by_field_name("right")
        if right is None or right.type != "call":
            return None
        if (
            self.callee_method_name(right) == "iterrows"
            and self.callee_is_attribute(right)
            and self._file_imports_pandas(node)
        ):
            return "pandas_iterrows_in_loop"
        return None

    @staticmethod
    def _file_imports_pandas(node: Node) -> bool:
        """True if the enclosing file has an ``import pandas`` / ``from pandas
        import ...`` statement (a soft gate; still a name match, see caller)."""
        root = node
        while root.parent is not None:
            root = root.parent
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if "import" in n.type:
                txt = (n.text or b"").decode("utf-8", "replace")
                if "pandas" in txt:
                    return True
                continue  # imports do not nest further imports
            stack.extend(n.children)
        return False

    def _front_insert_repeats(self, node: Node, root: str) -> bool:
        if not (self.callee_is_attribute(node) and self._first_arg_is_zero(node)):
            return False
        return not (
            self._receiver_reset_in_loop(node, root)
            or self._receiver_bound_to_deque(node, root)
            or self._loop_ends_after(node)
        )

    def _loop_ends_after(self, node: Node) -> bool:
        """A ``return``/``raise`` (or, with one loop around *node*, a ``break``)
        follows *node*'s statement in its block, so it runs once per call.
        Ceiling: a flag-driven exit (``stop = True`` then ``if stop: break``) is
        still reported."""
        stmt = node
        while stmt.parent is not None and stmt.parent.type != "block":
            if stmt.type in self.scope_kinds:  # a lambda body runs elsewhere
                return False
            stmt = stmt.parent
        loops, in_try = self._enclosing_loops(stmt)
        if not loops:
            return False
        # A ``raise`` inside a ``try`` may be caught and the loop go on, and a
        # ``break`` in a loop nest leaves only the inner loop.
        exits = {"return_statement"} if in_try else {"return_statement", "raise_statement"}
        if loops == 1:
            exits.add("break_statement")
        return self._exit_follows(
            stmt, lambda s: s.type in exits, frozenset({"continue_statement"})
        )

    def _enclosing_loops(self, stmt: Node) -> tuple[int, bool]:
        """Loops around *stmt* in its scope, and whether a ``try`` sits among them."""
        loops, in_try, cur = 0, False, stmt.parent
        while cur is not None and cur.type not in self.scope_kinds:
            loops += cur.type in ("for_statement", "while_statement")
            in_try = in_try or cur.type == "try_statement"
            cur = cur.parent
        return loops, in_try

    def _receiver_reset_in_loop(self, node: Node, name: str) -> bool:
        """True if the call's receiver ``name`` is re-assigned (reset to a fresh
        list) inside an enclosing loop body — so the per-front-insert is bounded
        per iteration, not an O(n^2) accumulation."""
        if not name:
            return False
        cur = node.parent
        while cur is not None:
            if cur.type in ("for_statement", "while_statement"):
                body = cur.child_by_field_name("body")
                if body is not None and self._resets_name(body, name, node):
                    return True
            cur = cur.parent
        return False

    def _receiver_bound_to_deque(self, node: Node, name: str) -> bool:
        """True if the call's receiver ``name`` is bound to a ``deque()`` /
        ``collections.deque(...)`` anywhere in the file. Mirrors the whole-tree
        binding scan of :meth:`list_bound_names`: a scoped rewrite is out of
        scope, so a same-named list in another scope would still be missed here —
        acceptable, since dropping the finding only trades recall for precision.
        """
        if not name:
            return False
        root = node
        while root.parent is not None:
            root = root.parent
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                left = n.child_by_field_name("left")
                right = n.child_by_field_name("right")
                if (
                    left is not None
                    and left.type == "identifier"
                    and left.text is not None
                    and left.text.decode("utf-8", "replace") == name
                    and right is not None
                    and self._rhs_is_deque(right)
                ):
                    return True
            stack.extend(n.children)
        return False

    @staticmethod
    def _rhs_is_deque(right: Node) -> bool:
        if right.type != "call":
            return False
        fn = right.child_by_field_name("function")
        if fn is None or fn.text is None:
            return False
        callee = fn.text.decode("utf-8", "replace")
        return callee == "deque" or callee.endswith(".deque")

    @staticmethod
    def _first_arg_is_zero(node: Node) -> bool:
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        first = next((c for c in args.children if c.is_named), None)
        return (
            first is not None
            and first.type == "integer"
            and (first.text or b"").decode("utf-8", "replace") == "0"
        )

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``x in big_list`` / ``x not in big_list`` where ``big_list`` is a
        # known list -> O(n) per probe. A set/dict membership test is O(1) and
        # must not fire, hence the ``list_names`` gate.
        if not list_names or node.type != "comparison_operator":
            return None
        # ``x in y`` is an ``in`` operator token; ``x not in y`` is a single
        # ``not in`` token. Either way it is an O(n) membership probe on a list.
        if not any(c.type in ("in", "not in") for c in node.children):
            return None
        named = [c for c in node.children if c.is_named]
        if len(named) < 2:
            return None
        right = named[-1]
        if right.type != "identifier" or right.text is None:
            return None
        name = right.text.decode("utf-8", "replace")
        return "membership_test_against_list_in_loop" if name in list_names else None

    def list_bound_names(self, root: Node) -> frozenset[str]:
        """Names assigned a provable list anywhere in the file (``[...]``, a list
        comprehension, ``list(...)``, ``sorted(...)``), minus any name also bound to
        a set or dict, or used as a parameter, in any scope."""
        list_names: set[str] = set()
        exclude: set[str] = set()
        stack: list[Node] = [root]
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                left = n.child_by_field_name("left")
                right = n.child_by_field_name("right")
                if (
                    left is not None
                    and left.type == "identifier"
                    and left.text is not None
                    and right is not None
                ):
                    name = left.text.decode("utf-8", "replace")
                    if self._rhs_is_list(right):
                        list_names.add(name)
                    elif self._rhs_is_nonlist_container(right):
                        exclude.add(name)
            elif n.type == "parameters":
                exclude.update(self._param_names(n))
            for c in n.children:
                stack.append(c)
        return frozenset(list_names - exclude)

    @staticmethod
    def _param_names(params: Node) -> set[str]:
        """The bound names declared in a ``parameters`` node (plain / typed /
        default / splat forms), excluding any type-annotation identifiers."""
        names: set[str] = set()
        for p in params.children:
            if not p.is_named:
                continue
            if p.type == "identifier":
                if p.text is not None:
                    names.add(p.text.decode("utf-8", "replace"))
                continue
            nm = p.child_by_field_name("name")
            if nm is None:
                # typed_parameter has no ``name`` field: the param name is its
                # first identifier child (the type comes after the ``:``).
                nm = next((c for c in p.children if c.type == "identifier"), None)
            if nm is not None and nm.text is not None:
                names.add(nm.text.decode("utf-8", "replace"))
        return names

    @staticmethod
    def _rhs_is_list(right: Node) -> bool:
        if right.type in _PY_LIST_RHS_KINDS:
            return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and fn.text is not None:
                return fn.text.decode("utf-8", "replace") in _PY_LIST_BUILTINS
        return False

    @staticmethod
    def _rhs_is_nonlist_container(right: Node) -> bool:
        if right.type in _PY_NONLIST_RHS_KINDS:
            return True
        if right.type == "call":
            fn = right.child_by_field_name("function")
            if fn is not None and fn.type == "identifier" and fn.text is not None:
                return fn.text.decode("utf-8", "replace") in _PY_NONLIST_BUILTINS
        return False

    # -- promotion facts (perf/loop_facts.py) ---------------------------------

    await_kind = "await"
    key_hops: ClassVar[dict[str, str]] = {"attribute": "object", "subscript": "value"}
    binding_fields: ClassVar[dict[str, str]] = {
        "assignment": "left",
        "augmented_assignment": "left",
        "for_statement": "left",
        "named_expression": "name",
    }
    branch_kinds = frozenset(
        {
            "if_statement", "try_statement", "match_statement", "while_statement",
            "conditional_expression", "boolean_operator",
        }
    )
    exit_kinds = frozenset(
        {"break_statement", "continue_statement", "return_statement", "raise_statement"}
    )
    scope_kinds = _PY_SCOPES
    sequence_appends = frozenset({"append", "extend", "insert"})

    # -- what a loop walks: the shared reading (perf/dialects/iterables.py) -------
    function_kinds = _PY_SCOPES
    member_kinds = frozenset({"attribute"})
    recursive_listings = _PY_RECURSIVE_LISTINGS
    recursive_marks = (b"**", b"recursive=True")
    conditional_kinds = frozenset(
        {
            "if_statement", "elif_clause", "else_clause", "for_statement", "while_statement",
            "try_statement", "except_clause", "match_statement", "case_clause",
            "conditional_expression", "boolean_operator",
        }
    )
    # The iterable's own syntax (``_magnitude``) says whether a read grows.
    read_grows = None

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        if node.type == "assignment":
            target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        elif node.type == "named_expression":
            target, value = node.child_by_field_name("name"), node.child_by_field_name("value")
        else:
            return []
        ok = target is not None and target.type == "identifier" and target.text and value is not None
        return [(target.text, value)] if ok else []


DIALECT = PythonPerfDialect()
