"""TypeScript / JavaScript ``PerfDialect``.

One instance serves ``typescript`` / ``tsx`` and ``javascript`` / ``jsx``, which
share a call grammar.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, ClassVar

from .. import loop_lexicon as lex
from ..loop_facts import BatchForm, LoopSource
from .iterables import IterableSources
from .python import HTTP_VERBS
from .ts_js_markers import TsJsMarkerHooks, first_named_child, identifier_name

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..loop_facts import LoopIterates, LoopMagnitude, SinkProbe

    Source = tuple[LoopIterates, LoopMagnitude | None]

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

# A function/lambda scope: the boundary for the reaching-assignment walk and the
# "not a loop exit" skip for break/return/throw inside a nested closure (a
# callback's own ``return`` doesn't exit the loop).
_FN_KINDS: frozenset[str] = frozenset(
    {"function_declaration", "function_expression", "arrow_function", "method_definition"}
)

# The prisma point calls a loop can batch, and the bulk call each becomes.
_PRISMA_BULK_FORMS: dict[str, str] = {
    "findUnique": "findMany",
    "findFirst": "findMany",
    "delete": "deleteMany",
}

# A concurrency limiter, named by convention (``limit`` / ``this.sem``), or by the
# method async-mutex (``runExclusive``) and bottleneck (``schedule``) run work through.
_LIMITER_NAME_RE = re.compile(r"(?i)(sem|semaphore|limiter|limit|mutex)$")
_LIMITER_METHODS: frozenset[str] = frozenset({"runExclusive", "schedule"})

# ESLint's rule for an ``await`` inside a loop, and its directive comments:
# ``// eslint-disable-next-line no-await-in-loop -- why`` and friends.
_AWAIT_IN_LOOP_RULE = "no-await-in-loop"
_ESLINT_DIRECTIVE_RE = re.compile(
    rb"(?://|/\*)[ \t]*eslint-(disable-next-line|disable-line|disable|enable)\b([^\r\n]*)"
)


def _directive_rules(tail: bytes) -> set[str]:
    """Rule names a directive lists, its ``-- reason`` and comment end dropped."""
    text = tail.decode("utf-8", "replace").split("*/")[0].split("--")[0]
    return {rule.strip() for rule in text.split(",") if rule.strip()}


def _eslint_allowed_lines(source: bytes, rule: str) -> frozenset[int]:
    """1-indexed lines an ESLint directive naming *rule* switches it off on.

    A directive with no rule list switches off every rule; it says nothing about
    this one in particular, so it does not count.
    """
    lines: set[int] = set()
    opened: int | None = None
    for match in _ESLINT_DIRECTIVE_RE.finditer(source):
        kind = match.group(1)
        rules = _directive_rules(match.group(2))
        line = source.count(b"\n", 0, match.start()) + 1
        if kind == b"enable":
            if opened is not None and (not rules or rule in rules):
                lines.update(range(opened, line + 1))
                opened = None
        elif rule not in rules:
            continue
        elif kind == b"disable-next-line":
            lines.add(line + 1)
        elif kind == b"disable-line":
            lines.add(line)
        elif opened is None:
            opened = line
    if opened is not None:
        lines.update(range(opened, source.count(b"\n") + 2))
    return frozenset(lines)

_ASSIGNMENT_FIELDS: dict[str, tuple[str, str]] = {
    "variable_declarator": ("name", "value"),
    "assignment_expression": ("left", "right"),
}


def _enclosing_scope(loop: Node) -> Node:
    """The function around *loop*, else the loop's own parent."""
    scope = loop.parent
    while scope is not None and scope.type not in _FN_KINDS:
        scope = scope.parent
    return scope or loop.parent or loop


def _unwrap(node: Node) -> Node:
    """Peel await / parens: ``(await x()).y`` needs both hops stripped to
    reach the call ``x()`` itself."""
    cur = node
    while cur.type in ("await_expression", "parenthesized_expression"):
        inner = first_named_child(cur)
        if inner is None:
            return cur
        cur = inner
    return cur


def _callee_object(call: Node) -> Node | None:
    """``x`` in ``x.method(...)``."""
    fn = call.child_by_field_name("function")
    return fn.child_by_field_name("object") if fn is not None else None


def _pair_key_is(node: Node, key: bytes) -> bool:
    return node.type == "pair" and (node.child_by_field_name("key") or node).text == key


def _option_value(call: Node, key: bytes) -> Node | None:
    """The value under *key* in the call's leading ``{ ... }`` options argument."""
    args = call.child_by_field_name("arguments")
    options = first_named_child(args) if args is not None else None
    if options is None or options.type != "object":
        return None
    return next(
        (pair.child_by_field_name("value") for pair in options.children if _pair_key_is(pair, key)),
        None,
    )


def _is_data_projection(node: Node) -> bool:
    """``response.data``: the body of whatever *response* resolves to."""
    prop = node.child_by_field_name("property")
    return node.type == "member_expression" and prop is not None and prop.text == b"data"


_TS_FUNCTION_KINDS = frozenset(
    {
        "function_declaration", "function_expression", "arrow_function", "method_definition",
        "generator_function_declaration", "generator_function",
    }
)


def _ts_walk(node: Node):
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(current.children)


def _declares(scope: Node, name: bytes) -> bool:
    """Whether a declarator, parameter or import anywhere in *scope* binds *name*."""
    for node in _ts_walk(scope):
        if node.type == "variable_declarator":
            key = node.child_by_field_name("name")
            if key is not None and key.text == name:
                return True
        elif node.type == "identifier" and node.text == name and node.parent is not None:
            parent = node.parent
            if parent.type in ("formal_parameters", "required_parameter", "optional_parameter"):
                return True
            if parent.type == "arrow_function" and parent.child_by_field_name("parameter") == node:
                return True
    return False


def _top_level_number_const(root: Node, name: bytes) -> bool:
    """``const NAME = 5`` as the only top-level binding of NAME, exported or not."""
    found: list[Node] = []
    for stmt in root.children:
        decl = stmt.child_by_field_name("declaration") if stmt.type == "export_statement" else stmt
        if decl is None:
            continue
        if decl.type in ("lexical_declaration", "variable_declaration"):
            found += [
                (decl, d) for d in decl.named_children
                if d.type == "variable_declarator"
                and (key := d.child_by_field_name("name")) is not None and key.text == name
            ]
        elif decl.type != "expression_statement" and _declares(decl, name):
            return False  # a function, class or import binds it too
    if len(found) != 1:
        return False
    decl, declarator = found[0]
    value = declarator.child_by_field_name("value")
    return decl.children[0].type == "const" and value is not None and value.type == "number"


def _module_number_constant(name: bytes, node: Node) -> bool:
    """``const NAME = 5`` at the top of *node*'s file, with no enclosing function
    binding NAME again (a parameter or a local of the same name shadows it)."""
    cur = node.parent
    while cur is not None and cur.parent is not None:
        if cur.type in _TS_FUNCTION_KINDS and _declares(cur, name):
            return False
        cur = cur.parent
    return cur is not None and _top_level_number_const(cur, name)


# Directory listings; a ``walk*`` helper or a ``recursive``/``**`` argument walks a tree.
_TS_LISTINGS = frozenset(
    {"readdir", "readdirSync", "glob", "globSync", "walk", "walkSync", "walkFiles"}
)
# Array and Map methods that walk (a projection of) their receiver.
_TS_PASS_THROUGH = frozenset(
    {"filter", "map", "flatMap", "toSorted", "sort", "toReversed", "reverse", "entries",
     "keys", "values"}
)
# ``Object.entries(x)`` / ``Array.from(x)`` walk their first argument.
_TS_ARG_WALKERS = frozenset({"entries", "keys", "values", "from"})


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", "replace")


def _param_names(fn: Node) -> set[bytes]:
    params = fn.child_by_field_name("parameters") or fn.child_by_field_name("parameter")
    if params is None:
        return set()
    kinds = ("identifier", "shorthand_property_identifier_pattern")
    return {n.text for n in _ts_walk(params) if n.type in kinds and n.text}


def _counted_stop(loop: Node) -> Node | None:
    """``n`` of a counted loop's ``i < n`` / ``i <= n``."""
    cond = loop.child_by_field_name("condition")
    if cond is not None and cond.type == "expression_statement":
        cond = first_named_child(cond)
    if cond is None or cond.type != "binary_expression":
        return None
    if not any(c.type in ("<", "<=") for c in cond.children):
        return None
    return cond.child_by_field_name("right")


def _fixed_count(stop: Node) -> bool:
    """``3``, or ``MAX`` that its file binds once to a number."""
    if stop.type == "number":
        return True
    name = identifier_name(stop)
    return bool(name) and name.isupper() and _module_number_constant(stop.text or b"", stop)


def _slice_is_bounded(call: Node) -> bool:
    """``.slice(a, N)`` where N is an integer literal or an ALL_CAPS constant
    its own file binds to one: a constant-width read, not the whole collection.
    The spelling alone proves nothing."""
    args = call.child_by_field_name("arguments")
    named = [c for c in args.children if c.is_named] if args is not None else []
    if len(named) != 2:
        return False
    end = named[1]
    if end.type == "number":
        return True
    name = identifier_name(end)
    return bool(name and name.isupper()) and _module_number_constant(end.text or b"", end)


def _sole_where_pair(call: Node) -> tuple[Node, Node] | None:
    """``(key, value)`` of a ``where`` object that filters on exactly one field."""
    where = _option_value(call, b"where")
    if where is None or where.type != "object":
        return None
    pairs = [c for c in where.children if c.type == "pair"]
    if len(pairs) != 1:
        return None
    key, value = pairs[0].child_by_field_name("key"), pairs[0].child_by_field_name("value")
    return (key, value) if key is not None and value is not None else None


class TsJsPerfDialect(TsJsMarkerHooks, IterableSources):
    language = "typescript"
    markers = frozenset(
        {
            "io_in_loop",
            # No ``string_concat_in_loop``: every major JS engine (V8, SpiderMonkey,
            # JavaScriptCore) concatenates through ropes, so ``s += x`` in a loop
            # is linear and is the idiom, not the problem (as Rust's ``push_str``).
            "resource_construction_in_loop",
            "serial_await_in_loop",
            "membership_test_against_list_in_loop",
            # Centrality-gated / nesting-confidence markers.
            "nested_loop_with_io",
            "nested_loop_quadratic",
            "hot_path_sync_io",
            # JS/TS-specific anti-patterns.
            "json_parse_in_loop",
            "array_spread_in_reduce",
        }
    )

    switch_kinds = frozenset({"switch_statement"})
    # Node's ``child_process`` launchers return a ChildProcess at once and report
    # through callbacks or events; only their ``*Sync`` forms wait for the child.
    # They stay sinks for the loop markers (one process per item is still a
    # cost) but are not blocking calls. Ceiling: shelljs's synchronous ``exec``
    # is excluded with them, a recall loss only.
    hot_path_excluded_methods = frozenset({"spawn", "exec", "execFile"})

    # Only ``for ... of`` / ``for ... in`` multiply over a collection. C-style
    # ``for (;;)``, ``while`` and ``do`` are cursors (pagination / polling), so
    # they do not make an inner sink a nested O(n*m) round-trip.
    _ITERATION_LOOP_KINDS: frozenset[str] = frozenset({"for_in_statement", "for_of_statement"})

    def awaits_in_loop_allowed(self, source: bytes) -> frozenset[int]:
        if _AWAIT_IN_LOOP_RULE.encode() not in source:
            return frozenset()
        return _eslint_allowed_lines(source, _AWAIT_IN_LOOP_RULE)

    def is_iteration_loop(self, node: Node) -> bool:
        return node.type in self._ITERATION_LOOP_KINDS

    def loop_iterable_name(self, node: Node) -> str | None:
        if node.type not in self._ITERATION_LOOP_KINDS:
            return None
        right = node.child_by_field_name("right")
        if right is not None and right.type in ("identifier", "member_expression"):
            return self._dotted_path(right)
        return None

    def is_constant_loop(self, node: Node) -> bool:
        """True if a ``for...of`` / ``for...in`` walks a fixed set: an inline array
        literal or an ALL_CAPS named constant, so no data-dependent N+1. C-style
        ``for (;;)`` / ``while`` / ``do`` are cursors and never constant."""
        if node.type not in self._ITERATION_LOOP_KINDS:
            return False
        right = node.child_by_field_name("right")
        if right is None:
            return False
        if right.type == "array":
            return True
        name = identifier_name(right)
        return name is not None and name.isupper() and len(name) > 1

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
        if method == "fetch":  # the ``fetch(...)`` global — no import to resolve
            return "network"
        root_kind = io_names.get(root)
        if root_kind is not None:
            # A call on an imported I/O package is a sink only as a round-trip
            # verb or when awaited, never a sync helper or a query builder.
            if method in TS_SINK_METHODS or awaited:
                return root_kind
            return None
        # Prisma verbs count only as member calls: a query goes through the client
        # (``prisma.user.aggregate``), so a bare ``aggregate(xs)`` is a local function.
        if is_attribute and method in PRISMA_METHODS:
            return "db"
        if method in TS_FS_METHODS:  # distinctive sync-fs verbs
            return "filesystem"
        return None

    # -- chunked-loop detection ------------------------------------------------

    def is_chunked_loop(self, node: Node) -> bool:
        """True when the loop already walks its data one chunk at a time.

        Two idioms: a C-style counter with a non-1 step (``i += CHUNK``), and a
        ``for...of`` over a ``chunk(xs, n)`` / lodash ``_.chunk(xs, n)`` call.
        """
        if node.type == "for_statement":
            return self._steps_by_chunk(node.child_by_field_name("increment"))
        if node.type in self._ITERATION_LOOP_KINDS:
            right = node.child_by_field_name("right")
            if right is None or right.type != "call_expression":
                return False
            return self.callee_method_name(right) == "chunk"
        return False

    # -- promotion facts (perf/loop_facts.py) ----------------------------------

    sequence_appends = frozenset({"push", "unshift", "splice"})
    await_kind = "await_expression"
    key_hops: ClassVar[dict[str, str]] = {
        "member_expression": "object",
        "subscript_expression": "object",
    }
    binding_fields: ClassVar[dict[str, str]] = {
        "variable_declarator": "name",
        "assignment_expression": "left",
        "augmented_assignment_expression": "left",
        "for_in_statement": "left",
    }
    branch_kinds = frozenset(
        {
            "if_statement", "try_statement", "switch_statement", "while_statement",
            "do_statement", "ternary_expression", "binary_expression",
        }
    )
    exit_kinds = frozenset(
        {"break_statement", "continue_statement", "return_statement", "throw_statement"}
    )
    scope_kinds = _FN_KINDS

    def _magnitude_of_expr(
        self, expr: Node | None, probe: SinkProbe, depth: int = 0
    ) -> LoopMagnitude | None:
        """Whether *expr* is provably a growing / bounded source, else None."""
        if expr is None or depth > 4:
            return None
        node = _unwrap(expr)
        if node.type == "call_expression":
            return self._call_magnitude(node, probe, depth)
        if node.type == "binary_expression" and any(c.type in ("||", "??") for c in node.children):
            # ``(await q).data ?? []``: the fallback is empty, the read is not.
            inner = node.child_by_field_name("left")
        elif _is_data_projection(node):
            inner = node.child_by_field_name("object")
        else:
            return None
        return self._grows(self._magnitude_of_expr(inner, probe, depth + 1))

    def _call_magnitude(self, call: Node, probe: SinkProbe, depth: int) -> LoopMagnitude | None:
        method = self.callee_method_name(call) or ""
        if method == "slice":
            return "bounded" if _slice_is_bounded(call) else None
        if probe(call) in ("db", "network"):
            # A read capped in the query (``take: n``) is as large as its cap, and
            # one filtered to a set of keys (``id: { in: ids }``) as large as the keys.
            capped = any(_pair_key_is(n, k) for n in self._walk(call) for k in (b"take", b"in"))
            return None if capped else "grows_with_data"
        if method == "json":
            # ``fetch(url).json()`` / ``(await sink()).json()`` — a
            # projection of whatever the receiver resolves to.
            return self._grows(self._magnitude_of_expr(_callee_object(call), probe, depth + 1))
        return None

    # -- what a loop walks: the shared reading (perf/dialects/iterables.py) -------
    function_kinds = _FN_KINDS
    member_kinds = frozenset({"member_expression"})
    recursive_listings = frozenset({"walk", "walkSync", "walkFiles"})
    recursive_marks = (b"**", b"recursive: true")
    conditional_kinds = frozenset(
        {
            "if_statement", "else_clause", "switch_statement", "switch_case", "switch_default",
            "for_statement", "for_in_statement", "while_statement", "do_statement",
            "try_statement", "catch_clause", "ternary_expression",
        }
    )
    # The iterable's own syntax (``_magnitude_of_expr``) says whether a read grows.
    read_grows = None

    def _scope(self, anchor: Node) -> Node | None:
        # A script's top-level loop reads the bindings around it.
        return _enclosing_scope(anchor)

    def bindings_of(self, node: Node) -> list[tuple[bytes, Node]]:
        fields = _ASSIGNMENT_FIELDS.get(node.type)
        if fields is None:
            return []
        target, value = (node.child_by_field_name(field) for field in fields)
        ok = target is not None and target.type == "identifier" and target.text and value is not None
        return [(target.text, value)] if ok else []

    def loop_source(self, loop: Node, probe: SinkProbe) -> LoopSource | None:
        if loop.type == "for_statement":
            iterates, claim = self._counted_source(loop, probe)
            return LoopSource(iterates, claim or "unknown") if iterates != "unknown" else None
        if not self.is_iteration_loop(loop):
            return None
        right = loop.child_by_field_name("right")
        baseline = self._expr_magnitude(right, loop, probe)
        iterates, claim = self._source(right, loop, probe, hop=True)
        if iterates == "unknown" and baseline is None:
            return None
        return LoopSource(iterates, claim or baseline or "unknown")

    def _expr_magnitude(
        self, expr: Node | None, loop: Node, probe: SinkProbe
    ) -> LoopMagnitude | None:
        """What *expr*'s syntax proves, one step through the name's last assignment."""
        magnitude = self._magnitude_of_expr(expr, probe)
        if magnitude is None and expr is not None and identifier_name(expr):
            return self._magnitude_of_expr(self.reaching_value(loop, expr.text), probe)
        return magnitude

    def _counted_source(self, loop: Node, probe: SinkProbe) -> Source:
        """``i < 3`` / ``i < MAX`` (a literal its file binds once) is a constant;
        ``i < rows.length`` walks ``rows``."""
        stop = _counted_stop(loop)
        if stop is None:
            return "unknown", None
        if _fixed_count(stop):
            return "constant", "bounded"
        prop = stop.child_by_field_name("property") if stop.type == "member_expression" else None
        if prop is not None and prop.text == b"length":
            walked = stop.child_by_field_name("object")
            iterates, claim = self._source(walked, loop, probe, hop=True)
            return iterates, claim or self._expr_magnitude(walked, loop, probe)
        return "unknown", None

    def _source(self, expr: Node | None, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        """What *expr* walks, and the magnitude that settles when it differs from
        what the iterable's syntax alone proves (``None``: keep that)."""
        if expr is None:
            return "unknown", None
        node = _unwrap(expr)
        if node.type == "binary_expression" and any(c.type in ("||", "??") for c in node.children):
            return self._source(node.child_by_field_name("left"), loop, probe, hop=hop)
        if node.type in ("identifier", "member_expression") and lex.is_config_name(_text(node)):
            # A registry grows with what users register: the name alone bounds nothing.
            return "config_table", None
        if node.type == "identifier":
            value = self.reaching_value(loop, node.text or b"") if hop else None
            if value is not None:
                return self._source(value, loop, probe, hop=False)
            if (node.text or b"") in _param_names(_enclosing_scope(loop)):
                return "parameter", None
            return "unknown", None
        if node.type == "member_expression" and _is_data_projection(node):
            return self._source(node.child_by_field_name("object"), loop, probe, hop=hop)
        if node.type == "call_expression":
            return self._call_source(node, loop, probe, hop=hop)
        return "unknown", None

    def _call_source(self, call: Node, loop: Node, probe: SinkProbe, *, hop: bool) -> Source:
        method, fn, receiver, first = self._call_bits(call)
        if lex.is_config_name(self._dotted_path(fn) or "", callee=True):
            return "config_table", None
        if method == "slice" and _slice_is_bounded(call):
            # A fixed-width slice bounds the trip count, not what is walked.
            return self._source(receiver, loop, probe, hop=hop)[0], "bounded"
        if receiver is not None and (
            receiver.text in (b"Object", b"Array") or method in _TS_PASS_THROUGH
        ):
            return self._walked_source(method, receiver, first, loop, probe, hop=hop)
        if method in _TS_LISTINGS:
            return "listing", "grows_with_data" if self._walks_repo(call, method, loop) else None
        if probe(call) in ("db", "network"):
            return self._query(call, loop)
        return "unknown", None

    def _call_bits(self, call: Node) -> tuple[str, Node | None, Node | None, Node | None]:
        """``(method, callee, receiver, first argument)`` of a call."""
        fn = call.child_by_field_name("function")
        is_member = fn is not None and fn.type == "member_expression"
        args = call.child_by_field_name("arguments")
        first = next((c for c in args.children if c.is_named), None) if args is not None else None
        return self.callee_method_name(call) or "", fn, _callee_object(call) if is_member else None, first

    def _walked_source(
        self,
        method: str,
        receiver: Node,
        first: Node | None,
        loop: Node,
        probe: SinkProbe,
        *,
        hop: bool,
    ) -> Source:
        """``Object.entries(x)`` / ``Array.from(x)`` walk ``x``; ``xs.filter(f)`` walks ``xs``."""
        if receiver.text in (b"Object", b"Array"):
            if method not in _TS_ARG_WALKERS:
                return "unknown", None
            return self._source(first, loop, probe, hop=hop)
        return self._source(receiver, loop, probe, hop=hop)

    def reads_config_table(self, call: Node, nodes: list[Node]) -> bool:
        """Every table the read names is a registry (``FROM repos``), or with no SQL,
        the delegate does (``prisma.repository.findMany()``)."""
        sql = [n.text or b"" for n in self._walk(call) if n.type in ("string", "template_string")]
        sql = [text for text in sql if lex.sql_tables(text)]
        if sql:
            return all(lex.is_config_sql(text) for text in sql)
        model = _callee_object(call)
        prop = model.child_by_field_name("property") if model is not None else None
        delegate = _text(prop) if prop is not None and model.type == "member_expression" else ""
        return bool(delegate) and lex.is_config_model(delegate[:1].upper() + delegate[1:])

    def _awaited_wrapper_call(self, closure: Node) -> Node | None:
        """The awaited call *closure* is passed to (``await limit(() => f(x))``).

        Awaited, the closure finishes before the loop moves on, so it is the loop
        body. Stored, returned or not awaited, it runs later and is not.
        """
        args = closure.parent
        if closure.type != "arrow_function" or args is None or args.type != "arguments":
            return None
        call = args.parent
        if call.type != "call_expression" or not self.is_awaited(call):
            return None
        fn = call.child_by_field_name("function")
        if fn is None or fn.type not in ("identifier", "member_expression"):
            return None
        return call

    def _limiter_of(self, closure: Node) -> str | None:
        """The limiter *closure* runs through. A curried ``pLimit(2)(...)`` builds a
        fresh limiter per call, which bounds nothing, so the callee must be named one."""
        call = self._awaited_wrapper_call(closure)
        if call is None:
            return None
        name = self.callee_method_name(call) or ""
        if not (_LIMITER_NAME_RE.search(name) or name in _LIMITER_METHODS):
            return None
        fn = call.child_by_field_name("function")
        return (fn.text or b"").decode() or None

    def runs_in_place(self, closure: Node) -> bool:
        return self._limiter_of(closure) is not None

    def is_awaited(self, node: Node) -> bool:
        # ``await limit(() => fetch(x))`` awaits what the closure's expression body returns.
        if super().is_awaited(node):
            return True
        parent = node.parent
        if parent is None or parent.child_by_field_name("body") != node:
            return False
        return self._limiter_of(parent) is not None

    def concurrency_bound(self, sink: Node, loop: Node) -> str | None:
        if self._exits_early(self.loop_body(loop) or loop):
            return None
        cur = sink.parent
        while cur is not None and cur != loop:
            limiter = self._limiter_of(cur)
            if limiter:
                return limiter
            cur = cur.parent
        return None

    def batch_form(self, sink: Node, loop: Node, probe: SinkProbe) -> BatchForm | None:
        """Prisma ``m.findUnique/findFirst/delete({ where: { f: key } })`` -> its bulk form."""
        element = self._loop_element(loop)
        point_call = self._prisma_point_call(sink)
        if element is None or point_call is None:
            return None
        receiver, method = point_call
        body = self.loop_body(loop) or loop
        if self._appends_to_iterable(loop, body):
            return None  # a worklist: its keys are not known before the loop
        field = self._where_key(sink, element)
        if field is None or self._reads_loop_local(sink, body):
            return None
        text = (receiver.text or b"").decode()
        return BatchForm(
            f"{text}.{_PRISMA_BULK_FORMS[method]}({{ where: {{ {field}: {{ in: keys }} }} }})",
            self._batch_equivalent(sink, body, probe),
        )

    def _loop_element(self, loop: Node) -> Node | None:
        """The identifier a ``for...of`` / ``for...in`` binds each element to."""
        target = loop.child_by_field_name("left")
        if not self.is_iteration_loop(loop) or identifier_name(target) is None:
            return None
        return target

    def _prisma_point_call(self, sink: Node) -> tuple[Node, str] | None:
        """``(receiver, method)`` of a ``m.findUnique/findFirst/delete(...)`` member call."""
        fn = sink.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            return None
        receiver, method = fn.child_by_field_name("object"), self.callee_method_name(sink)
        if receiver is None or method not in _PRISMA_BULK_FORMS:
            return None
        return receiver, method

    def _appends_to_iterable(self, loop: Node, body: Node) -> bool:
        """The body pushes onto the collection the loop walks."""
        iterable = self.loop_iterable_name(loop)
        return any(
            node.type == "call_expression"
            and self.callee_root_name(node) == iterable
            and self.callee_method_name(node) in self.sequence_appends
            for node in self._walk(body)
        )

    def _where_key(self, sink: Node, element: Node) -> str | None:
        """The field of a one-field ``where`` whose value is the loop *element*,
        when the sink names that element exactly once."""
        refs = [n for n in self._walk(sink) if n.type == "identifier" and n.text == element.text]
        pair = _sole_where_pair(sink)
        if len(refs) != 1 or pair is None or not self._is_key_of(pair[1], refs[0]):
            return None
        return (pair[0].text or b"").decode() or None

    def _batch_equivalent(self, sink: Node, body: Node, probe: SinkProbe) -> bool:
        """Whether the bulk call does exactly what the loop's per-row calls did."""
        if not self._only_io_in_body(sink, body, probe):
            return False
        method = self.callee_method_name(sink)
        if method == "delete":
            # Batched, a delete also removes rows a skipped iteration would have left.
            return self._unconditional(sink, body)
        return method == "findUnique" and not self._builds_in_order(body)


DIALECT = TsJsPerfDialect()
