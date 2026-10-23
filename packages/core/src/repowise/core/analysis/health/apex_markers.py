"""Apex security and test-hygiene markers, read off the Apex tree.

Four advisory markers, all precision-first (each stays silent when the tree does
not prove its claim):

- ``apex_soql_injection``: a dynamic query (``Database.query`` and friends,
  ``Search.query``) whose string splices a value from outside the method (a
  String parameter, a public String field or property, or a local built from one) inside
  a quoted SOQL literal, with no escape call and no bind. Values outside quotes
  (field lists, object names) are not flagged: escaping cannot protect them and
  they are almost always describe results.
- ``apex_missing_sharing``: a top-level class that runs SOQL, SOSL or DML in its
  own body and declares no sharing keyword. A subclass inherits its parent's
  setting and an inner class's queries are its own, so both are left out.
- ``apex_hardcoded_id``: a string literal shaped like a record Id (an 18-char
  Id with a valid checksum, or a 15-char Id with a standard key prefix), not a
  zero-filled or ``xx`` sample Id, outside fixture-named classes.
- ``apex_see_all_data``: ``@IsTest(SeeAllData=true)``.

Test classes and methods are skipped by the first three, and a
``@SuppressWarnings('PMD.<Rule>')`` naming the matching PMD rule (or ``'PMD'``) on
the class or method silences each. Ceiling: any String parameter counts as from
outside, so a private helper only ever called with constants still fires.
Hits ride ``FileComplexity.perf_hits`` like the SQL smells.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import TYPE_CHECKING

from .complexity.models import PerfHit

if TYPE_CHECKING:
    from tree_sitter import Node

SOQL_INJECTION = "apex_soql_injection"
MISSING_SHARING = "apex_missing_sharing"
HARDCODED_ID = "apex_hardcoded_id"
SEE_ALL_DATA = "apex_see_all_data"

_PMD_RULE = {
    SOQL_INJECTION: "pmd.apexsoqlinjection",
    MISSING_SHARING: "pmd.apexsharingviolations",
    HARDCODED_ID: "pmd.avoidhardcodingid",
    SEE_ALL_DATA: "pmd.apexunittestshouldnotuseseealldatatrue",
}

_DYNAMIC_QUERY = {
    "database": frozenset(
        {
            "query", "countquery", "getquerylocator",
            "querywithbinds", "countquerywithbinds", "getquerylocatorwithbinds",
        }
    ),
    "search": frozenset({"query"}),
}
# Calls whose records sharing can hide: reads and changes to existing rows. An
# insert creates a row no sharing rule hides, so it does not count.
_SHARED_OPS = {
    "database": _DYNAMIC_QUERY["database"] | {"update", "upsert", "delete", "undelete", "merge"},
    "search": frozenset({"query", "find"}),
}
_SHARING = frozenset({"with_sharing", "without_sharing", "inherited_sharing"})
# Objects whose rows carry no sharing, so a query on them reads the same either way.
_UNSHARED_OBJECTS = frozenset(
    {
        "recordtype", "profile", "organization", "apexclass", "apextrigger", "asyncapexjob",
        "crontrigger", "cronjobdetail", "permissionset", "userrole", "businesshours",
        "staticresource", "entitydefinition", "fielddefinition", "userlicense",
        "flowdefinitionview", "flowversionview", "opportunitystage", "casestatus",
        "leadstatus", "taskstatus", "taskpriority", "contractstatus", "currencytype",
        "datedconversionrate", "period", "fiscalyearsettings", "group", "groupmember",
        "folder", "network", "site", "holiday", "permissionsetassignment",
    }
)
_METHODS = frozenset({"method_declaration", "constructor_declaration"})
_TYPES = frozenset({"class_declaration", "interface_declaration", "enum_declaration"})
_STRING_COLLECTIONS = frozenset({"list<string>", "set<string>", "string[]"})
# String methods whose result still carries the receiver's characters (``replace``
# and ``remove`` are left out: they are how code strips quotes).
_PASS_THROUGH = frozenset(
    {
        "trim", "tolowercase", "touppercase", "normalizespace", "deletewhitespace",
        "substring", "left", "right", "mid", "capitalize", "abbreviate", "get",
    }
)

# Standard key prefixes a 15-char literal must start with (an 18-char Id proves
# itself by its checksum instead).
_KEY_PREFIXES = frozenset(
    {
        "001", "003", "005", "006", "00Q", "500", "701", "00G", "012", "00e", "0PS",
        "00D", "00T", "00U", "015", "068", "069", "00O", "01Z", "00X", "00E", "01t",
        "01s", "01u", "00k", "00v", "02i", "800", "801", "0Q0", "00N", "01I", "00l",
    }
)
# Test-support classes not marked @IsTest (fixtures, mocks) carry sample Ids by design.
# Ceiling: named by convention only; a class reached only from tests is not detected.
_FIXTURE_NAME = re.compile(r"fixture|mock|stub|fake|testdata|testfactory|testutil|datafactory", re.I)
_ID_SHAPE = re.compile(r"[a-zA-Z0-9]{15}(?:[a-zA-Z0-9]{3})?")
_CHECKSUM_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", "replace") if node is not None else ""


def _lower(node: Node | None) -> str:
    return _text(node).lower()


def _walk(node: Node, skip: frozenset[str] = frozenset()) -> Iterator[Node]:
    """Every descendant of *node*, not entering a child whose type is in *skip*."""
    stack = list(reversed(node.children))
    while stack:
        cur = stack.pop()
        yield cur
        if cur.type not in skip:
            stack.extend(reversed(cur.children))


def _ancestors(node: Node) -> Iterator[Node]:
    cur = node.parent
    while cur is not None:
        yield cur
        cur = cur.parent


def _modifiers(decl: Node) -> Node | None:
    return next((c for c in decl.children if c.type == "modifiers"), None)


def _annotations(decl: Node) -> list[Node]:
    mods = _modifiers(decl)
    return [] if mods is None else [c for c in mods.children if c.type == "annotation"]


def _modifier_kinds(decl: Node) -> set[str]:
    mods = _modifiers(decl)
    if mods is None:
        return set()
    return {g.type.lower() for c in mods.children if c.type == "modifier" for g in c.children}


def _is_test_decl(decl: Node) -> bool:
    return "testmethod" in _modifier_kinds(decl) or any(
        _lower(a.child_by_field_name("name")) == "istest" for a in _annotations(decl)
    )


def _in_test(node: Node) -> bool:
    return any(a.type in _METHODS | _TYPES and _is_test_decl(a) for a in _ancestors(node))


def _suppressed(node: Node, kind: str) -> bool:
    rule = _PMD_RULE[kind]
    for decl in (a for a in (node, *_ancestors(node)) if a.type in _METHODS | _TYPES):
        for ann in _annotations(decl):
            if _lower(ann.child_by_field_name("name")) != "suppresswarnings":
                continue
            args = ann.child_by_field_name("arguments")
            values = [n for n in _walk(args) if n.type == "string_literal"] if args is not None else []
            names = {n.strip() for v in values for n in _literal(v).lower().split(",")}
            if "pmd" in names or rule in names:
                return True
    return False


def _decl_name(node: Node) -> str | None:
    for a in _ancestors(node):
        if a.type in _METHODS | _TYPES:
            return _text(a.child_by_field_name("name")) or None
    return None


def _decl_start(node: Node) -> int:
    for a in _ancestors(node):
        if a.type in _METHODS:
            return a.start_point[0] + 1
    return 0


def _hit(kind: str, node: Node, detail: str, at: Node | None = None) -> PerfHit:
    return PerfHit(
        kind=kind,
        line=(at or node).start_point[0] + 1,
        function=_decl_name(node),
        detail=detail,
        func_start=_decl_start(node),
    )


def _literal(node: Node) -> str:
    """A string literal's value, Apex escapes decoded (``\\'`` is ``'``)."""
    raw = _text(node)[1:-1]
    return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), raw)


def _soql_quotes(value: str) -> int:
    """Quotes that open or close a SOQL string (a ``\\'`` inside one is escaped)."""
    return len(re.findall(r"(?<!\\)'", value))


# -- apex_see_all_data ----------------------------------------------------------


def _see_all_data(root: Node) -> list[PerfHit]:
    hits = []
    for ann in _walk(root):
        if ann.type != "annotation" or _lower(ann.child_by_field_name("name")) != "istest":
            continue
        args = ann.child_by_field_name("arguments")
        for kv in args.named_children if args is not None else ():
            if (
                kv.type == "annotation_key_value"
                and _lower(kv.child_by_field_name("key")) == "seealldata"
                and _lower(kv.child_by_field_name("value")) == "true"
                and not _suppressed(ann, SEE_ALL_DATA)
            ):
                owner = ann.parent.parent if ann.parent is not None else None
                what = "class" if owner is not None and owner.type in _TYPES else "method"
                hits.append(_hit(SEE_ALL_DATA, ann.parent or ann, what, at=ann))
    return hits


# -- apex_hardcoded_id ----------------------------------------------------------


def _checksum(value: str) -> str:
    out = ""
    for chunk in range(3):
        bits = sum(1 << i for i, c in enumerate(value[chunk * 5 : chunk * 5 + 5]) if c.isupper())
        out += _CHECKSUM_CHARS[bits]
    return out


def is_record_id(value: str) -> bool:
    """*value* reads as a Salesforce record Id: 18 chars with a valid checksum, or
    15 chars on a standard key prefix, mixing digits and letters either way."""
    body = value[3:15]
    if not _ID_SHAPE.fullmatch(value) or sum(c.isdigit() for c in value[:15]) < 3:
        return False
    # No letters is a zero-filled stand-in; ``xx`` marks Salesforce's sample Ids (``001xx000003DGb2``).
    if not any(c.isalpha() for c in body) or "xx" in body:
        return False
    if len(value) == 18:
        return _checksum(value[:15]) == value[15:]
    # A 15-char Id has no checksum: also ask for the zero run its pod and reserved digits leave.
    return value[:3] in _KEY_PREFIXES and "000" in body


def _hardcoded_ids(root: Node) -> list[PerfHit]:
    hits: list[PerfHit] = []
    if any(c.type in _TYPES and _FIXTURE_NAME.search(_text(c.child_by_field_name("name"))) for c in root.named_children):
        return hits
    for node in _walk(root):
        if node.type != "string_literal":
            continue
        value = _literal(node)
        if is_record_id(value) and not _in_test(node) and not _suppressed(node, HARDCODED_ID):
            hits.append(_hit(HARDCODED_ID, node, value))
    return hits


# -- apex_missing_sharing -------------------------------------------------------


def _static_call(node: Node, table: dict[str, frozenset[str]]) -> bool:
    if node.type != "method_invocation":
        return False
    receiver = _lower(node.child_by_field_name("object")).replace(" ", "")
    receiver = receiver.removeprefix("system.")
    return _lower(node.child_by_field_name("name")) in table.get(receiver, frozenset())


def _unshared(obj: str) -> bool:
    """*obj* (lowercased) keeps no record sharing: custom metadata, a custom
    setting (by its usual ``...Settings__c`` name) or a setup object."""
    obj = obj.rsplit(".", 1)[-1]
    return obj.endswith("__mdt") or "setting" in obj or obj in _UNSHARED_OBJECTS


def _declared_type(ident: Node) -> str:
    """The lowercased element type a name is declared with in its method or class."""
    name = _lower(ident)
    for scope in (a for a in _ancestors(ident) if a.type in _METHODS or a.type == "class_declaration"):
        typ = _local_type(scope, name) if scope.type in _METHODS else _Scope(scope).fields.get(name, ("",))[0]
        if typ:
            return re.sub(r"^(?:list|set)<(.*)>$|\[\]$", r"\1", typ)
    return ""


def _query_objects(node: Node) -> list[str]:
    """The objects a query may read: the FROM of inline SOQL, or of a dynamic
    query built from literals (directly or through a local). ``""`` is unknown."""
    if node.type == "query_expression":
        body = node.named_children[0] if node.named_children else None
        source = body.child_by_field_name("from_clause") if body is not None else None
        return [_lower(source.named_children[0]) if source is not None and source.named_children else ""]
    args = node.child_by_field_name("arguments")
    values = list(args.named_children[:1]) if args is not None else []
    if values and values[0].type == "identifier":
        method = next((a for a in _ancestors(values[0]) if a.type in _METHODS), None)
        values = _assigned(method, _lower(values[0])) if method is not None else []
    objects = []
    for value in values:
        text = "".join(_literal(p) if p.type == "string_literal" else " ? " for p in _operands(value))
        # Child subqueries name relationships, not the object read.
        found = re.search(r"\bfrom\s+(\w+)", re.sub(r"\([^()]*\)", " ", text), re.I)
        if found:
            objects.append(found.group(1).lower())
    # A ``+=`` appending a filter names no object; no FROM anywhere is unknown.
    return objects or [""]


def _shared_op(node: Node) -> bool:
    """*node* reads or changes records that record sharing applies to."""
    if node.type == "dml_expression":
        kind = next((c for c in node.children if c.type == "dml_type"), None)
        target = node.child_by_field_name("target")
        if _lower(kind) == "insert" or target is None:
            return False
        typ = _declared_type(target) if target.type == "identifier" else _lower(target)
        return not _unshared(typ)
    if node.type == "query_expression" or _static_call(node, _DYNAMIC_QUERY):
        return not all(obj and _unshared(obj) for obj in _query_objects(node))
    if not _static_call(node, _SHARED_OPS):
        return False
    args = node.child_by_field_name("arguments")
    first = args.named_children[0] if args is not None and args.named_children else None
    return first is None or first.type != "identifier" or not _unshared(_declared_type(first))


def _first_db_op(cls: Node) -> Node | None:
    body = cls.child_by_field_name("body")
    if body is None:
        return None
    return next((n for n in _walk(body, skip=_TYPES) if _shared_op(n)), None)


def _has_test_method(cls: Node) -> bool:
    """A legacy test class: ``testMethod`` members and no ``@IsTest`` on the class."""
    body = cls.child_by_field_name("body")
    return any(m.type in _METHODS and _is_test_decl(m) for m in (body.named_children if body else ()))


def _missing_sharing(root: Node) -> list[PerfHit]:
    hits = []
    for cls in root.named_children:
        if (
            cls.type != "class_declaration"
            or _modifier_kinds(cls) & _SHARING
            or cls.child_by_field_name("superclass") is not None
            or _is_test_decl(cls)
            or _has_test_method(cls)
            or _suppressed(cls, MISSING_SHARING)
        ):
            continue
        op = _first_db_op(cls)
        if op is not None:
            name = cls.child_by_field_name("name") or cls
            hits.append(
                PerfHit(
                    kind=MISSING_SHARING,
                    line=name.start_point[0] + 1,
                    function=_text(cls.child_by_field_name("name")) or None,
                    detail=f"line {op.start_point[0] + 1}",
                )
            )
    return hits


# -- apex_soql_injection --------------------------------------------------------


class _Scope:
    """Declarations and assignments of one class, for tracing a query string."""

    def __init__(self, cls: Node) -> None:
        self.cls = cls
        self.fields: dict[str, tuple[str, bool]] = {}  # name -> (type, externally settable)
        self.assigns: dict[str, list[tuple[Node, Node | None]]] = {}  # name -> [(rhs, method)]
        body = cls.child_by_field_name("body")
        for member in body.named_children if body is not None else ():
            if member.type != "field_declaration":
                continue
            kinds = _modifier_kinds(member)
            settable = bool(kinds & {"public", "global"}) and "final" not in kinds
            typ = "".join(_lower(member.child_by_field_name("type")).split())
            for decl in member.children_by_field_name("declarator"):
                self.fields[_lower(decl.child_by_field_name("name"))] = (typ, settable)

    def assignments(self, name: str, method: Node | None) -> list[tuple[Node, Node | None]]:
        """``(rhs, method)`` for every value *name* takes: in *method* when it is a
        local or parameter there, else across the class when it is a field."""
        if method is not None and _local_type(method, name) is not None:
            return [(rhs, method) for rhs in _assigned(method, name)]
        if name not in self.fields:
            return []
        if name not in self.assigns:
            found: list[tuple[Node, Node | None]] = []
            body = self.cls.child_by_field_name("body")
            for member in body.named_children if body is not None else ():
                if member.type in _METHODS and _local_type(member, name) is None:
                    found += [(rhs, member) for rhs in _assigned(member, name)]
            self.assigns[name] = found
        return self.assigns[name]


def _local_type(method: Node, name: str) -> str | None:
    """The lowercased type *method* declares *name* with (parameter or local)."""
    for node in _walk(method, skip=_TYPES):
        if node.type == "formal_parameter" and _lower(node.child_by_field_name("name")) == name:
            return "".join(_lower(node.child_by_field_name("type")).split())
        if node.type == "local_variable_declaration":
            for decl in node.children_by_field_name("declarator"):
                if _lower(decl.child_by_field_name("name")) == name:
                    return "".join(_lower(node.child_by_field_name("type")).split())
    return None


def _is_param(method: Node, name: str) -> bool:
    params = method.child_by_field_name("parameters")
    return any(
        p.type == "formal_parameter" and _lower(p.child_by_field_name("name")) == name
        for p in (params.named_children if params is not None else ())
    )


def _assigned(method: Node, name: str) -> list[Node]:
    """Every expression *method* stores in *name* (initializer, ``=``, ``+=``)."""
    out = []
    for node in _walk(method, skip=_TYPES):
        if node.type == "variable_declarator" and _lower(node.child_by_field_name("name")) == name:
            value = node.child_by_field_name("value")
            if value is not None:
                out.append(value)
        elif node.type == "assignment_expression" and _target(node.child_by_field_name("left")) == name:
            out.append(node.child_by_field_name("right"))
    return [n for n in out if n is not None]


def _target(node: Node | None) -> str | None:
    if node is None:
        return None
    if node.type == "identifier":
        return _lower(node)
    if node.type == "field_access" and _lower(node.child_by_field_name("object")) == "this":
        return _lower(node.child_by_field_name("field"))
    return None


def _is_escape(node: Node) -> bool:
    return node.type == "method_invocation" and "escape" in _lower(node.child_by_field_name("name"))


def _operands(expr: Node) -> list[Node]:
    """*expr* as the pieces a ``+`` chain concatenates, parentheses dropped."""
    out, stack = [], [expr]
    while stack:  # iterative: generated code chains thousands of ``+``
        cur = stack.pop()
        if cur.type == "parenthesized_expression" and cur.named_children:
            stack.append(cur.named_children[0])
        elif cur.type == "binary_expression" and _text(cur.child_by_field_name("operator")) == "+":
            stack += [c for c in (cur.child_by_field_name("right"), cur.child_by_field_name("left")) if c]
        else:
            out.append(cur)
    return out


class _Tracer:
    def __init__(self, scope: _Scope) -> None:
        self.scope = scope
        self.seen: set[tuple[int, str]] = set()

    # A value from outside the method --------------------------------------

    def _type_of(self, name: str, method: Node | None) -> tuple[str | None, bool]:
        """``(type, is a source)`` of *name* seen from *method*."""
        if method is not None:
            local = _local_type(method, name)
            if local is not None:
                return local, _is_param(method, name)
        typ, settable = self.scope.fields.get(name, (None, False))
        return typ, settable

    def _sanitized(self, name: str, method: Node | None) -> bool:
        return any(
            _is_escape(rhs) for rhs, _m in self.scope.assignments(name, method)
        )

    def tainted_name(self, node: Node, method: Node | None, depth: int = 0) -> str | None:
        """The outside String *node* carries unescaped, or ``None``."""
        if depth > 4:
            return None
        if node.type == "parenthesized_expression" and node.named_children:
            return self.tainted_name(node.named_children[0], method, depth)
        if node.type == "ternary_expression":
            for branch in ("consequence", "alternative"):
                child = node.child_by_field_name(branch)
                found = self.tainted_name(child, method, depth + 1) if child else None
                if found:
                    return found
            return None
        if node.type == "array_access":
            return self._collection(node.child_by_field_name("array"), method)
        if node.type == "method_invocation":
            return self._call(node, method, depth)
        name = _target(node)
        if name is None:
            return None
        typ, source = self._type_of(name, method)
        if typ != "string" or self._sanitized(name, method):
            return None
        if source:
            return _text(node)
        key = (method.id if method is not None else 0, name)
        if key in self.seen:
            return None
        self.seen.add(key)
        for rhs, where in self.scope.assignments(name, method):
            for piece in _operands(rhs):
                found = self.tainted_name(piece, where, depth + 1)
                if found:
                    return found
        return None

    def _collection(self, node: Node | None, method: Node | None) -> str | None:
        name = _target(node)
        if name is None:
            return None
        typ, source = self._type_of(name, method)
        return _text(node) if source and typ in _STRING_COLLECTIONS else None

    def _call(self, call: Node, method: Node | None, depth: int) -> str | None:
        if _is_escape(call):
            return None
        receiver = call.child_by_field_name("object")
        name = _lower(call.child_by_field_name("name"))
        args = call.child_by_field_name("arguments")
        first = args.named_children[0] if args is not None and args.named_children else None
        if _lower(receiver) == "string" and name == "join" and first is not None:
            return self._collection(first, method)
        if receiver is None or name not in _PASS_THROUGH:
            return None
        if name == "get":
            return self._collection(receiver, method)
        return self.tainted_name(receiver, method, depth + 1)

    # Where the query string splices one in ---------------------------------

    def quoted_splice(self, expr: Node, method: Node | None, depth: int = 0) -> str | None:
        """The outside value *expr* splices inside a quoted SOQL literal, tracing
        the String locals and fields it is built from."""
        if depth > 4:
            return None
        quoted = False
        for piece in _operands(expr):
            if piece.type == "string_literal":
                quoted ^= _soql_quotes(_literal(piece)) % 2 == 1
                continue
            if quoted:
                found = self.tainted_name(piece, method)
                if found:
                    return found
            found = self._built_from(piece, method, depth)
            if found:
                return found
        return None

    def _built_from(self, piece: Node, method: Node | None, depth: int) -> str | None:
        if piece.type == "ternary_expression":
            for branch in ("consequence", "alternative"):
                child = piece.child_by_field_name(branch)
                found = self.quoted_splice(child, method, depth + 1) if child else None
                if found:
                    return found
            return None
        if piece.type == "method_invocation":
            return self._format(piece, method)
        name = _target(piece)
        if name is None or self._type_of(name, method)[0] != "string":
            return None
        key = (-1 - (method.id if method is not None else 0), name)
        if key in self.seen:
            return None
        self.seen.add(key)
        for rhs, where in self.scope.assignments(name, method):
            found = self.quoted_splice(rhs, where, depth + 1)
            if found:
                return found
        return None

    def _format(self, call: Node, method: Node | None) -> str | None:
        """``String.format('... \\'{0}\\'', new List<Object>{x})`` with *x* outside."""
        if _lower(call.child_by_field_name("object")) != "string":
            return None
        if _lower(call.child_by_field_name("name")) != "format":
            return None
        args = call.child_by_field_name("arguments")
        named = args.named_children if args is not None else []
        if len(named) < 2 or named[0].type != "string_literal":
            return None
        init = next((c for c in named[1].children if c.type == "array_initializer"), None)
        values = init.named_children if init is not None else []
        for index, quoted in _format_slots(_literal(named[0])):
            found = self.tainted_name(values[index], method) if quoted and index < len(values) else None
            if found:
                return found
        return None


def _format_slots(template: str) -> list[tuple[int, bool]]:
    """``(argument index, inside a SOQL quote)`` for each placeholder of a
    ``String.format`` template. Apex formats like Java's MessageFormat: ``''`` is
    one quote, and a lone ``'`` quotes the text after it, placeholders included."""
    slots, rendered, literal, i = [], 0, False, 0
    while i < len(template):
        if template.startswith("''", i):
            rendered += 1
            i += 2
        elif template[i] == "'":
            literal = not literal
            i += 1
        elif not literal and (m := re.match(r"\{(\d+)\}", template[i:])):
            slots.append((int(m.group(1)), rendered % 2 == 1))
            i += m.end()
        else:
            i += 1
    return slots


def _soql_injections(root: Node) -> list[PerfHit]:
    hits = []
    scopes: dict[int, _Scope] = {}
    for call in _walk(root):
        if not _static_call(call, _DYNAMIC_QUERY):
            continue
        args = call.child_by_field_name("arguments")
        if args is None or not args.named_children or _in_test(call):
            continue
        cls = next((a for a in _ancestors(call) if a.type == "class_declaration"), None)
        method = next((a for a in _ancestors(call) if a.type in _METHODS), None)
        if cls is None or _suppressed(call, SOQL_INJECTION):
            continue
        scope = scopes.setdefault(cls.id, _Scope(cls))
        found = _Tracer(scope).quoted_splice(args.named_children[0], method)
        if found:
            hits.append(_hit(SOQL_INJECTION, call, found))
    return hits


def apex_marker_hits(root: Node) -> list[PerfHit]:
    """Every Apex marker hit in the file whose tree is *root*."""
    return [
        *_soql_injections(root),
        *_missing_sharing(root),
        *_hardcoded_ids(root),
        *_see_all_data(root),
    ]
