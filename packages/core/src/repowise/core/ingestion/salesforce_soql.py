"""SObject and field names written in Apex SOQL/SOSL, DML and triggers.

Produces :class:`~.models.SchemaReference` records that
``salesforce_schema.add_schema_edges`` binds to object and field metadata:

* inline ``[SELECT ... FROM Obj]``: the top-level ``FROM`` object, and every
  field path selected, filtered or ordered on it. A subquery in the select
  list names a child relationship (``FROM Contacts``), never an object, so it
  is skipped; a semi-join subquery in ``WHERE`` names a real object.
* inline ``[FIND ... RETURNING Obj(fields)]``: each returned object and its fields.
* ``insert acc;`` and ``Database.insert(acc)``: the declared type of the
  target (``Account acc``, ``List<Account> accs``, ``new Account(...)``).
* ``trigger T on Obj``: the object.
* ``Database.query('...')`` and ``Search.query('...')`` with a single string
  literal, re-parsed with the standalone SOQL/SOSL grammar. Anything built by
  concatenation names nothing statically and yields nothing.
* a ``.soql`` file, read the same way.

Ceilings, deliberately: ``Account.Name.getDescribe()``-style schema tokens,
``TYPEOF`` branches, and the child object behind a child relationship name
are not read.
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


def _sosl(body: Node, emit: _Emit) -> None:
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type != "sobject_return":
            stack.extend(node.named_children)
            continue
        ident = next((c for c in node.named_children if c.type == "identifier"), None)
        obj = _text(ident)
        if ident is None or not obj:
            continue
        emit(ident, obj, ())
        fields = [node]
        while fields:
            n = fields.pop()
            for c in n.named_children:
                if c.type == "field_identifier":
                    path = _field_path(c, None)
                    if path:
                        emit(c, obj, path)
                elif c.type not in ("bound_apex_expression", "identifier"):
                    fields.append(c)


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


def _declared_type(use: Node, name: str) -> str | None:
    """The type the nearest earlier declaration of *name* gives it, method then class."""
    wanted = name.lower()
    # Only a declaration whose scope encloses the use is visible: a local's
    # enclosing block, a for-each's own statement, or the method's parameters.
    ancestors: set[tuple[int, int, str]] = set()
    scope = use.parent
    while scope is not None and scope.type not in _SCOPES:
        ancestors.add(_key(scope))
        scope = scope.parent
    best: Node | None = None
    if scope is not None:
        stack = [scope]
        while stack:
            node = stack.pop()
            if node.start_byte >= use.start_byte:
                continue
            if (
                node.type in _DECLARATIONS
                and wanted in (n.lower() for n in _declared_names(node))
                and _visible(node, ancestors)
                and (best is None or node.start_byte > best.start_byte)
            ):
                best = node
            stack.extend(node.named_children)
    if best is None:
        klass = (scope or use).parent
        while klass is not None and klass.type != "class_body":
            klass = klass.parent
        if klass is not None:
            best = next(
                (
                    f
                    for f in klass.named_children
                    if f.type == "field_declaration"
                    and wanted in (n.lower() for n in _declared_names(f))
                ),
                None,
            )
    return _type_name(best.child_by_field_name("type")) if best is not None else None


def _dml_type(target: Node | None) -> str | None:
    if target is None:
        return None
    if target.type == "object_creation_expression":
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
