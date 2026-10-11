"""Rust ``PerfDialect``.

Flagship: a ``sqlx`` query or a ``std::fs`` / ``reqwest`` round-trip inside a
``for … in collection`` loop (the Rust N+1). Rust-distinctive markers ride along:

* ``regex_compile_in_loop`` — ``Regex::new("…")`` recompiled every iteration
  instead of hoisted into a ``once_cell`` / ``lazy_static``. Compiling a Rust
  ``regex`` is famously expensive, so this is the same high-precision marker
  Java/Go already emit; clippy has no in-loop analogue.
* ``resource_construction_in_loop`` — a fresh ``reqwest::Client::new()`` /
  ``PgPool::connect()`` / ``redis::Client::open()`` per iteration instead of a
  hoisted, pooled one.
* ``blocking_sync_in_async`` — a synchronous, executor-blocking call inside an
  ``async fn``: ``block_on`` (the canonical deadlock-shaped smell),
  ``std::thread::sleep`` (use ``tokio::time::sleep().await``), and the sync
  ``std::fs`` family (use ``tokio::fs`` / ``spawn_blocking``). The walker's ``not
  awaited`` gate excludes the async ``tokio::*`` equivalents by construction
  (they are always ``.await``ed).

Rust has no compile-optimization hazard around ``String`` building — ``push_str``
/ ``+=`` on a ``String`` are amortized O(1) (the buffer grows geometrically), so
``string_concat_in_loop`` is deliberately NOT in :attr:`markers`: it would be a
guaranteed false positive, which is why markers are scoped per language.

The per-grammar seam: Rust spells free calls (``foo()``), method calls
(``x.fetch_all()`` -> ``call_expression`` over a ``field_expression``) and
scoped/associated calls (``std::fs::read()`` / ``Regex::new()`` ->
``call_expression`` over a ``scoped_identifier``) all as ``call_expression``. The
base extraction returns the right method for every form and the right root for
the bare/method forms; :meth:`callee_root_name` is overridden so a scoped call's
"root" is its module/type QUALIFIER (``std::fs::read`` -> ``fs``, ``reqwest::get``
-> ``reqwest``, ``File::open`` -> ``File``), letting ``sink_kind`` match on the
distinctive segment instead of the leftmost crate root (which would be ``std``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .iterables import IterableSources, text_of
from .python import HTTP_VERBS

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import SinkProbe
    from .iterables import Source

# Rust string-literal node kinds (the hoistable-pattern gate).
_RUST_STRING_KINDS: frozenset[str] = frozenset({"string_literal", "raw_string_literal"})

# ``std::fs`` / ``tokio::fs`` / ``async_std::fs`` filesystem round-trips, keyed on
# the function name once an ``fs`` module qualifier is the callee root.
RUST_FS_METHODS: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "read_to_string",
        "read_dir",
        "copy",
        "rename",
        "remove_file",
        "remove_dir",
        "remove_dir_all",
        "create_dir",
        "create_dir_all",
        "metadata",
        "canonicalize",
        "hard_link",
        "read_link",
        "set_permissions",
    }
)
# The synchronous, executor-blocking subset — flagged inside an ``async fn`` as
# ``blocking_sync_in_async`` (use ``tokio::fs`` / ``spawn_blocking``).
RUST_SYNC_FS_METHODS: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "read_to_string",
        "read_dir",
        "copy",
        "remove_file",
        "create_dir",
        "create_dir_all",
        "metadata",
    }
)
# Distinctive sqlx / sea-orm executor verbs (a real DB round-trip, not a builder
# step). ``fetch_all`` / ``fetch_one`` / ``fetch_optional`` are sqlx-only verbs
# with no common non-db collision, so they fire un-gated; the ambiguous
# ``execute`` is gated on db-import evidence.
RUST_DB_EXEC: frozenset[str] = frozenset({"fetch_all", "fetch_one", "fetch_optional"})

# Heavy connection / client constructors keyed ``(type_qualifier, method)``.
# ``PgPool::connect`` etc. are distinctive on their own; ``Client::new`` /
# ``Client::open`` collide (many types have ``new``) so they are gated on a
# ``reqwest`` / ``redis`` segment in :meth:`loop_call_marker`.
RUST_RESOURCE_CTORS: frozenset[tuple[str, str]] = frozenset(
    {
        ("PgPool", "connect"),
        ("MySqlPool", "connect"),
        ("SqlitePool", "connect"),
        ("AnyPool", "connect"),
        ("Pool", "connect"),
        ("PgConnection", "connect"),
        ("MySqlConnection", "connect"),
        ("SqliteConnection", "connect"),
        ("PgPoolOptions", "connect_lazy"),
    }
)
# ``Regex::new`` / ``RegexBuilder::new`` — the recompile-in-loop type qualifiers.
RUST_REGEX_TYPES: frozenset[str] = frozenset({"Regex", "RegexBuilder", "RegexSet"})


def _scoped_segments(call_node: Node) -> list[str] | None:
    """Segments of a ``scoped_identifier`` callee (``std::fs::read`` ->
    ``['std', 'fs', 'read']``), or ``None`` when the callee is a bare or method
    call. The last element is the called function / associated name."""
    fn = call_node.child_by_field_name("function")
    if fn is None or fn.type != "scoped_identifier":
        return None
    txt = (fn.text or b"").decode("utf-8", "replace")
    segs = [s for s in txt.split("::") if s]
    return segs or None


_RS_FN_KINDS: frozenset[str] = frozenset({"function_item", "closure_expression"})
# Directory walkers that recurse unless capped with ``max_depth``.
_RS_WALKERS: frozenset[str] = frozenset({"WalkDir", "WalkBuilder"})
# sqlx query builders whose ``fetch`` streams the rows they select.
_SQLX_QUERIES: frozenset[str] = frozenset(
    {"query", "query_as", "query_scalar", "query_with", "query_as_with"}
)


def _rs_bindings(node: Node) -> list[tuple[bytes, Node]]:
    """What *node* binds: ``let x = v`` or ``x = v``."""
    if node.type == "let_declaration":
        target, value = node.child_by_field_name("pattern"), node.child_by_field_name("value")
    elif node.type == "assignment_expression":
        target, value = node.child_by_field_name("left"), node.child_by_field_name("right")
    else:
        return []
    ok = target is not None and target.type == "identifier" and target.text and value is not None
    return [(target.text, value)] if ok else []


def _rs_file_bindings(item: Node) -> list[tuple[bytes, Node]]:
    """A file-level ``const`` / ``static`` (never ``static mut``)."""
    if item.type not in ("const_item", "static_item"):
        return []
    if any(child.type == "mutable_specifier" for child in item.children):
        return []
    name, value = item.child_by_field_name("name"), item.child_by_field_name("value")
    return [(name.text, value)] if name is not None and name.text and value is not None else []


# Statements and the field holding the pattern they declare names in.
_RS_PATTERN_FIELDS: dict[str, str] = {
    "let_declaration": "pattern",
    "for_expression": "pattern",
    "parameter": "pattern",
    "let_condition": "pattern",
    "match_arm": "pattern",
}


def _rs_declared(node: Node) -> list[bytes]:
    """Names a ``let`` / ``for`` / parameter / ``if let`` / match / closure pattern declares."""
    if node.type == "closure_parameters":
        pattern: Node | None = node
    else:
        field = _RS_PATTERN_FIELDS.get(node.type)
        pattern = node.child_by_field_name(field) if field else None
    if pattern is None:
        return []
    return [n.text for n in IterableSources._walk(pattern) if n.type == "identifier" and n.text]


def _rs_escaped(args: list[Node]) -> list[bytes]:
    """``xs`` of ``f(&mut xs)`` / ``f(xs)``: a callee that may grow it."""
    names: list[bytes] = []
    for arg in args:
        if arg.type == "reference_expression" and any(c.type == "mutable_specifier" for c in arg.children):
            arg = arg.child_by_field_name("value") or arg
        if arg.type == "identifier" and arg.text:
            names.append(arg.text)
    return names


def _range_end(node: Node) -> Node | None:
    """``n`` of ``a..n`` / ``a..=n``; ``None`` for an open ``a..``."""
    last = node.children[-1] if node.children else None
    return last if last is not None and last.is_named and node.named_child_count == 2 else None


def _deref(node: Node | None) -> Node | None:
    """``xs`` of ``&xs`` / ``&mut xs``."""
    while node is not None and node.type == "reference_expression":
        node = node.child_by_field_name("value")
    return node


class RustPerfDialect(IterableSources):
    language = "rust"
    markers = frozenset(
        {
            "io_in_loop",
            "blocking_sync_in_async",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "serial_await_in_loop",
            # Centrality-gated / nesting-confidence markers (parity).
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
        }
    )

    # -- callee extraction (the per-grammar seam) -----------------------------

    def callee_root_name(self, call_node: Node) -> str | None:
        """For a scoped/associated call, the QUALIFIER segment (the one before
        the called name): ``std::fs::read`` -> ``fs``, ``reqwest::get`` ->
        ``reqwest``, ``File::open`` -> ``File``, ``reqwest::Client::new`` ->
        ``Client``. For bare/method calls, the base extraction (the receiver /
        function identifier) is correct."""
        segs = _scoped_segments(call_node)
        if segs is not None and len(segs) >= 2:
            return segs[-2]
        return super().callee_root_name(call_node)

    # -- sink classification --------------------------------------------------

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
        # Scoped/associated calls (``not is_attribute``): ``root`` is the
        # module/type qualifier supplied by :meth:`callee_root_name`.
        if not is_attribute:
            if root == "fs" and method in RUST_FS_METHODS:
                return "filesystem"
            if root == "File" and method in ("open", "create"):
                return "filesystem"
            if root == "reqwest" and method in HTTP_VERBS:
                return "network"
            return None
        # Method call on a receiver (``q.fetch_all(&pool)``).
        if method in RUST_DB_EXEC:
            return "db"
        if method == "execute" and (has_db_import or io_names.get(root) == "db"):
            return "db"
        return None

    # -- loop / async predicates ----------------------------------------------

    def is_constant_loop(self, node: Node) -> bool:
        """``for _ in 0..10`` (an integer-literal range) is a bounded count loop,
        not a data-dependent N+1. ``for x in 0..n`` / ``for x in items`` and
        ``while`` / ``loop`` are real loops."""
        if node.type != "for_expression":
            return False
        val = node.child_by_field_name("value")
        if val is None or val.type != "range_expression":
            return False
        named = [c for c in val.children if c.is_named]
        return bool(named) and all(c.type == "integer_literal" for c in named)

    def is_iteration_loop(self, node: Node) -> bool:
        """Only ``for x in collection`` multiplies over data; ``while`` / ``loop``
        spin a cursor (the ``nested_loop_with_io`` outer-loop gate)."""
        return node.type == "for_expression"

    def loop_iterable_name(self, node: Node) -> str | None:
        """The collection a ``for x in coll`` loop iterates (``coll``) — the
        same-collection ``nested_loop_quadratic`` shape gate."""
        if node.type != "for_expression":
            return None
        val = node.child_by_field_name("value")
        if val is None:
            return None
        if val.type in ("identifier", "field_expression"):
            return self._dotted_path(val)
        return None

    def is_async_fn(self, node: Node) -> bool:
        """``async fn`` carries an ``async`` token inside a ``function_modifiers``
        child (tree-sitter-rust has no dedicated async function node type)."""
        return any(
            c.type == "function_modifiers" and any(cc.type == "async" for cc in c.children)
            for c in node.children
        )

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        """Executor-blocking sync calls flagged inside an ``async fn``.

        The walker only calls this for a NON-awaited call, which excludes the
        async ``tokio::*`` equivalents (always ``.await``ed) by construction, so
        the ``sleep`` / ``fs`` matches cannot collide with their async forms.
        ``root`` is the scoped qualifier (``std::fs::read`` -> ``fs``,
        ``std::thread::sleep`` -> ``thread``).
        """
        if method == "block_on":
            # ``futures::executor::block_on`` / ``Handle::block_on`` inside async
            # blocks the executor thread — the canonical sync-in-async smell.
            return "block_on"
        if root in ("std", "thread") and method == "sleep":
            return "thread::sleep"
        if root in ("std", "fs") and method in RUST_SYNC_FS_METHODS:
            return f"fs::{method}"
        return None

    # -- extra-marker hooks ---------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        segs = _scoped_segments(node)
        if segs is None or len(segs) < 2:
            return None
        type_seg = segs[-2]
        earlier = {s.lower() for s in segs[:-2]}
        # ``Regex::new("^…$")`` recompiled per iteration — only a string-literal
        # pattern is unambiguously hoistable (a dynamic ``Regex::new(&pat)`` may
        # legitimately vary), mirroring the Go/Java regex gate.
        if type_seg in RUST_REGEX_TYPES and method == "new" and self._has_static_pattern_arg(node):
            return "regex_compile_in_loop"
        # Heavy connection/client constructors per iteration.
        if (type_seg, method) in RUST_RESOURCE_CTORS:
            return "resource_construction_in_loop"
        # ``reqwest::Client::new()`` / ``redis::Client::open()`` — the generic
        # ``Client`` qualifier is gated on the distinctive crate segment.
        if type_seg == "Client" and method == "new" and "reqwest" in earlier:
            return "resource_construction_in_loop"
        if type_seg == "Client" and method == "open" and "redis" in earlier:
            return "resource_construction_in_loop"
        return None

    # -- what a loop walks (perf/dialects/iterables.py) ---------------------------

    function_kinds = _RS_FN_KINDS

    int_kinds = frozenset({"integer_literal"})
    # ``&xs`` / ``&mut xs`` walk ``xs``; a call handed ``&mut xs`` before the
    # loop may grow it (:meth:`escaped_names`).
    wrapper_kinds = frozenset(
        {"parenthesized_expression", "try_expression", "await_expression", "reference_expression"}
    )
    pass_through = frozenset(
        {
            "iter", "iter_mut", "into_iter", "enumerate", "rev", "cloned", "copied", "filter",
            "filter_map", "map", "flat_map", "flatten", "skip", "skip_while", "take_while",
            "peekable", "keys", "values", "values_mut", "into_keys", "into_values", "drain",
            "windows", "chunks", "chunks_exact", "by_ref", "as_slice", "to_vec", "collect",
            "inspect", "step_by", "sorted", "unique",
        }
    )
    caps = frozenset({"take"})
    size_names = frozenset({"len"})
    # ``while let Some(row) = rows.next()`` (rusqlite), ``stream.try_next().await`` (sqlx).
    cursor_advances = frozenset({"next", "try_next"})
    listings = frozenset({"read_dir", "glob"})
    recursive_listings = _RS_WALKERS
    query_caps = frozenset({"limit"})
    member_kinds = frozenset({"field_expression"})
    change_kinds = frozenset({"assignment_expression", "compound_assignment_expr"})
    grow_methods = frozenset(
        {
            "push", "push_str", "extend", "extend_from_slice", "insert", "append", "push_back",
            "push_front", "resize",
        }
    )
    conditional_kinds = frozenset(
        {
            "if_expression", "if_let_expression", "else_clause", "match_expression", "match_arm",
            "for_expression", "while_expression", "while_let_expression", "loop_expression",
        }
    )

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        if loop.type == "for_expression":
            value = _deref(loop.child_by_field_name("value"))
            if value is not None and value.type == "range_expression":
                return "counted", _range_end(value)
            return "each", value
        if loop.type in ("while_expression", "while_let_expression"):
            cond = loop.child_by_field_name("condition") or loop.child_by_field_name("value")
            if cond is not None and cond.type == "let_condition":
                cond = cond.child_by_field_name("value")
            return ("cursor", cond) if cond is not None else ("each", None)
        return "each", None

    def is_literal(self, node: Node) -> bool:
        """``[a, b]`` / ``[0; 16]`` / ``vec![a, b]`` / ``vec![0; 16]`` / ``0..10``."""
        if node.type == "range_expression":
            ends = node.named_children
            return len(ends) == 2 and all(end.type in self.int_kinds for end in ends)
        if node.type == "array_expression":
            length = node.child_by_field_name("length")
            if length is not None:
                return length.type in self.int_kinds
            return any("comment" not in c.type for c in node.named_children)
        return node.type == "macro_invocation" and self._vec_literal(node)

    def _vec_literal(self, node: Node) -> bool:
        if text_of(node.child_by_field_name("macro")) != "vec":
            return False
        tree = next((c for c in node.named_children if c.type == "token_tree"), None)
        entries = tree.named_children if tree is not None else []
        if tree is not None and any(c.type == ";" for c in tree.children):
            return bool(entries) and entries[-1].type in self.int_kinds
        return bool(entries)

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        arg_nodes = [c for c in args.named_children if "comment" not in c.type] if args else []
        if fn is not None and fn.type == "field_expression":
            field, value = fn.child_by_field_name("field"), fn.child_by_field_name("value")
            return text_of(field), value, arg_nodes
        if fn is not None and fn.type == "scoped_identifier":
            # ``WalkDir::new(p)``: the receiver is the type the function belongs to.
            return text_of(fn.child_by_field_name("name")), fn.child_by_field_name("path"), arg_nodes
        return text_of(fn), None, arg_nodes

    def special_call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
    ) -> Source | None:
        root = self.call_parts(self.chain_root(call))
        if root is not None and root[0].startswith("build") and root[1] is not None:
            # ``builder.build().fetch_all(..)``: the SQL, its filter and its LIMIT
            # were pushed onto the builder piece by piece, out of sight here.
            reads = probe(call) == "db" or self.query_chain(call)
            return ("query", None) if reads else None
        if root is None or root[0] != "new" or root[1] is None:
            return None
        if text_of(root[1]).rsplit("::", 1)[-1] not in _RS_WALKERS:
            return None
        # ``WalkDir::new(p).max_depth(1)`` lists one directory.
        recursive = b"max_depth" not in (call.text or b"")
        grows = recursive and self._walks_repo(call, "WalkDir", loop)
        return "listing", "grows_with_data" if grows else None

    def query_chain(self, node: Node) -> bool:
        """A read the probe does not name: a sqlx ``fetch`` stream, rusqlite's
        ``query_map`` / ``stmt.query``, diesel's ``load(conn)`` / ``get_results``."""
        parts = self.call_parts(node)
        if parts is None:
            return False
        method, receiver, args = parts
        if method in ("fetch", "fetch_many"):
            root_parts = self.call_parts(self.chain_root(node))
            if root_parts is None:
                return False
            base = self._unwrap(root_parts[1]) if root_parts[1] is not None else None
            if base is not None and base.type == "macro_invocation":
                # ``sqlx::query!(..).fetch(..)``
                return text_of(base.child_by_field_name("macro")).startswith("query")
            return root_parts[0] in _SQLX_QUERIES
        if method in ("query_map", "query_and_then", "get_results"):
            return True
        if method == "query":
            return text_of(receiver).lower().endswith(("stmt", "statement"))
        return method == "load" and bool(args) and "conn" in text_of(args[0])

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        return _rs_bindings(node)

    def declared_names(self, node: Node) -> list[bytes]:
        return _rs_declared(node)

    def escaped_names(self, node: Node) -> list[bytes]:
        parts = self.call_parts(node)
        return _rs_escaped(parts[2]) if parts is not None else []

    def file_bindings_of(self, item: Node) -> list[tuple[bytes, Node]]:
        return _rs_file_bindings(item)

    def param_names(self, loop: Node) -> set[bytes]:
        scope = self._scope(loop)
        params = scope.child_by_field_name("parameters") if scope is not None else None
        names: set[bytes] = set()
        for param in params.named_children if params is not None else ():
            target = param.child_by_field_name("pattern") if param.type == "parameter" else param
            if target is not None and target.type == "identifier" and target.text:
                names.add(target.text)
        return names

    @staticmethod
    def _has_static_pattern_arg(node: Node) -> bool:
        """True if the call's first argument is a compile-time string literal
        (``Regex::new("^x$")`` is hoistable; ``Regex::new(&pat)`` is not)."""
        args = node.child_by_field_name("arguments")
        if args is None:
            return False
        first = next((c for c in args.children if c.is_named), None)
        return first is not None and first.type in _RUST_STRING_KINDS


DIALECT = RustPerfDialect()
