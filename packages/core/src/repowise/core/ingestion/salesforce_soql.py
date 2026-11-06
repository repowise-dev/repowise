"""SObject and field names written in Apex SOQL/SOSL, DML and triggers.

Read: SOQL ``FROM`` objects and field paths (a select-list subquery names a
child relationship, so it is skipped), SOSL ``RETURNING`` objects, DML targets
by declared type, ``trigger ... on Obj``, single-literal ``Database.query`` /
``Search.query`` strings and ``.soql`` files.

Ceiling: ``Account.Name.getDescribe()`` tokens, ``TYPEOF`` branches and the
child object behind a relationship name are not read.
"""

from __future__ import annotations

from collections.abc import Callable
from functools import lru_cache

from tree_sitter import Language, Node, Parser

from .models import FileInfo, ParsedFile, SchemaReference, Symbol
from .parser_helpers import _find_enclosing_symbol

_QUERY_METHODS = frozenset(
    {
        "query",
        "querywithbinds",
        "getquerylocator",
        "getquerylocatorwithbinds",
        "countquery",
        "countquerywithbinds",
    }
)
_DML_METHODS = frozenset({"insert", "update", "upsert", "delete", "undelete", "merge"})
_COLLECTIONS = frozenset({"list", "set"})
_BODIES = frozenset({"soql_query_body", "sosl_query_body"})
_SCOPES = frozenset({"method_declaration", "constructor_declaration", "trigger_declaration"})
_DECLARATIONS = frozenset(
    {"local_variable_declaration", "formal_parameter", "enhanced_for_statement"}
)

# (node to report, object name, field path)
_Emit = Callable[[Node, str, tuple[str, ...]], None]


def _text(node: Node | None) -> str:
    return node.text.decode("utf-8", errors="replace") if node is not None and node.text else ""


@lru_cache(maxsize=2)
def _language(grammar: str) -> Language | None:
    try:
        from tree_sitter_language_pack import get_language

        return get_language(grammar)  # type: ignore[arg-type]
    except Exception:
        return None


def _parser(grammar: str) -> Parser | None:
    """A fresh parser per use: a ``Parser`` is not safe to share across threads."""
    language = _language(grammar)
    return Parser(language) if language is not None else None


# ---------------------------------------------------------------------------
# Query bodies (shared by the Apex, SOQL and SOSL grammars)
# ---------------------------------------------------------------------------


def _field_path(node: Node, alias: str | None) -> tuple[str, ...]:
    parts = [_text(c) for c in node.named_children if c.type == "identifier"]
    if not parts:
        parts = [
            _text(c)
            for d in node.named_children
            if d.type == "dotted_identifier"
            for c in d.named_children
        ]
    if alias and len(parts) > 1 and parts[0].lower() == alias:
        parts = parts[1:]
    return tuple(parts)


def _soql(body: Node, emit: _Emit) -> None:
    obj: str | None = None
    alias: str | None = None
    from_clause = body.child_by_field_name("from_clause")
    if from_clause is not None:
        storage = next(
            (
                n
                for c in from_clause.named_children
                for n in ([c] if c.type == "storage_identifier" else c.named_children)
                if n.type == "storage_identifier"
            ),
            None,
        )
        name = _text(storage)
        if storage is not None and name and "." not in name:
            obj = name
            emit(storage, obj, ())
            holder = storage.parent
            if holder is not None and holder.type == "storage_alias":
                alias_node = next(
                    (c for c in holder.named_children if c.type == "identifier"), None
                )
                alias = _text(alias_node).lower() or None

    def walk(node: Node, in_select: bool) -> None:
        for child in node.named_children:
            kind = child.type
            if kind in ("from_clause", "type_of_clause", "bound_apex_expression"):
                continue
            if kind == "subquery":
                # In the select list it names a child relationship, not an object.
                inner = next((c for c in child.named_children if c.type == "soql_query_body"), None)
                if inner is not None and not in_select:
                    _soql(inner, emit)
                continue
            if kind == "field_identifier":
                path = _field_path(child, alias)
                if obj and path:
                    emit(child, obj, path)
                continue
            walk(child, in_select or kind == "select_clause")

    walk(body, False)


def _descendants(node: Node, stop: frozenset[str] = frozenset()) -> list[Node]:
    """Every named descendant, not entering node types in *stop*."""
    out: list[Node] = []
    stack = list(node.named_children)
    while stack:
        child = stack.pop()
        out.append(child)
        if child.type not in stop:
            stack.extend(child.named_children)
    return out


def _sosl(body: Node, emit: _Emit) -> None:
    for node in _descendants(body, frozenset({"sobject_return"})):
        if node.type != "sobject_return":
            continue
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        obj = _text(ident)
        if ident is None or not obj:
            continue
        emit(ident, obj, ())
        for field in _descendants(node, frozenset({"field_identifier", "bound_apex_expression"})):
            path = _field_path(field, None) if field.type == "field_identifier" else ()
            if path:
                emit(field, obj, path)


def _query(node: Node, emit: _Emit) -> None:
    for child in node.named_children if node.type not in _BODIES else [node]:
        if child.type == "soql_query_body":
            _soql(child, emit)
        elif child.type == "sosl_query_body":
            _sosl(child, emit)


def _literal_query(literal: Node, grammar: str, emit: _Emit) -> None:
    """Re-parse one Apex string literal as a standalone query, reported at the literal."""
    raw = _text(literal)
    if len(raw) < 2 or raw[0] != "'" or raw[-1] != "'":
        return
    text = raw[1:-1].replace("\\'", "'").replace("\\\\", "\\")
    parser = _parser(grammar)
    if parser is None:
        return
    root = parser.parse(text.encode("utf-8")).root_node
    _query(root, lambda _n, obj, path: emit(literal, obj, path))


# ---------------------------------------------------------------------------
# DML targets
# ---------------------------------------------------------------------------


def _type_name(node: Node | None) -> str | None:
    """The SObject a declared type holds: ``Account``, ``List<Account>``, ``Account[]``."""
    if node is None:
        return None
    if node.type == "generic_type":
        base = next((c for c in node.named_children if c.type != "type_arguments"), None)
        args = next((c for c in node.named_children if c.type == "type_arguments"), None)
        if _text(base).lower() not in _COLLECTIONS or args is None or not args.named_children:
            return None
        return _type_name(args.named_children[0])
    if node.type == "array_type":
        return _type_name(node.child_by_field_name("element"))
    if node.type in ("type_identifier", "scoped_type_identifier"):
        return _text(node).rpartition(".")[2] or None
    return None


def _declared_names(decl: Node) -> list[str]:
    name = decl.child_by_field_name("name")
    if name is not None:
        return [_text(name)]
    return [
        _text(d.child_by_field_name("name"))
        for d in decl.named_children
        if d.type == "variable_declarator"
    ]


def _key(node: Node) -> tuple[int, int, str]:
    return (node.start_byte, node.end_byte, node.type)


def _visible(decl: Node, ancestors: set[tuple[int, int, str]]) -> bool:
    if decl.type == "formal_parameter":
        return True
    holder = decl if decl.type == "enhanced_for_statement" else decl.parent
    return holder is not None and _key(holder) in ancestors


def _declares(node: Node, wanted: str) -> bool:
    return wanted in (n.lower() for n in _declared_names(node))


def _local_declaration(scope: Node, use: Node, wanted: str) -> Node | None:
    """The nearest earlier declaration of *wanted* in *scope* that is visible at *use*.

    Visible means its scope encloses the use: a local's enclosing block, a
    for-each's own statement, or the method's parameters.
    """
    ancestors: set[tuple[int, int, str]] = set()
    node = use.parent
    while node is not None and _key(node) != _key(scope):
        ancestors.add(_key(node))
        node = node.parent
    candidates = [
        n
        for n in _descendants(scope)
        if n.start_byte < use.start_byte
        and n.type in _DECLARATIONS
        and _declares(n, wanted)
        and _visible(n, ancestors)
    ]
    return max(candidates, key=lambda n: n.start_byte, default=None)


def _field_declaration(node: Node, wanted: str) -> Node | None:
    klass = node.parent
    while klass is not None and klass.type != "class_body":
        klass = klass.parent
    if klass is None:
        return None
    return next(
        (f for f in klass.named_children if f.type == "field_declaration" and _declares(f, wanted)),
        None,
    )


def _declared_type(use: Node, name: str) -> str | None:
    """The type the nearest visible declaration of *name* gives it, method then class."""
    wanted = name.lower()
    scope = use.parent
    while scope is not None and scope.type not in _SCOPES:
        scope = scope.parent
    best = _local_declaration(scope, use, wanted) if scope is not None else None
    if best is None:
        best = _field_declaration(scope or use, wanted)
    return _type_name(best.child_by_field_name("type")) if best is not None else None


def _dml_type(target: Node | None) -> str | None:
    if target is None:
        return None
    # ``new Account(...)``, ``new Account[]{..}``, ``new List<Account>{..}``
    if target.type in ("object_creation_expression", "array_creation_expression"):
        return _type_name(target.child_by_field_name("type"))
    if target.type == "identifier":
        return _declared_type(target, _text(target))
    if target.type == "field_access" and _text(target.child_by_field_name("object")) == "this":
        field = target.child_by_field_name("field")
        return _declared_type(target, _text(field)) if field is not None else None
    return None


def _database_call(node: Node, emit: _Emit) -> None:
    receiver = node.child_by_field_name("object")
    method = _text(node.child_by_field_name("name")).lower()
    args = node.child_by_field_name("arguments")
    if receiver is None or receiver.type != "identifier" or args is None:
        return
    first = args.named_children[0] if args.named_children else None
    if first is None:
        return
    owner = _text(receiver).lower()
    if owner == "database" and method in _QUERY_METHODS:
        if first.type == "string_literal":
            _literal_query(first, "soql", emit)
    elif owner == "search" and method == "query":
        if first.type == "string_literal":
            _literal_query(first, "sosl", emit)
    elif owner == "database" and method in _DML_METHODS:
        obj = _dml_type(first)
        if obj:
            emit(node, obj, ())


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _collector(path: str, symbols: list[Symbol]) -> tuple[list[SchemaReference], _Emit]:
    ranges = sorted(((s.start_line, s.end_line, s.id) for s in symbols), key=lambda t: (t[0], -t[1]))
    module = f"{path}::__module__"
    refs: list[SchemaReference] = []

    def emit(node: Node, obj: str, field_path: tuple[str, ...]) -> None:
        line = node.start_point[0] + 1
        owner = _find_enclosing_symbol(line, ranges) or module
        refs.append(SchemaReference(owner, obj, line, field_path))

    return refs, emit


def apex_schema_refs(root: Node, symbols: list[Symbol], path: str) -> list[SchemaReference]:
    """Every SObject and field an Apex file names statically."""
    refs, emit = _collector(path, symbols)
    stack = [root]
    while stack:
        node = stack.pop()
        kind = node.type
        if kind in _BODIES:
            _query(node, emit)
            continue
        if kind == "trigger_declaration":
            obj = node.child_by_field_name("object")
            if obj is not None and _text(obj):
                emit(obj, _text(obj), ())
        elif kind == "dml_expression":
            obj_name = _dml_type(node.child_by_field_name("target"))
            if obj_name:
                emit(node, obj_name, ())
        elif kind == "method_invocation":
            _database_call(node, emit)
        stack.extend(node.named_children)
    return refs


def parse_soql_file(file_info: FileInfo, source: bytes) -> ParsedFile:
    """A ``.soql`` file: the objects and fields its query names, owned by the file."""
    refs, emit = _collector(file_info.path, [])
    parser = _parser("soql")
    if parser is not None:
        _query(parser.parse(source).root_node, emit)
    return ParsedFile(file_info=file_info, symbols=[], imports=[], exports=[], schema_refs=refs)
