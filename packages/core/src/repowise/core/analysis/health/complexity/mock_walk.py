"""Per-function mock-setup counting (the numerator of mock saturation).

Counts, for one function body, how many **statements** are mock setup rather
than the test's actual work. The vocabulary is per-language data in
``analysis/health/mocks/lexicon.py``; everything here is language-agnostic and
driven by the ``LanguageNodeMap``, so adding a language is a lexicon row plus its
node kinds.

Two grammar shapes are handled here rather than per language, so that stays
true: a call whose callee is one subtree (Python, TS) and a call that splits the
name from its receiver into separate fields (Java, C#). A grammar that is
neither would need this file, and that is worth knowing before promising a
language it does not cover.

Two rules keep the count honest. Statements, not tokens:
``repo.load.return_value = MagicMock()`` is one act of setup though it matches
twice. And an assertion is never setup, even when it reads a double
(``mock.assert_called_once_with(...)``), so anything ambiguous counts against
firing.

A language that maps no ``block_kinds`` produces no count: without them a
statement cannot be told from an intermediate node.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..mocks.lexicon import MOCK_IDENTIFIER_TOKENS, MockDialect
from .assertions import _is_assertion_statement
from .ast_utils import _IDENTIFIER_SUFFIX
from .languages import LanguageNodeMap

if TYPE_CHECKING:
    from tree_sitter import Node

# Whole-file precheck, so production code pays one substring scan and nothing
# else. Matched against a lowercased copy, so casing never matters.
_FILE_MARKERS: tuple[bytes, ...] = tuple(
    sorted(tok.encode() for tok in (*MOCK_IDENTIFIER_TOKENS, "patch"))
)


def file_may_contain_mocks(source: bytes) -> bool:
    """True when *source* is worth running the mock pass over.

    A pure function of the file's bytes, so it is safe under the walk cache,
    which keys on content and language and never on the path.
    """
    lowered = source.lower()
    return any(marker in lowered for marker in _FILE_MARKERS)


def _identifier_chain(node: Node) -> list[str]:
    """Every identifier under *node*, lowercased, in document order.

    Order is load-bearing: the last entry is the name being called, the rest
    the receiver path. Argument lists are never descended into.
    """
    names: list[str] = []
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur.type in ("argument_list", "arguments"):
            continue
        if cur.type.endswith(_IDENTIFIER_SUFFIX) and cur.text is not None:
            names.append(cur.text.decode("utf-8", "replace").lower())
        stack.extend(reversed(cur.children))
    return names


def _callee_names(call_node: Node) -> tuple[str, set[str]] | None:
    """``(called_name, receiver_roots)`` for a call, or ``None``.

    Two grammar shapes: a single callee subtree under ``function`` / ``macro``,
    read rightmost-last so ``mock.patch(...)`` gives ``("patch", {"mock"})``; or
    a ``name`` field beside an ``object`` field (Java, C#), where the callee is
    not one node. Arguments are excluded in both.
    """
    name_node = call_node.child_by_field_name("name")
    if name_node is not None:
        # Split shape: the name IS the called name, the receiver is ``object``.
        called = (name_node.text or b"").decode("utf-8", "replace").lower()
        if not called:
            return None
        receiver = call_node.child_by_field_name("object")
        return called, set(_identifier_chain(receiver)) if receiver is not None else set()

    callee = call_node.child_by_field_name("function") or call_node.child_by_field_name("macro")
    if callee is None:
        named = [
            c
            for c in call_node.children
            if c.is_named and c.type not in ("argument_list", "arguments")
        ]
        callee = named[0] if named else None
    if callee is None:
        return None
    chain = _identifier_chain(callee)
    if not chain:
        return None
    return chain[-1], set(chain[:-1])


def _is_mock_call(call_node: Node, dialect: MockDialect) -> bool:
    """True when *call_node* constructs, patches or configures a test double."""
    names = _callee_names(call_node)
    if names is None:
        return False
    called, roots = names
    for root in roots:
        methods = dialect.receiver_methods.get(root)
        if methods is not None:
            # A listed receiver is exhaustive about its own methods.
            return called in methods
    if any(token in name for name in (called, *roots) for token in MOCK_IDENTIFIER_TOKENS):
        return True
    if called in dialect.setup_callees or roots & dialect.setup_callees:
        return True
    if called in dialect.guarded_callees:
        # A bare call, or a known mocking module. ``client.patch("/url")``
        # is an HTTP request, not a mock.
        return not roots or bool(roots & dialect.guard_roots)
    return False


def _is_config_assignment(stmt: Node, lmap: LanguageNodeMap, dialect: MockDialect) -> bool:
    """True when *stmt* assigns to a double's configuration attribute."""
    if not dialect.config_attributes:
        return False
    for node in (stmt, *(c for c in stmt.children if c.is_named)):
        if node.type not in lmap.assignment_kinds:
            continue
        left = node.child_by_field_name("left")
        if left is None:
            continue
        chain = _identifier_chain(left)
        # The attribute needs a receiver, or a local named ``return_value``
        # reads as mock configuration.
        if len(chain) >= 2 and any(name in dialect.config_attributes for name in chain[1:]):
            return True
    return False


def _contains_mock_call(node: Node, lmap: LanguageNodeMap, dialect: MockDialect) -> bool:
    """True when *node*'s own expression contains a mock call.

    Descends through the statement but stops at nested function bodies and at
    nested blocks, whose statements are scanned in their own right.
    """
    stack: list[Node] = [node]
    while stack:
        cur = stack.pop()
        if cur.type in lmap.call_kinds and _is_mock_call(cur, dialect):
            return True
        for child in cur.children:
            if child.type in lmap.function_kinds or child.type in lmap.block_kinds:
                continue
            stack.append(child)
    return False


def _count_decorators(fn_node: Node, lmap: LanguageNodeMap, dialect: MockDialect) -> int:
    """Mock-injecting decorators on *fn_node*'s signature.

    ``@patch("a.b")`` injects a double exactly as a ``patch(...)`` call in the
    body would. Grammars that park decorators on a wrapper node declare it via
    ``decorated_definition_kinds``; the rest keep them inside the function node.
    The scan descends one level, since C# groups attributes under an
    ``attribute_list`` and Java annotations under ``modifiers``.
    """
    if not lmap.decorator_kinds:
        return 0
    region = fn_node
    parent = fn_node.parent
    if parent is not None and parent.type in lmap.decorated_definition_kinds:
        region = parent
    candidates: list[Node] = []
    for child in region.children:
        if child.type in lmap.block_kinds:
            continue  # the body, not the signature
        candidates.append(child)
        candidates.extend(child.children)
    count = 0
    for child in candidates:
        if child.type not in lmap.decorator_kinds:
            continue
        if _contains_mock_call(child, lmap, dialect) or any(
            token in name for name in _identifier_chain(child) for token in MOCK_IDENTIFIER_TOKENS
        ):
            count += 1
    return count


def _count_body_setup(body: Node, lmap: LanguageNodeMap, dialect: MockDialect) -> int:
    """Mock-setup statements within *body*.

    Statements are the named children of a block, so a nested block's
    statements are counted once, at their own level.
    """
    count = 0

    def _scan(block: Node) -> None:
        nonlocal count
        for stmt in block.children:
            if not stmt.is_named:
                continue
            # Assertions are classified first and are never setup, even when
            # they read a double (``mock.assert_called_once()``).
            if _is_assertion_statement(stmt, lmap):
                continue
            if _is_config_assignment(stmt, lmap, dialect) or _contains_mock_call(
                stmt, lmap, dialect
            ):
                count += 1

    def _visit(node: Node) -> None:
        if node.type in lmap.block_kinds:
            _scan(node)
        for child in node.children:
            if child.type in lmap.function_kinds:
                # Not collected as its own entry either, so its setup is
                # dropped rather than reattributed. Only suppresses findings.
                continue
            _visit(child)

    _visit(body)
    return count


def _count_mock_setup(
    fn_node: Node,
    body: Node,
    lmap: LanguageNodeMap,
    dialect: MockDialect | None,
) -> int:
    """Mock-setup statements for one function, decorators included."""
    if dialect is None or not lmap.block_kinds or not lmap.call_kinds:
        return 0
    return _count_decorators(fn_node, lmap, dialect) + _count_body_setup(body, lmap, dialect)
