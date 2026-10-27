"""Which modules a JavaScript or TypeScript test replaces outright, read from its text.

``vi.mock(spec, factory)`` and ``jest.mock(spec, factory)`` swap the module at
*spec* for whatever *factory* returns, for every import in the test. The swap
masks the module only when the stand-in provably holds nothing of the original:
a closed factory, with zero parameters (a parameter can receive the original
module) and a body of literals, objects, arrays and runner doubles: a bare
``vi.fn()``, or one configured by a chained ``mockReturnValue`` /
``mockResolvedValue`` / ``mockRejectedValue`` / ``mockImplementation`` (or their
``Once`` forms) whose every argument is closed. A name bound exactly once, by
``vi.hoisted(<closed factory>)``, is closed too. Any other call, a free
identifier, a dynamic ``import()``, an ``await``, a spread or a ``new`` abstains,
and so does every way of reaching the original (``importActual``,
``requireActual``, ``importOriginal``) since none is a double. The vocabulary is the mock lexicon's
(:attr:`~repowise.core.analysis.health.mocks.lexicon.MockDialect.binding_patches`).

Never masks: an automock (no factory), ``doMock`` (later dynamic imports only),
any mock in a file that also calls ``unmock`` / ``doUnmock``, and a receiver not
proven to be the runner's: ``vi`` must be imported from ``vitest``; ``jest`` may
also be the global, when the file imports none. A name used other than as
``name.method`` anywhere in the file abstains, since it may be rebound.
"""

from __future__ import annotations

from typing import Any

from repowise.core.analysis.health.mocks.lexicon import MockDialect, mock_dialect

JS_SUFFIXES = (".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs")

# Nodes a closed factory may hold beyond calls, functions and ``undefined``:
# literals and the containers that only arrange them.
_LITERALS = frozenset(
    {"string", "number", "true", "false", "null", "template_string", "regex"}
)
_CONTAINERS = frozenset(
    {
        "object",
        "pair",
        "array",
        "parenthesized_expression",
        "statement_block",
        "return_statement",
        "expression_statement",
        "unary_expression",
        "comment",
    }
)
_FUNCTIONS = frozenset({"arrow_function", "function_expression", "function"})

# Nodes that name a binding or a use of one.
_NAME_NODES = frozenset(
    {
        "identifier",
        "shorthand_property_identifier",
        "shorthand_property_identifier_pattern",
        "type_identifier",
    }
)


def replaced_specifiers(path: str, text: str | bytes) -> frozenset[str]:
    """The module specifiers the test at *path* swaps for a closed factory's stand-in."""
    dialect = mock_dialect("typescript")
    if dialect is None or not path.lower().endswith(JS_SUFFIXES):
        return frozenset()
    data = text.encode("utf-8", "replace") if isinstance(text, str) else text
    methods = {callee.rsplit(".", 1)[-1] for callee in dialect.binding_patches}
    if not any(f".{m}(".encode() in data for m in methods):
        return frozenset()
    root = _parse(path, data)
    if root is None:
        return frozenset()
    runners, namespaces = _runner_bindings(root, dialect)
    if not runners and not namespaces:
        return frozenset()
    proof = _Proof(dialect, runners, namespaces)
    proof.hoisted = _hoisted_names(root, proof)
    masked: set[str] = set()
    for call in _calls(root):
        form = dialect.binding_patches.get(proof.callee(call.child_by_field_name("function")) or "")
        if form == "restore":
            # An unmock may name the module by another specifier; any one abstains.
            return frozenset()
        if form != "factory":
            continue
        args = [a for a in call.child_by_field_name("arguments").named_children if a.type != "comment"]
        spec = _literal(args[0]) if args else None
        if spec is not None and len(args) == 2 and proof.closed_factory(args[1]):
            masked.add(spec)
    return frozenset(masked)


def _parse(path: str, data: bytes) -> Any:
    from tree_sitter import Parser

    from repowise.core.ingestion.parser import _get_language
    from repowise.core.ingestion.parser.grammars import grammar_tag_for

    tag = "typescript" if path.lower().endswith((".ts", ".tsx", ".mts", ".cts")) else "javascript"
    language = _get_language(grammar_tag_for(tag, path))
    if language is None:
        return None
    try:
        return Parser(language).parse(data).root_node
    except (ValueError, RecursionError, MemoryError):
        return None


def _calls(root: Any) -> list[Any]:
    out: list[Any] = []
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "call_expression":
            out.append(node)
        stack.extend(node.named_children)
    return out


def _literal(node: Any) -> str | None:
    """The text of a plain string literal; ``None`` for anything built or escaped."""
    if node is None or node.type != "string":
        return None
    parts = node.named_children
    if len(parts) != 1 or parts[0].type != "string_fragment":
        return None
    return parts[0].text.decode("utf-8", "replace")


def _runner_bindings(root: Any, dialect: MockDialect) -> tuple[dict[str, str], dict[str, str]]:
    """``({local name: runner}, {namespace name: runner module})`` the file proves.

    A name is proven only when every other use of it is the object of a member
    access (``vi.mock``): a declaration, parameter, pattern or argument of the
    same name could rebind it, so it abstains.
    """
    runners: dict[str, str] = {}
    namespaces: dict[str, str] = {}
    imported: set[str] = set()
    owner = {m: r for r, mods in dialect.runner_modules.items() for m in mods}
    other: set[str] = set()
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "import_statement":
            source = _literal(node.child_by_field_name("source"))
            for ident in _descendants(node, "identifier"):
                imported.add(_text(ident))
            for spec in _descendants(node, "import_specifier"):
                local = spec.child_by_field_name("alias") or spec.child_by_field_name("name")
                name = _text(spec.child_by_field_name("name"))
                if source in owner and name == owner[source]:
                    runners[_text(local)] = name
            for ns in _descendants(node, "namespace_import"):
                for ident in ns.named_children:
                    if source in owner:
                        namespaces[_text(ident)] = owner[source]
            continue
        stack.extend(node.named_children)
        if node.type in _NAME_NODES:
            parent = node.parent
            used = (
                parent is not None
                and parent.type == "member_expression"
                and parent.child_by_field_name("object") == node
            )
            if not used:
                other.add(_text(node))
    for runner in dialect.runner_globals:
        if runner not in imported:
            runners[runner] = runner
    runners = {name: r for name, r in runners.items() if name not in other}
    namespaces = {name: r for name, r in namespaces.items() if name not in other}
    return runners, namespaces


def _descendants(node: Any, kind: str) -> list[Any]:
    out: list[Any] = []
    stack = list(node.named_children)
    while stack:
        n = stack.pop()
        if n.type == kind:
            out.append(n)
        stack.extend(n.named_children)
    return out


def _text(node: Any) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


# Where a name is bound or written, keyed by the parent node type and the field
# of that parent the name must occupy (the other children are uses).
_BINDING_FIELD = {
    "variable_declarator": "name",
    "function_declaration": "name",
    "generator_function_declaration": "name",
    "function_expression": "name",
    "generator_function": "name",
    "class_declaration": "name",
    "class": "name",
    "assignment_expression": "left",
    "augmented_assignment_expression": "left",
    "for_in_statement": "left",
    "arrow_function": "parameter",
    "assignment_pattern": "left",
    "pair_pattern": "value",
    "catch_clause": "parameter",
    "required_parameter": "pattern",
    "optional_parameter": "pattern",
    "update_expression": "argument",
}
# Parents whose every name child is a binding.
_BINDING_CONTAINERS = frozenset(
    {
        "formal_parameters",
        "array_pattern",
        "object_pattern",
        "rest_pattern",
        "import_specifier",
        "import_clause",
        "namespace_import",
    }
)


def _binds(node: Any) -> bool:
    """Whether *node* binds or writes its name, rather than reading it."""
    if node.type == "shorthand_property_identifier_pattern":
        return True
    parent = node.parent
    if parent is None or node.type != "identifier":
        return False
    if parent.type in _BINDING_CONTAINERS:
        return True
    field = _BINDING_FIELD.get(parent.type)
    return field is not None and parent.child_by_field_name(field) == node


def _hoisted_names(root: Any, proof: _Proof) -> frozenset[str]:
    """Names bound once, by ``vi.hoisted(<closed factory>)``, and never rebound.

    A binding of the same name anywhere else in the file (a second declaration,
    a parameter, an assignment, an import) abstains: the proof is by name, so
    any other binding could be what a reference reads.
    """
    bound: dict[str, int] = {}
    candidates: list[str] = []
    stack = [root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if _binds(node):
            name = _text(node)
            bound[name] = bound.get(name, 0) + 1
        if node.type == "variable_declarator" and _is_hoisted_call(
            node.child_by_field_name("value"), proof
        ):
            candidates.extend(_declared_names(node.child_by_field_name("name")))
    return frozenset(name for name in candidates if bound.get(name) == 1)


def _is_hoisted_call(node: Any, proof: _Proof) -> bool:
    if node is None or node.type != "call_expression":
        return False
    callee = proof.callee(node.child_by_field_name("function"))
    if callee is None or callee.rsplit(".", 1)[-1] not in proof.dialect.hoist_callees:
        return False
    args = node.child_by_field_name("arguments")
    named = [a for a in args.named_children if a.type != "comment"] if args is not None else []
    return len(named) == 1 and proof.closed_factory(named[0])


def _declared_names(target: Any) -> list[str]:
    """The names a declarator target binds: a plain name, or the properties of a
    flat object pattern (``{ f }``, ``{ f: g }``). Defaults, rests and nested
    patterns bind nothing here."""
    if target is None:
        return []
    if target.type == "identifier":
        return [_text(target)]
    if target.type != "object_pattern":
        return []
    names: list[str] = []
    for child in target.named_children:
        if child.type == "shorthand_property_identifier_pattern":
            names.append(_text(child))
        elif child.type == "pair_pattern":
            value = child.child_by_field_name("value")
            if value is not None and value.type == "identifier":
                names.append(_text(value))
    return names


class _Proof:
    """Reads callees against the runner bindings one file proves."""

    def __init__(
        self, dialect: MockDialect, runners: dict[str, str], namespaces: dict[str, str]
    ):
        self.dialect = dialect
        self.runners = runners
        self.namespaces = namespaces
        self.hoisted: frozenset[str] = frozenset()

    def runner(self, node: Any) -> str | None:
        """``vi`` / ``jest`` when *node* is a proven runner object, else ``None``."""
        if node is None:
            return None
        if node.type == "identifier":
            return self.runners.get(_text(node))
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            prop = _text(node.child_by_field_name("property"))
            if obj is not None and obj.type == "identifier":
                return prop if self.namespaces.get(_text(obj)) == prop else None
        return None

    def callee(self, node: Any) -> str | None:
        """``vi.mock`` for a proven ``<runner>.mock``; ``None`` for anything else."""
        if node is None or node.type != "member_expression":
            return None
        runner = self.runner(node.child_by_field_name("object"))
        if runner is None:
            return None
        return f"{runner}.{_text(node.child_by_field_name('property'))}"

    def closed_factory(self, node: Any) -> bool:
        """A zero-parameter function whose stand-in holds only literals and bare doubles."""
        try:
            return node.type in _FUNCTIONS and self._closed(node)
        except RecursionError:
            return False

    def _closed(self, node: Any) -> bool:
        kind = node.type
        if kind in _LITERALS:
            return kind != "template_string" or not any(
                c.type == "template_substitution" for c in node.named_children
            )
        if kind == "identifier":
            return _text(node) == "undefined" or _text(node) in self.hoisted
        if kind == "shorthand_property_identifier":
            return _text(node) in self.hoisted
        if kind in ("undefined", "property_identifier", "string_fragment", "escape_sequence"):
            return True
        if kind in _CONTAINERS:
            return all(self._closed(c) for c in node.named_children)
        if kind in _FUNCTIONS:
            params = node.child_by_field_name("parameters")
            if node.child_by_field_name("parameter") is not None:
                return False
            if params is not None and any(c.type != "comment" for c in params.named_children):
                return False
            body = node.child_by_field_name("body")
            return body is not None and self._closed(body)
        if kind == "call_expression":
            return self._double(node)
        return False

    def _double(self, node: Any) -> bool:
        """A bare runner double (``vi.fn()``), or one configured by chained
        ``mockReturnValue(...)``-style verbs, with every argument closed."""
        if node is None or node.type != "call_expression":
            return False
        args = node.child_by_field_name("arguments")
        if args is None or not all(self._closed(c) for c in args.named_children):
            return False
        func = node.child_by_field_name("function")
        if (
            func is not None
            and func.type == "member_expression"
            and _text(func.child_by_field_name("property")) in self.dialect.double_configurers
        ):
            return self._double(func.child_by_field_name("object"))
        callee = self.callee(func)
        return callee is not None and callee.rsplit(".", 1)[-1] in self.dialect.double_callees
