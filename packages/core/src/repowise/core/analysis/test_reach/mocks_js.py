"""Which modules a JavaScript or TypeScript test replaces outright, read from its text.

``vi.mock(spec, factory)`` and ``jest.mock(spec, factory)`` swap the module at
*spec* for whatever *factory* returns, for every import in the test. The swap
masks the module only when the stand-in provably holds nothing of the original:
a closed factory, with zero parameters (a parameter can receive the original
module) and a body of literals, objects, arrays and bare runner doubles
(``vi.fn()``). Any other call, a free identifier, a dynamic ``import()``, an
``await``, a spread or a ``new`` abstains, and so does every way of reaching the
original (``importActual``, ``requireActual``, ``importOriginal``) since none is
a bare double. The vocabulary is the mock lexicon's
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


class _Proof:
    """Reads callees against the runner bindings one file proves."""

    def __init__(
        self, dialect: MockDialect, runners: dict[str, str], namespaces: dict[str, str]
    ):
        self.dialect = dialect
        self.runners = runners
        self.namespaces = namespaces

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
            return _text(node) == "undefined"
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
            callee = self.callee(node.child_by_field_name("function"))
            if callee is None or callee.rsplit(".", 1)[-1] not in self.dialect.double_callees:
                return False
            args = node.child_by_field_name("arguments")
            return args is not None and all(self._closed(c) for c in args.named_children)
        return False
