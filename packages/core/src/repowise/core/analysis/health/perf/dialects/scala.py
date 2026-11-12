"""Scala ``PerfDialect``.

Rides the JVM lexicon: Scala call sites hit ``java.*`` interop verbatim, so the
Java dialect's sink tables are imported as the base and extended with the
Scala-native boundary libraries (``scala.io.Source``, os-lib, sttp / http4s,
Slick / doobie).

Flagship: ``"...".r`` recompiling a ``java.util.regex.Pattern`` on every loop
iteration - the top Scala perf footgun - plus ``Await.result`` inside a
``Future``-returning ``def`` (thread-pool starvation), the Scala spelling of
sync-in-async: the language has no ``async`` keyword, so ``is_async_fn`` sniffs
the declared ``Future[...]`` return type instead of a modifier token.

Grammar seams: a ``call_expression`` carries a ``function`` field whose member
form is a ``field_expression`` (fields ``value`` / ``field``), so the generic
base extraction works; only ``new Foo(...)`` (``instance_expression``, no
``function`` field) needs a dedicated arm. ``x += "..."`` is a plain
``infix_expression`` whose ``operator`` child carries the ``+=`` text - not a
compound-assignment node type - so ``is_string_concat`` is overridden outright,
and it additionally requires a same-file ``var x = "<string>"`` binding so a
``+=`` onto a mutable *collection* of strings never fires.

Documented ceiling: idiomatic Scala iterates via ``.map`` / ``.foreach`` whose
bodies are lambdas - a new execution scope where the walker resets
``loop_depth`` - so combinator-driven iteration produces no loop markers. Loops
here are ``while`` / ``do-while`` / for-comprehensions, pending the shared
block-iteration answer (02_ruby precedent). Caps recall, not precision.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .iterables import IterableSources, text_of
from .java import (
    _JAVA_TYPE_NAME,
    AMBIGUOUS_DB,
    FILES_METHODS,
    FS_CONSTRUCTORS,
    JAVA_LOCK_METHODS,
    JAVA_RESOURCE_CTORS,
    NET_CONSTRUCTORS,
    RECEIVER_DB_METHODS,
    ambiguous_db_verbs,
    is_repository_call,
)

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import SinkProbe
    from .iterables import Source

# ``scala.io.Source`` factories, split by boundary.
SOURCE_FS_METHODS: frozenset[str] = frozenset({"fromFile", "fromInputStream", "fromResource"})
SOURCE_NET_METHODS: frozenset[str] = frozenset({"fromURL"})
# lihaoyi os-lib round-trips (``os.read(path)`` / ``os.walk(dir)``). The bare
# ``os`` root is not seeded in the io_kind table (it would collide with
# Python's stdlib ``os`` cross-ecosystem), so the gate lives here: receiver
# exactly ``os`` + a distinctive method name.
OS_LIB_FS_METHODS: frozenset[str] = frozenset(
    {"read", "write", "list", "walk", "copy", "move", "remove", "makeDir", "stat", "exists"}
)
# ``os.proc(...)`` spawns a subprocess (the ``.call()`` finisher rides on it).
OS_LIB_SUBPROCESS_METHODS: frozenset[str] = frozenset({"proc"})
# doobie's ``query.transact(xa)`` - distinctive enough to classify ungated.
DOOBIE_METHODS: frozenset[str] = frozenset({"transact"})
# Slick's ``db.run(action)`` - ``run`` is generic, so it is gated on file-level
# db evidence like the shared AMBIGUOUS_DB stratum.
SLICK_AMBIGUOUS_DB: frozenset[str] = frozenset({"run"})
_SCALA_AMBIGUOUS_DB: frozenset[str] = AMBIGUOUS_DB | SLICK_AMBIGUOUS_DB
# http4s client verbs, gated on network evidence (``expect`` collides with the
# test-assertion vocabulary when un-gated).
HTTP4S_METHODS: frozenset[str] = frozenset({"expect", "fetch"})
# ``Await.result`` / ``Await.ready`` block a thread; ``Thread.sleep`` stalls it.
_AWAIT_METHODS: frozenset[str] = frozenset({"result", "ready"})

_STRING_KINDS: frozenset[str] = frozenset({"string", "interpolated_string_expression"})
_COLLECTION_FACTORIES = frozenset({"List", "Seq", "Vector", "Set", "Array", "Map"})


def _last_type_segment(node: Node) -> str | None:
    """Constructed type name of an ``instance_expression`` (``new a.b.Foo[T]``
    -> ``Foo``): the last ``type_identifier`` before any type arguments."""
    stack = [c for c in node.children if c.is_named]
    while stack:
        cur = stack.pop(0)
        if cur.type == "type_identifier" and cur.text:
            return cur.text.decode("utf-8", "replace").split(".")[-1]
        if cur.type in ("generic_type", "stable_type_identifier"):
            stack = [c for c in cur.children if c.is_named] + stack
    return None


def _scala_range(value: Node | None) -> tuple[str, Node | None]:
    """Read a literal-start ``to`` / ``until`` range, or an ordinary iterable."""
    if value is not None and value.type == "infix_expression":
        op = text_of(value.child_by_field_name("operator"))
        start = value.child_by_field_name("left")
        if op in ("to", "until") and start is not None and start.type == "integer_literal":
            return "counted", value.child_by_field_name("right")
    return "each", value


class ScalaPerfDialect(IterableSources):
    language = "scala"
    markers = frozenset(
        {
            "io_in_loop",
            "string_concat_in_loop",
            "regex_compile_in_loop",
            "resource_construction_in_loop",
            "lock_in_loop",
            "blocking_sync_in_async",
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            "blocking_io_under_lock",
        }
    )

    # -- callee extraction ----------------------------------------------------

    def callee_method_name(self, call_node: Node) -> str | None:
        if call_node.type == "instance_expression":
            return _last_type_segment(call_node)
        return super().callee_method_name(call_node)

    def callee_root_name(self, call_node: Node) -> str | None:
        if call_node.type == "instance_expression":
            return _last_type_segment(call_node)
        return super().callee_root_name(call_node)

    def callee_is_attribute(self, call_node: Node) -> bool:
        if call_node.type == "instance_expression":
            return True  # a constructor sink (``new FileInputStream``)
        return super().callee_is_attribute(call_node)

    # -- lexicon --------------------------------------------------------------

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
        net_ev = root_kind == "network" or "network" in io_names.values()

        # JVM interop - verbatim from the Java lexicon.
        if method in FS_CONSTRUCTORS:
            return "filesystem"
        if method in NET_CONSTRUCTORS:
            return "network"
        if is_attribute and method in RECEIVER_DB_METHODS:
            return "db"
        if is_repository_call(method, root, io_names):
            return "db"
        if root == "Files" and method in FILES_METHODS:
            return "filesystem"
        if method == "exec" and root == "Runtime":
            return "subprocess"

        # Scala-native boundaries.
        if root == "Source" and method in SOURCE_FS_METHODS:
            return "filesystem"
        if root == "Source" and method in SOURCE_NET_METHODS:
            return "network"
        if root == "os" and is_attribute:
            if method in OS_LIB_FS_METHODS:
                return "filesystem"
            if method in OS_LIB_SUBPROCESS_METHODS:
                return "subprocess"
        if method in DOOBIE_METHODS and is_attribute:
            return "db"
        if method == "send" and net_ev:  # sttp ``request.send(backend)``
            return "network"
        if method in HTTP4S_METHODS and is_attribute and net_ev:
            return "network"
        if is_attribute and method in _SCALA_AMBIGUOUS_DB:
            # Slick's ``run`` needs the same library evidence as ``get``.
            verb = "get" if method in SLICK_AMBIGUOUS_DB else method
            return "db" if verb in ambiguous_db_verbs(io_names) else None
        return None

    # -- loops ----------------------------------------------------------------

    @staticmethod
    def _first_enumerator_iterable(node: Node) -> Node | None:
        """The iterable expression of a for-comprehension's first enumerator
        (``for (i <- items ...)`` -> the ``items`` node)."""
        # NB: ``child_by_field_name("enumerators")`` returns the ``(`` token -
        # the grammar stamps the field on the delimiters too - so look the
        # named node up by type.
        enums = next((c for c in node.children if c.type == "enumerators"), None)
        if enums is None:
            return None
        first = next((c for c in enums.children if c.type == "enumerator"), None)
        if first is None:
            return None
        named = [c for c in first.children if c.is_named and c.type != "guard"]
        return named[1] if len(named) >= 2 else None

    def is_constant_loop(self, node: Node) -> bool:
        # ``for (i <- 1 to 3)`` / ``0 until N-literal`` - a compile-time bound,
        # not a data-dependent multiplier.
        if node.type != "for_expression":
            return False
        it = self._first_enumerator_iterable(node)
        if it is None or it.type != "infix_expression":
            return False
        op = it.child_by_field_name("operator")
        if op is None or op.text not in (b"to", b"until"):
            return False
        left = it.child_by_field_name("left")
        right = it.child_by_field_name("right")
        return (
            left is not None
            and right is not None
            and left.type == "integer_literal"
            and right.type == "integer_literal"
        )

    def is_iteration_loop(self, node: Node) -> bool:
        # Only a for-comprehension multiplies over a collection; ``while`` /
        # ``do-while`` are cursors (pagination / retry), not data multipliers.
        return node.type == "for_expression"

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type != "for_expression":
            return None
        return self._dotted_path(self._first_enumerator_iterable(node))

    # -- loop sources ----------------------------------------------------------

    function_kinds = frozenset({"function_definition", "lambda_expression"})
    int_kinds = frozenset({"integer_literal"})
    wrapper_kinds = frozenset({"parenthesized_expression"})
    member_kinds = frozenset({"field_expression"})
    pass_through = frozenset(
        {
            "map", "filter", "flatMap", "toList", "toSeq", "iterator", "view",
            "keys", "values", "distinct", "sorted", "zipWithIndex",
        }
    )
    caps = frozenset({"take"})
    size_names = frozenset({"size", "length"})
    cursor_advances = frozenset({"next", "hasNext"})
    recursive_listings = frozenset({"walk", "find"})
    change_kinds = frozenset({"assignment_expression"})
    grow_methods = frozenset({"append", "addOne", "addAll", "prepend", "put", "update"})
    conditional_kinds = frozenset(
        {
            "if_expression", "match_expression", "case_clause", "try_expression",
            "catch_clause", "while_expression", "do_while_expression", "for_expression",
        }
    )

    def loop_header(self, loop: Node) -> tuple[str, Node | None]:
        if loop.type in ("while_expression", "do_while_expression"):
            return "cursor", loop.child_by_field_name("condition")
        if loop.type != "for_expression":
            return "each", None
        enums = next((c for c in loop.named_children if c.type == "enumerators"), None)
        # A later generator can multiply a fixed first collection by unknown
        # data. The walker represents the whole comprehension as one loop.
        if enums is None or sum(c.type == "enumerator" for c in enums.named_children) != 1:
            return "each", None
        return _scala_range(self._first_enumerator_iterable(loop))

    def call_parts(self, node: Node) -> tuple[str, Node | None, list[Node]] | None:
        if node.type == "field_expression":
            member = self.member_parts(node)
            return (member[0], member[1], []) if member is not None else None
        if node.type != "call_expression":
            return None
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "generic_function":
            fn = fn.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        arg_nodes = [c for c in args.named_children if "comment" not in c.type] if args else []
        member = self.member_parts(fn) if fn is not None else None
        if member is not None:
            return member[0], member[1], arg_nodes
        return (text_of(fn), None, arg_nodes) if fn is not None and fn.type == "identifier" else None

    def member_parts(self, node: Node) -> tuple[str, Node] | None:
        value = node.child_by_field_name("value") if node.type == "field_expression" else None
        return (text_of(node.child_by_field_name("field")), value) if value is not None else None

    def special_call(
        self,
        call: Node,
        parts: tuple[str, Node | None, list[Node]],
        loop: Node,
        probe: SinkProbe,
    ) -> Source | None:
        method, receiver, args = parts
        if receiver is None and method in _COLLECTION_FACTORIES:
            return ("constant", "bounded") if args and not any(self._is_spread(a) for a in args) else None
        owner = text_of(receiver)
        if method == "values" and not args and _JAVA_TYPE_NAME.fullmatch(owner):
            return "constant", "bounded"
        if owner == "Files" and method in ("list", "walk", "find", "newDirectoryStream"):
            grows = method in self.recursive_listings and self._walks_repo(call, method, loop)
            return "listing", "grows_with_data" if grows else None
        return None

    def _is_spread(self, arg: Node) -> bool:
        arg = self._unwrap(arg)
        # Scala 2 ``xs: _*`` and Scala 3 ``xs*`` expand an unknown number of entries.
        if arg.type == "ascription_expression":
            return any(c.type == "repeated_parameter_type" for c in arg.named_children)
        return arg.type == "postfix_expression" and text_of(arg.named_children[-1]) == "*"

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        if node.type in ("val_definition", "var_definition"):
            name, value = node.child_by_field_name("pattern"), node.child_by_field_name("value")
        elif node.type == "assignment_expression":
            name, value = node.child_by_field_name("left"), node.child_by_field_name("right")
        else:
            return []
        if name is not None and name.type == "identifier" and name.text and value is not None:
            return [(name.text, value)]
        return []

    def mutated_name(self, node: Node) -> bytes | None:
        if node.type == "infix_expression":
            op = text_of(node.child_by_field_name("operator"))
            left = node.child_by_field_name("left")
            if op in ("+=", "++=") and left is not None and left.type == "identifier":
                return left.text
        return super().mutated_name(node)

    def param_names(self, loop: Node) -> set[bytes]:
        scope = self._scope(loop)
        if scope is None:
            return set()
        return {
            name.text
            for params in scope.children_by_field_name("parameters")
            for param in params.named_children
            if param.type == "parameter"
            and (name := param.child_by_field_name("name")) is not None and name.text
        }

    # -- string concat ----------------------------------------------------------

    def is_string_concat(self, node: Node) -> bool:
        """``acc += "<lit>"`` or ``acc = acc + "<lit>"`` where ``acc`` is a
        same-file ``var`` initialized to a string.

        The var-binding gate is load-bearing: ``+=`` in Scala also appends to
        mutable collections (``buf += "x"`` on a ``ListBuffer[String]`` is
        amortized O(1)), so without it every builder append would false-fire.
        """
        left: Node | None
        if node.type == "infix_expression":
            op = node.child_by_field_name("operator")
            if op is None or op.text != b"+=":
                return False
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
        elif node.type == "assignment_expression":
            left = next((c for c in node.children if c.is_named), None)
            right = node.children[-1] if node.children else None
            # ``acc = acc + "<lit>"``: RHS is an infix ``+`` whose left operand
            # re-reads the assignment target.
            if (
                right is None
                or right.type != "infix_expression"
                or (op := right.child_by_field_name("operator")) is None
                or op.text != b"+"
                or left is None
                or (rl := right.child_by_field_name("left")) is None
                or rl.text != left.text
            ):
                return False
            right = right.child_by_field_name("right")
        else:
            return False
        if left is None or left.type != "identifier" or left.text is None:
            return False
        if right is None or right.type not in _STRING_KINDS:
            return False
        return self._is_string_var(node, left.text)

    @staticmethod
    def _is_string_var(node: Node, name: bytes) -> bool:
        """True when the file carries ``var <name> = "<string literal>"``."""
        root = node
        while root.parent is not None:
            root = root.parent
        stack = [root]
        while stack:
            cur = stack.pop()
            if cur.type == "var_definition":
                pattern = cur.child_by_field_name("pattern")
                value = cur.child_by_field_name("value")
                if (
                    pattern is not None
                    and pattern.text == name
                    and value is not None
                    and value.type in _STRING_KINDS
                ):
                    return True
            stack.extend(cur.children)
        return False

    # -- sync-over-Future -------------------------------------------------------

    def is_async_fn(self, node: Node) -> bool:
        # No ``async`` keyword - a ``def`` whose declared return type is
        # ``Future[...]`` is the async execution context. Undeclared return
        # types are not inferred (precision over recall).
        ret = node.child_by_field_name("return_type")
        if ret is None or ret.text is None:
            return False
        return ret.text.decode("utf-8", "replace").startswith("Future")

    def blocking_sync_api(self, root: str, method: str) -> str | None:
        if root == "Await" and method in _AWAIT_METHODS:
            return f"Await.{method}"
        if root == "Thread" and method == "sleep":
            return "Thread.sleep"
        return None

    # -- extra loop markers -------------------------------------------------------

    def loop_call_marker(
        self, root: str, method: str, node: Node, list_names: frozenset[str]
    ) -> str | None:
        if node.type == "instance_expression":
            return "resource_construction_in_loop" if method in JAVA_RESOURCE_CTORS else None
        # ``x.synchronized { ... }`` taken every iteration is a contention site;
        # ``lock.lock()`` is the java.util.concurrent form.
        if method == "synchronized" and self.callee_is_attribute(node):
            return "lock_in_loop"
        if method in JAVA_LOCK_METHODS and self.callee_is_attribute(node):
            return "lock_in_loop"
        # ``Pattern.compile(...)`` recompiled per iteration (JVM interop; match
        # on the receiver's LAST segment so an FQN receiver still resolves).
        if method != "compile":
            return None
        fn = node.child_by_field_name("function")
        recv = fn.child_by_field_name("value") if fn is not None else None
        if recv is not None and recv.text:
            last = recv.text.decode("utf-8", "replace").split(".")[-1]
            return "regex_compile_in_loop" if last == "Pattern" else None
        return "regex_compile_in_loop" if root == "Pattern" else None

    def loop_stmt_marker(self, node: Node, list_names: frozenset[str]) -> str | None:
        # ``"<lit>".r`` is a bare ``field_expression`` (StringOps.r compiles a
        # fresh ``Pattern`` on every call) - the top Scala regex footgun. Only
        # a literal receiver fires (a name bound outside the loop is the same
        # bug but not provably a string here).
        if node.type != "field_expression":
            return None
        field = node.child_by_field_name("field")
        value = node.child_by_field_name("value")
        if (
            field is not None
            and field.text == b"r"
            and value is not None
            and value.type in _STRING_KINDS
        ):
            return "regex_compile_in_loop"
        return None

    def is_lock_scope(self, node: Node) -> bool:
        # ``x.synchronized { ... }`` - the block argument is the held region
        # (the walker raises lock_depth for block-typed children only, which
        # excludes the receiver expression).
        if node.type != "call_expression":
            return False
        return self.callee_method_name(node) == "synchronized"


DIALECT = ScalaPerfDialect()
