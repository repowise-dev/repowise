"""Ruby ``PerfDialect``.

Owns the **block-iteration loop definition** (the shared design decision the
plan assigns to Ruby): idiomatic Ruby iterates via combinator calls taking a
block (``items.each do |x| … end`` / ``items.map { … }``), not ``while``/``for``
nodes, so :meth:`block_loop_body` recognises a ``call`` whose method is a known
full-iteration combinator AND that carries an inline block, and returns the
block node as the per-iteration body. The walker then applies every loop rule
(body scoping, constant-bound skip, nesting, same-collection quadratic gate)
to it exactly as to a native loop. Scala (``.map``/``.foreach``), Kotlin and
Dart backport this hook rather than re-deciding the question.

Flagship: the canonical Rails N+1 — an ActiveRecord query inside ``.each``.
The AR lexicon is stratified like Python's DBAPI strata: distinctive verbs
(``find_by`` / ``pluck`` / ``update_all``…) fire ungated; ``where`` needs a
constant-rooted receiver (a model class); generic collision-prone verbs
(``find`` / ``first`` / ``count``…) additionally need file-level db evidence.
Rails' Zeitwerk autoloading means most Rails files carry no ``require`` at
all, so that evidence gate rarely opens there — a documented recall ceiling
that keeps ``Plugin.find(name)``-style in-memory registries from false-firing.

Grammar seams: a ``call`` node has ``receiver`` / ``method`` fields (no
``function`` field, so all three callee hooks are overridden); backticks are a
dedicated ``subshell`` node routed through a sentinel method name; ``x += y``
is an ``operator_assignment`` whose ``+=`` operator token satisfies the base
string-concat predicate, while ``s << x`` is a plain ``binary`` (amortized
append — never flagged, by construction).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .base import BasePerfDialect
from .python import HTTP_VERBS

if TYPE_CHECKING:
    from tree_sitter import Node

# Full-iteration combinators: a ``call`` with one of these methods AND an
# inline block is a loop scope (the block body runs once per element).
# Early-exit searches (``find`` / ``detect`` / ``any?``) are deliberately
# absent — they may stop after one element, so flagging their bodies as
# per-iteration would overclaim. ``loop`` is the unconditional-repeat form.
ITERATION_BLOCK_METHODS: frozenset[str] = frozenset(
    {
        "each",
        "each_with_index",
        "each_with_object",
        "each_slice",
        "each_cons",
        "each_pair",
        "each_key",
        "each_value",
        "each_entry",
        "each_line",
        "each_char",
        "each_byte",
        "reverse_each",
        "map",
        "map!",
        "flat_map",
        "collect",
        "collect!",
        "filter_map",
        "select",
        "select!",
        "filter",
        "reject",
        "reject!",
        "partition",
        "group_by",
        "sort_by",
        "min_by",
        "max_by",
        "sum",
        "count",
        "reduce",
        "inject",
        "times",
        "upto",
        "downto",
        "step",
        # ActiveRecord batch iteration — a loop AND a db sink in one call.
        "find_each",
        "find_in_batches",
        "in_batches",
        "loop",
    }
)

# ``.times`` / ``.upto`` — data-independent when the receiver is a literal.
_COUNTING_METHODS: frozenset[str] = frozenset({"times", "upto", "downto", "step"})

# --- filesystem --------------------------------------------------------------
_FILE_METHODS: frozenset[str] = frozenset(
    {
        "read",
        "write",
        "open",
        "readlines",
        "foreach",
        "binread",
        "binwrite",
        "delete",
        "unlink",
        "rename",
        "exist?",
        "exists?",
        "stat",
        "mtime",
        "size",
    }
)
_IO_FS_METHODS: frozenset[str] = frozenset(
    {"read", "write", "foreach", "readlines", "binread", "binwrite", "copy_stream"}
)
_DIR_METHODS: frozenset[str] = frozenset(
    {
        "glob",
        "entries",
        "children",
        "each_child",
        "foreach",
        "mkdir",
        "rmdir",
        "delete",
        "exist?",
        "exists?",
    }
)
_CSV_FS_METHODS: frozenset[str] = frozenset({"read", "foreach", "open"})

# --- network -----------------------------------------------------------------
_NET_HTTP_METHODS: frozenset[str] = frozenset(
    {
        "get",
        "post",
        "put",
        "delete",
        "patch",
        "head",
        "get_response",
        "post_form",
        "start",
        "request",
        "request_get",
        "request_post",
    }
)
# Distinctive HTTP-client constants — a verb on one of these is a round-trip.
_HTTP_CLIENT_ROOTS: frozenset[str] = frozenset(
    {"HTTParty", "Faraday", "RestClient", "Excon", "Typhoeus", "HTTP"}
)

# --- db ----------------------------------------------------------------------
# Stratum 1 — distinctive ActiveRecord/Mongoid verbs: no stdlib/core collision,
# fire ungated on any member call.
_AR_UNAMBIGUOUS: frozenset[str] = frozenset(
    {
        "find_by",
        "find_by!",
        "find_each",
        "find_in_batches",
        "in_batches",
        "find_or_create_by",
        "find_or_create_by!",
        "find_or_initialize_by",
        "pluck",
        "update_all",
        "delete_all",
        "destroy_all",
        "insert_all",
        "upsert_all",
        "exists?",
        # Bang persistence verbs — the raise-on-failure convention is
        # ActiveRecord-distinctive (plain ``create``/``save`` collide with
        # factory helpers and are left to the evidence-gated stratum below).
        # ``obj.assoc.create!`` inside ``.each`` is the classic N+1 write.
        "create!",
        "save!",
        "update!",
        "destroy!",
    }
)
# Raw-connection execution verbs (``conn.execute(sql)``) — the Python DBAPI
# posture: unambiguous when called as a member.
_DB_EXEC_METHODS: frozenset[str] = frozenset(
    {
        "execute",
        "exec_query",
        "exec_insert",
        "exec_update",
        "exec_delete",
        "select_all",
        "select_one",
        "select_value",
        "select_values",
    }
)
# Stratum 2 — ``where`` needs a constant-rooted receiver (an AR model class /
# Sequel ``DB`` handle); no Ruby core class responds to ``where``.
# Stratum 3 — collision-prone verbs (Enumerable/Hash surface) additionally need
# file-level db evidence (a classified db require), like Python's ambiguous
# stratum.
_AR_AMBIGUOUS: frozenset[str] = frozenset(
    {"find", "first", "last", "all", "count", "sum", "take", "create", "save", "update", "destroy"}
)

# --- subprocess ----------------------------------------------------------------
_OPEN3_METHODS: frozenset[str] = frozenset(
    {
        "capture2",
        "capture2e",
        "capture3",
        "popen2",
        "popen2e",
        "popen3",
        "pipeline",
        "pipeline_r",
        "pipeline_w",
        "pipeline_rw",
    }
)
_BARE_SUBPROCESS: frozenset[str] = frozenset({"system", "spawn", "exec"})

# Sentinel routed through the callee hooks for backtick ``subshell`` nodes —
# not a legal Ruby method name, so it can never collide.
_SUBSHELL = "__subshell__"

# Heavy-client constructors: building one per loop iteration opens a fresh
# connection/session instead of reusing a hoisted one.
_RESOURCE_NEW_ROOTS: frozenset[str] = frozenset(
    {"Net::HTTP", "Faraday", "Redis", "Mongo::Client", "Mysql2::Client"}
)
_RESOURCE_CONNECT_ROOTS: frozenset[str] = frozenset({"PG", "Sequel", "Mysql2"})

_REGEXP_CTOR_METHODS: frozenset[str] = frozenset({"new", "compile", "union"})

_STRING_KINDS: frozenset[str] = frozenset({"string"})


def _decode(node: Node | None) -> str | None:
    if node is None or node.text is None:
        return None
    return node.text.decode("utf-8", "replace")


class RubyPerfDialect(BasePerfDialect):
    language = "ruby"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            "blocking_io_under_lock",
        }
    )

    string_literal_kinds = _STRING_KINDS
    aug_assign_kinds = frozenset({"operator_assignment"})

    # -- callee extraction (receiver/method fields, no ``function`` field) -----

    def callee_method_name(self, call_node: Node) -> str | None:
        if call_node.type == "subshell":
            return _SUBSHELL
        return _decode(call_node.child_by_field_name("method"))

    def callee_root_name(self, call_node: Node) -> str | None:
        if call_node.type == "subshell":
            return _SUBSHELL
        recv = call_node.child_by_field_name("receiver")
        if recv is None:
            # Bare call: the root is its own name (``system("ls")`` -> system).
            return _decode(call_node.child_by_field_name("method"))
        # Walk down a chain (``Order.where(x).each`` -> the ``Order`` bottom).
        for _ in range(8):
            if recv.type != "call":
                break
            nxt = recv.child_by_field_name("receiver")
            if nxt is None:
                # Chain bottoms at a bare call (``helper().each``).
                return _decode(recv.child_by_field_name("method"))
            recv = nxt
        if recv.type == "scope_resolution":
            # Keep the full path — ``Net::HTTP`` / ``ActiveRecord::Base`` are
            # the distinctive lexicon keys.
            return _decode(recv)
        if recv.type in ("identifier", "constant", "instance_variable", "self"):
            return _decode(recv)
        return None

    def callee_is_attribute(self, call_node: Node) -> bool:
        return call_node.child_by_field_name("receiver") is not None

    # -- lexicon ----------------------------------------------------------------

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
        if method == _SUBSHELL:
            return "subprocess"
        # ``require`` binds lowercase feature segments (``net`` / ``httparty``)
        # while call receivers are constants — normalise before the lookup.
        root_kind = io_names.get(root) or io_names.get(root.split("::")[0].lower())
        db_ev = has_db_import or root_kind == "db"
        net_ev = root_kind == "network" or "network" in io_names.values()

        # Subprocess first — distinctive shapes, no gate needed.
        if root == "Open3" and method in _OPEN3_METHODS:
            return "subprocess"
        if root == "IO" and method == "popen":
            return "subprocess"
        if root in ("Process", "PTY") and method in ("spawn", "exec"):
            return "subprocess"
        if not is_attribute and method in _BARE_SUBPROCESS:
            return "subprocess"
        if root == "Kernel" and method in _BARE_SUBPROCESS:
            return "subprocess"

        # Filesystem — distinctive stdlib constants, before db so ``File.read``
        # / ``Dir.exist?`` never reach the ambiguous strata.
        if root == "File" and method in _FILE_METHODS:
            return "filesystem"
        if root == "IO" and method in _IO_FS_METHODS:
            return "filesystem"
        if root == "Dir" and method in _DIR_METHODS:
            return "filesystem"
        if root == "FileUtils":  # the whole module is filesystem verbs
            return "filesystem"
        if root == "Find" and method == "find":
            return "filesystem"
        if root == "Tempfile" and method in ("new", "create", "open"):
            return "filesystem"
        if root == "CSV" and method in _CSV_FS_METHODS:
            return "filesystem"
        if method == "load_file":  # ``YAML.load_file`` / ``JSON.load_file``
            return "filesystem"
        if not is_attribute and method == "open":  # bare Kernel#open
            return "filesystem"

        # Network — distinctive client constants.
        if root == "Net::HTTP" and method in _NET_HTTP_METHODS:
            return "network"
        if root in _HTTP_CLIENT_ROOTS and is_attribute and method in _NET_HTTP_METHODS:
            return "network"
        if root == "URI" and method == "open":  # open-uri
            return "network"
        # Instance clients (``conn.get`` on a Faraday connection) need
        # file-level network evidence — ``get``/``post`` are far too generic
        # alone (Sinatra's route DSL is a bare ``get``, already excluded by
        # ``is_attribute``).
        if is_attribute and net_ev and method in HTTP_VERBS:
            return "network"

        # DB — the stratified ActiveRecord/Sequel lexicon (see module docstring).
        if is_attribute and (method in _AR_UNAMBIGUOUS or method.startswith("find_by_")):
            return "db"
        if is_attribute and method in _DB_EXEC_METHODS:
            return "db"
        if is_attribute and method == "where" and root and root[0].isupper():
            return "db"
        if is_attribute and db_ev and (method in _AR_AMBIGUOUS or method == "where"):
            return "db"
        if is_attribute and db_ev and method == "run":  # Sequel ``DB.run(sql)``
            return "db"
        return None

    # -- block-iteration loops (the shared design decision) ---------------------

    def block_loop_body(self, node: Node) -> Node | None:
        if node.type != "call":
            return None
        block = node.child_by_field_name("block")
        if block is None:
            return None
        method = _decode(node.child_by_field_name("method"))
        return block if method in ITERATION_BLOCK_METHODS else None

    def is_constant_loop(self, node: Node) -> bool:
        """``for i in 1..3`` / ``[1, 2].each`` / ``3.times`` / ``ROLES.each`` —
        compile-time-constant bounds, not data-dependent multipliers."""
        if node.type == "for":
            it = self._for_iterable(node)
            return it is not None and self._is_constant_collection(it)
        if node.type == "call":
            recv = node.child_by_field_name("receiver")
            return recv is not None and self._is_constant_collection(recv)
        return False

    @staticmethod
    def _is_constant_collection(node: Node) -> bool:
        if node.type in ("integer", "float", "string", "array", "range"):
            if node.type == "range":
                return all(c.type == "integer" for c in node.children if c.is_named)
            return True
        if node.type == "constant" and node.text is not None:
            name = node.text.decode("utf-8", "replace")
            return name.isupper() and len(name) > 1  # ALL_CAPS named constant
        return False

    @staticmethod
    def _for_iterable(node: Node) -> Node | None:
        """The iterable of a ``for x in xs`` loop (the ``in`` node's payload)."""
        value = node.child_by_field_name("value")
        if value is None:
            return None
        return next((c for c in value.children if c.is_named), None)

    def is_iteration_loop(self, node: Node) -> bool:
        # ``while`` / ``until`` (+ modifier forms) and ``loop do`` are cursors
        # (pagination / retry), not data multipliers; ``for`` and the
        # collection combinators iterate a collection.
        if node.type == "for":
            return True
        if node.type == "call":
            method = _decode(node.child_by_field_name("method"))
            return method != "loop"
        return False

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type == "for":
            return self._dotted_path(self._for_iterable(node))
        if node.type == "call":
            method = _decode(node.child_by_field_name("method"))
            if method in _COUNTING_METHODS or method == "loop":
                return None  # ``n.times`` iterates a count, not a collection
            return self._dotted_path(node.child_by_field_name("receiver"))
        return None

    # -- string concat -----------------------------------------------------------

    def _rhs_is_stringish(self, node: Node) -> bool:
        # Same posture as the base predicate, but Ruby's binary-op node is
        # ``binary`` (not ``binary_expression``): ``s += x + "\n"`` counts when
        # a string literal is an operand.
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type in self.string_literal_kinds:
            return True
        if right.type == "binary":
            return any(c.is_named and c.type in self.string_literal_kinds for c in right.children)
        return False

    def is_string_concat(self, node: Node) -> bool:
        """``s += "<lit>"`` (a fresh String every pass — O(n^2)); ``s << x`` is
        amortized append and parses as a plain ``binary``, so it can never
        reach here. Mirrors the Python reset-per-iteration guard: an
        accumulator plainly re-assigned inside the enclosing loop body is
        bounded per iteration, not a cross-iteration accumulation.
        """
        if not super().is_string_concat(node):
            return False
        left = node.child_by_field_name("left")
        if left is None or left.type != "identifier" or left.text is None:
            return True  # opaque / @ivar target -> keep the precision-first flag
        name = left.text
        cur = node.parent
        while cur is not None:
            body: Node | None = None
            if cur.type in ("while", "until", "for"):
                body = cur.child_by_field_name("body")
            elif cur.type == "call":
                body = self.block_loop_body(cur)
            if body is not None and self._resets_name(body, name):
                return False
            cur = cur.parent
        return True

    @staticmethod
    def _resets_name(body: Node, name: bytes) -> bool:
        """True if *body* contains a plain ``name = …`` assignment (the
        accumulator is reset each iteration)."""
        stack: list[Node] = [body]
        while stack:
            n = stack.pop()
            if n.type == "assignment":
                lhs = n.child_by_field_name("left")
                if lhs is not None and lhs.type == "identifier" and lhs.text == name:
                    return True
            stack.extend(n.children)
        return False

    # -- extra loop markers --------------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        # ``Regexp.new(pattern)`` recompiles per iteration; ``/…/`` literals are
        # compiled once by the VM and never fire.
        if root == "Regexp" and method in _REGEXP_CTOR_METHODS:
            return "regex_compile_in_loop"
        if method == "new" and root in _RESOURCE_NEW_ROOTS:
            return "resource_construction_in_loop"
        if method == "connect" and root in _RESOURCE_CONNECT_ROOTS:
            return "resource_construction_in_loop"
        # ``mutex.synchronize { … }`` taken every iteration is a contention site.
        if method == "synchronize" and self.callee_is_attribute(node):
            return "lock_in_loop"
        return None

    def is_lock_scope(self, node: Node) -> bool:
        # ``mutex.synchronize do … end`` — the block argument is the held
        # region (the walker raises lock_depth for block-typed children only).
        if node.type != "call":
            return False
        return self.callee_method_name(node) == "synchronize"


DIALECT = RubyPerfDialect()
