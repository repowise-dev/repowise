"""Apex ``PerfDialect``: operations with governor limits inside a loop.

Inline SOQL/SOSL and DML are statements, not calls, so the Apex node map lists
them in ``call_kinds`` and :meth:`ApexPerfDialect.call_sink_kind` classifies
them by node shape. The rest of the lexicon is the platform's static API
(``Database.*``, callouts, ``Messaging.sendEmail``, ``System.enqueueJob``,
same-class ``@future`` calls), matched case-insensitively. The Java dialect
supplies the callee seam and the loop-source reading; only ``io_in_loop`` and
``nested_loop_with_io`` are emitted.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .iterables import upper_bound
from .java import JavaPerfDialect

if TYPE_CHECKING:
    from tree_sitter import Node

# Receiver (lowercased, ``System.`` prefix dropped) -> methods (lowercased) -> kind.
# Ceiling: ``Database.executeBatch`` / ``System.schedule`` are out; their loops walk job types.
_STATIC_SINKS: dict[str, dict[str, str]] = {
    "database": dict.fromkeys(
        (
            "query", "querywithbinds", "countquery", "countquerywithbinds",
            "getquerylocator", "getquerylocatorwithbinds", "insert", "update",
            "upsert", "delete", "undelete", "merge", "emptyrecyclebin",
            "convertlead", "insertimmediate", "updateimmediate", "deleteimmediate",
            "insertasync", "updateasync", "deleteasync",
        ),
        "db",
    ),
    "search": dict.fromkeys(("query", "find", "suggest"), "db"),
    "eventbus": {"publish": "db"},
    "approval": dict.fromkeys(("process", "lock", "unlock"), "db"),
    "system": {"enqueuejob": "db"},
    "messaging": dict.fromkeys(("sendemail", "sendemailmessage"), "network"),
    "webservicecallout": {"invoke": "network"},
}

_FN_KINDS: frozenset[str] = frozenset(
    {"method_declaration", "constructor_declaration", "trigger_declaration", "accessor_list"}
)
_CLASS = frozenset({"class_declaration"})


def _lower(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace").lower() if node is not None else ""


def _ancestor(node: Node, kinds: frozenset[str]) -> Node | None:
    cur = node.parent
    while cur is not None and cur.type not in kinds:
        cur = cur.parent
    return cur


def _declared_type(scope: Node, name: str) -> str | None:
    """The lowercased type *scope* declares *name* with, as a local, a
    parameter or (for a class) a field. ``None`` when nothing declares it."""
    stack = [scope]
    while stack:
        cur = stack.pop()
        if cur.type in ("local_variable_declaration", "field_declaration", "formal_parameter"):
            names = [cur.child_by_field_name("name")] + [
                d.child_by_field_name("name") for d in cur.children_by_field_name("declarator")
            ]
            if any(_lower(n) == name for n in names if n is not None):
                return "".join(_lower(cur.child_by_field_name("type")).split())
            continue
        # A nested class declares its own names.
        if cur is scope or cur.type != "class_declaration":
            stack.extend(cur.children)
    return None


def _is_http(receiver: Node) -> bool:
    """``new Http()``, or a name the enclosing method or class declares ``Http``."""
    if receiver.type == "object_creation_expression":
        return _lower(receiver.child_by_field_name("type")) in ("http", "system.http")
    if receiver.type != "identifier":
        return False
    name = _lower(receiver)
    for scope in (_ancestor(receiver, _FN_KINDS), _ancestor(receiver, _CLASS)):
        if scope is None:
            continue
        declared = _declared_type(scope, name)
        if declared is not None:
            return declared in ("http", "system.http")
    return False


def _is_future_call(call: Node) -> bool:
    """A call to a method of the enclosing class whose every overload is ``@future``."""
    receiver = call.child_by_field_name("object")
    cls = _ancestor(call, _CLASS)
    if cls is None:
        return False
    if receiver is not None and _lower(receiver) != _lower(cls.child_by_field_name("name")):
        return False
    name = _lower(call.child_by_field_name("name"))
    body = cls.child_by_field_name("body")
    found = False
    for member in body.named_children if body is not None else ():
        if member.type != "method_declaration" or _lower(member.child_by_field_name("name")) != name:
            continue
        mods = next((c for c in member.children if c.type == "modifiers"), None)
        annotations = [] if mods is None else [c for c in mods.children if c.type == "annotation"]
        if not any(_lower(a.child_by_field_name("name")) == "future" for a in annotations):
            return False
        found = True
    return found


class ApexPerfDialect(JavaPerfDialect):
    language = "apex"
    markers = frozenset({"io_in_loop", "nested_loop_with_io"})
    function_kinds = _FN_KINDS
    int_kinds = frozenset({"int"})

    # -- callee seam: inline SOQL/SOSL and DML have no callee ------------------

    def callee_method_name(self, call_node: Node) -> str | None:
        if call_node.type == "query_expression":
            body = call_node.named_children[0] if call_node.named_children else None
            return "SOSL" if body is not None and body.type == "sosl_query_body" else "SOQL"
        if call_node.type == "dml_expression":
            kind = next((c for c in call_node.children if c.type == "dml_type"), None)
            return _lower(kind) or None
        return super().callee_method_name(call_node)

    def callee_root_name(self, call_node: Node) -> str | None:
        if call_node.type in ("query_expression", "dml_expression"):
            return self.callee_method_name(call_node)
        return super().callee_root_name(call_node)

    def callee_is_attribute(self, call_node: Node) -> bool:
        if call_node.type in ("query_expression", "dml_expression"):
            return False
        return super().callee_is_attribute(call_node)

    # -- lexicon ----------------------------------------------------------------

    def call_sink_kind(
        self, call: Node, *, awaited: bool, io_names: dict[str, str], has_db_import: bool
    ) -> str | None:
        kind = _sink_kind(call)
        if kind is None or _runs_once(call):
            return None
        return kind

    def is_literal(self, node: Node) -> bool:
        # ``new List<String>{'a', 'b'}``: the entries sit in its initializer.
        init = next((c for c in node.children if c.type == "array_initializer"), None)
        return node.type == "array_creation_expression" and init is not None and bool(
            init.named_children
        )

    def is_constant_loop(self, node: Node) -> bool:
        """A loop whose body is no per-record work: a literal bound (``i < 6``, a
        literal list), a walk over chunks (``i += size``, a list of lists, or
        ``for (List<Account> chunk : [SELECT ...])``, the platform's 200-row
        batch idiom), a retry inside a ``catch`` after a bulk call failed, and
        ``while`` / ``do``, which walk a cursor or a chain. Ceiling: any loop
        inside a ``catch`` and any ``while`` (a SOQL per hierarchy level) go
        unreported."""
        if node.type in ("while_statement", "do_statement"):
            return True
        holder = _ancestor(node, _SCOPES | {"catch_clause"})
        if holder is not None and holder.type == "catch_clause":
            return True
        if node.type == "for_statement":
            update = "".join(_lower(node.child_by_field_name("update")).split())
            step = update.partition("+=")[2]
            if (step and step != "1") or _walks_chunks(node):
                return True
        elif node.type == "enhanced_for_statement":
            declared = "".join(_lower(node.child_by_field_name("type")).split())
            if declared.startswith(("list<", "set<")) or declared.endswith("[]"):
                return True
        else:
            return False
        source = self.loop_source(node, lambda _call: None)
        return source is not None and source.magnitude == "bounded"

    def is_iteration_loop(self, node: Node) -> bool:
        return node.type in ("for_statement", "enhanced_for_statement")

    def runs_once_in_loop(self, call: Node) -> bool:
        return _loop_ends_after(call)


def _walks_chunks(loop: Node) -> bool:
    """``for (i = 0; i < groups.size(); i++)`` where each entry of ``groups`` is a collection."""
    stop = upper_bound(loop.child_by_field_name("condition"))
    owner = stop.child_by_field_name("object") if stop is not None else None
    if stop is None or owner is None or _lower(stop.child_by_field_name("name")) != "size":
        return False
    for scope in (_ancestor(loop, _FN_KINDS), _ancestor(loop, _CLASS)):
        declared = _declared_type(scope, _lower(owner)) if scope is not None else None
        if declared is not None:
            return _holds_collections(declared)
    return False


def _holds_collections(declared: str) -> bool:
    """``list<list<x>>``, ``x[][]``, ``map<k,list<x>>``: a container of collections."""
    if declared.endswith("[][]"):
        return True
    head, _, inner = declared.partition("<")
    if head == "map":
        depth = 0
        for i, char in enumerate(inner):
            depth += char == "<"
            depth -= char == ">"
            if char == "," and depth == 0:
                inner = inner[i + 1 :]
                break
    elif head not in ("list", "set"):
        return False
    return inner.startswith(("list<", "set<", "map<")) or inner.removesuffix(">").endswith("[]")


def _sink_kind(call: Node) -> str | None:
    if call.type in ("query_expression", "dml_expression"):
        return "db"
    if call.type != "method_invocation":
        return None
    method = _lower(call.child_by_field_name("name"))
    receiver = call.child_by_field_name("object")
    if receiver is None:
        return "db" if _is_future_call(call) else None
    owner = _lower(receiver).removeprefix("system.")
    kind = _STATIC_SINKS.get(owner, {}).get(method)
    if kind is not None:
        return kind
    if method == "send" and _is_http(receiver):
        return "network"
    return "db" if _is_future_call(call) else None


# -- operations that run once, or are the bulk idiom, though written in a loop --

_DATABASE = ("database", "system.database")
_DYNAMIC_QUERIES = frozenset(
    {
        "query", "querywithbinds", "countquery", "countquerywithbinds",
        "getquerylocator", "getquerylocatorwithbinds",
    }
)
_LOOP_KINDS = frozenset(
    {"for_statement", "enhanced_for_statement", "while_statement", "do_statement"}
)
_SCOPES = _FN_KINDS | _CLASS
# ``' FROM ' + objName``: the object a dynamic query reads changes with a value.
_FROM_A_VALUE = re.compile(r"\bfrom\s*'\s*\+", re.IGNORECASE)
# Custom metadata type queries do not count against the SOQL limit.
_FROM_METADATA = re.compile(r"\bfrom\s+\w+__mdt\b", re.IGNORECASE)
_SPACES = re.compile(r"\s+")


def _runs_once(call: Node) -> bool:
    """*call* is no per-iteration governor cost: ``merge`` (three records at
    most), a custom metadata query, a dynamic query per object type, a lazily
    filled cache, a sink a Boolean parameter switches, DML of a per-group list,
    a buffer flushed at a size, or a loop that ends after it. Ceiling: a
    per-record query built with ``' FROM ' +`` is not reported."""
    if _is_merge(call):
        return True
    text = _query_text(call)
    if text and (_FROM_METADATA.search(text) or _FROM_A_VALUE.search(text)):
        return True
    if _cached(call) or _parameter_switched(call):
        return True
    target = _dml_target(call)
    if target is not None and (_per_group_list(target) or _flushed_buffer(call, target)):
        return True
    return _loop_ends_after(call)


def _database_method(call: Node) -> str | None:
    if call.type != "method_invocation":
        return None
    if _lower(call.child_by_field_name("object")) not in _DATABASE:
        return None
    return _lower(call.child_by_field_name("name"))


def _is_merge(call: Node) -> bool:
    if call.type == "dml_expression":
        kind = next((c for c in call.children if c.type == "dml_type"), None)
        return _lower(kind) == "merge"
    return _database_method(call) == "merge"


def _first_argument(call: Node) -> Node | None:
    args = call.child_by_field_name("arguments")
    return next(iter(args.named_children), None) if args is not None else None


def _query_text(call: Node) -> str:
    """The SOQL a query runs: its own text, or for a dynamic query its string
    argument plus every assignment the method makes to that name."""
    if call.type == "query_expression":
        return _lower(call)
    if _database_method(call) not in _DYNAMIC_QUERIES:
        return ""
    arg = _first_argument(call)
    if arg is None or arg.type != "identifier":
        return _lower(arg)
    name = _lower(arg)
    scope = _ancestor(call, _FN_KINDS)
    parts: list[str] = []
    stack = [scope] if scope is not None else []
    while stack:
        cur = stack.pop()
        if cur.type == "variable_declarator" and _lower(cur.child_by_field_name("name")) == name:
            parts.append(_lower(cur.child_by_field_name("value")))
        elif cur.type == "assignment_expression" and _lower(cur.child_by_field_name("left")) == name:
            parts.append(_lower(cur.child_by_field_name("right")))
        stack.extend(cur.children)
    return "\n".join(parts)


def _dml_target(call: Node) -> Node | None:
    if call.type == "dml_expression":
        return call.child_by_field_name("target")
    method = _database_method(call)
    if method is None or method in _DYNAMIC_QUERIES:
        return None
    return _first_argument(call)


def _normalised(node: Node | None) -> str:
    return _SPACES.sub("", _lower(node)).removeprefix("this.")


def _fills(node: Node, name: str) -> bool:
    """*node*'s subtree stores into *name*: ``x = ...`` or ``x.put(...)``."""
    stack = [node]
    while stack:
        cur = stack.pop()
        if cur.type == "assignment_expression" and _normalised(cur.child_by_field_name("left")) == name:
            return True
        if (
            cur.type == "method_invocation"
            and _normalised(cur.child_by_field_name("object")) == name
            and _lower(cur.child_by_field_name("name")) in ("put", "putall")
        ):
            return True
        stack.extend(cur.children)
    return False


# ``x == null`` / ``x.f == null`` / ``x.get(k) == null`` / ``!x.containsKey(k)``, and the opposites.
_MISSING = re.compile(r"^(?:([\w.]+?)(?:\.get\(.*\))?==null|null==([\w.]+?)(?:\.get\(.*\))?|!([\w.]+?)\.containskey\(.*\))$")
_PRESENT = re.compile(r"^(?:([\w.]+?)(?:\.get\(.*\))?!=null|null!=([\w.]+?)(?:\.get\(.*\))?|([\w.]+?)\.containskey\(.*\))$")


def _cache_name(condition: Node | None, test: re.Pattern[str]) -> str | None:
    found = test.match(_normalised(condition).removeprefix("(").removesuffix(")").removeprefix("this."))
    return next((g for g in found.groups() if g), None) if found else None


def _sole_statement(branch: Node | None) -> Node | None:
    """The one statement of an ``if`` branch, braced or not; ``None`` otherwise."""
    if branch is None or branch.type != "block":
        return branch
    inner = branch.named_children
    return inner[0] if len(inner) == 1 else None


def _fills_when_missing(branch: Node, call: Node) -> bool:
    """*branch* is ``if (x == null)`` whose consequence holds *call* and fills ``x``."""
    name = _cache_name(branch.child_by_field_name("condition"), _MISSING)
    consequence = branch.child_by_field_name("consequence")
    return bool(
        name
        and consequence is not None
        and consequence.start_byte <= call.start_byte < consequence.end_byte
        and _fills(consequence, name)
    )


def _cached(call: Node) -> bool:
    """A lazily filled cache: *call* sits in ``if (x == null)`` (or ``x.get(k)``,
    ``!x.containsKey(k)``) whose branch fills ``x``, or its method opens with
    ``if (x != null) return ...;`` and fills ``x`` below."""
    fn = _ancestor(call, _FN_KINDS)
    cur = call.parent
    while cur is not None and cur is not fn:
        if cur.type == "if_statement" and _fills_when_missing(cur, call):
            return True
        cur = cur.parent
    body = fn.child_by_field_name("body") if fn is not None else None
    first = next(iter(body.named_children), None) if body is not None else None
    if first is None or first.type != "if_statement":
        return False
    name = _cache_name(first.child_by_field_name("condition"), _PRESENT)
    returns = _sole_statement(first.child_by_field_name("consequence"))
    return bool(name) and returns is not None and returns.type == "return_statement" and _fills(body, name)


def _parameter_switched(call: Node) -> bool:
    """Outside any loop, under ``if (flag)`` on a Boolean parameter of its method
    (``if (doInsert) insert rows;``): whether it runs is the caller's argument,
    which reachability does not read. Ceiling: a caller passing ``true`` in a
    loop is not reported."""
    fn = _ancestor(call, _FN_KINDS)
    params = fn.child_by_field_name("parameters") if fn is not None else None
    if params is None:
        return False
    flags = {
        _lower(p.child_by_field_name("name"))
        for p in params.named_children
        if _lower(p.child_by_field_name("type")) == "boolean"
    }
    cur = call.parent
    switched = False
    while cur is not None and cur is not fn:
        if cur.type in _LOOP_KINDS:
            return False
        if cur.type == "if_statement":
            condition = _normalised(cur.child_by_field_name("condition")).strip("()")
            switched = switched or condition.removesuffix("==true") in flags
        cur = cur.parent
    return switched


def _per_group_list(target: Node) -> bool:
    """``byType.get(t)`` where ``byType`` is declared a map whose values are lists."""
    if target.type != "method_invocation" or _lower(target.child_by_field_name("name")) != "get":
        return False
    owner = target.child_by_field_name("object")
    if owner is None or owner.type != "identifier":
        return False
    for scope in (_ancestor(target, _FN_KINDS), _ancestor(target, _CLASS)):
        declared = _declared_type(scope, _lower(owner)) if scope is not None else None
        if declared is not None:
            return declared.startswith("map<") and _holds_collections(declared)
    return False


def _flushed_buffer(call: Node, target: Node) -> bool:
    """DML on ``buf`` under a branch that tests ``buf.size()`` against a batch
    size (``== 100``, ``>= BATCH``); an emptiness test (``> 0``) is no flush."""
    if target.type != "identifier":
        return False
    threshold = re.compile(re.escape(_lower(target)) + r"\.size\(\)(?:==|>=|>)(\d+|[a-z_][\w.]*)")
    cur = call.parent
    while cur is not None and cur.type not in _SCOPES and cur.type not in _LOOP_KINDS:
        if cur.type == "if_statement":
            condition = _normalised(cur.child_by_field_name("condition")).removeprefix("(").removesuffix(")")
            found = threshold.fullmatch(condition)
            if found and not (found[1].isdigit() and int(found[1]) < 2):
                return True
        cur = cur.parent
    return False


def _loop_ends_after(call: Node) -> bool:
    """The loop ends right after *call*: a ``return`` or ``break`` follows it in
    its own block, or the next exit tests a flag the ``try`` around *call* sets
    (retry until one attempt succeeds). A ``break`` counts only when one loop
    surrounds *call*. An exit on any other condition keeps the hit."""
    loops = 0
    cur = call.parent
    while cur is not None and cur.type not in _SCOPES:
        loops += cur.type in _LOOP_KINDS
        cur = cur.parent
    if not loops:
        return False
    exits = ("return_statement", "break_statement") if loops == 1 else ("return_statement",)
    statement = call
    while statement.parent is not None and statement.parent.type != "block":
        statement = statement.parent
    return any(s.type in exits for s in _later_siblings(statement)) or _retry_exit(call, exits)


def _retry_exit(call: Node, exits: tuple[str, ...]) -> bool:
    """``try { <call> } catch (...) { ok = false; }  if (ok) break;``."""
    attempt = _ancestor(call, _SCOPES | _LOOP_KINDS | {"try_statement"})
    if attempt is None or attempt.type != "try_statement":
        return False
    later = next((n for n in _later_siblings(attempt) if n.type == "if_statement"), None)
    leave = _sole_statement(later.child_by_field_name("consequence")) if later is not None else None
    if leave is None or leave.type not in exits:
        return False
    flags = {
        _normalised(n.child_by_field_name("left"))
        for n in ApexPerfDialect._walk(attempt)
        if n.type == "assignment_expression"
    }
    return _normalised(later.child_by_field_name("condition")).strip("()!") in flags


def _later_siblings(node: Node) -> list[Node]:
    out: list[Node] = []
    nxt = node.next_named_sibling
    while nxt is not None:
        out.append(nxt)
        nxt = nxt.next_named_sibling
    return out


DIALECT = ApexPerfDialect()
