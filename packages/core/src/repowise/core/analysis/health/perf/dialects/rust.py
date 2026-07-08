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
``string_concat_in_loop`` is deliberately NOT in :attr:`markers` (it would be a
guaranteed-FP marker — the language-scope rule in ``MARKER_BACKLOG.md``).

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

from .base import BasePerfDialect
from .python import HTTP_VERBS

if TYPE_CHECKING:
    from tree_sitter import Node

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


class RustPerfDialect(BasePerfDialect):
    language = "rust"
    markers = frozenset(
        {
            "io_in_loop",
            # Record own data-dependent loops so a caller looping over this
            # function becomes a cross-function quadratic target.
            "interprocedural_quadratic_loop",
            "blocking_sync_in_async",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "serial_await_in_loop",
            # Phase 7b — centrality-gated / nesting-confidence markers (parity).
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
