"""Whether a function is marked deprecated.

A deprecated function is on its way out, so a finding on it is rarely the
first thing to fix. This reads the two ways code says so:

- a marker on the declaration: Python ``@deprecated``, Java / Kotlin
  ``@Deprecated``, C# ``[Obsolete]``, Rust ``#[deprecated]``, a JSDoc
  ``@deprecated`` tag, or Go's ``// Deprecated:`` doc paragraph;
- a top-level statement of the body that issues a deprecation warning
  (``warnings.warn(..., DeprecationWarning)``, ``console.warn("... deprecated")``,
  a ``warn_deprecated(...)`` helper).

Only an unconditional warning at the top of the body counts (the first two
statements after a docstring). One inside a branch, or one whose message
names a parameter (``"old_opt is deprecated"``), deprecates an option or a
code path, not the function. A function whose own name says it warns
(``warn_deprecated`` itself) is a helper for other deprecations and is not
read as deprecated by its body.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from .ast_utils import declaration_head

if TYPE_CHECKING:
    from tree_sitter import Node

    from .languages import LanguageNodeMap

_MARKER_RE = re.compile(
    r"@(?:[\w.]+\.)?deprecated\b"
    r"|\[\s*(?:System\.)?Obsolete(?:Attribute)?\b"
    r"|#\[\s*deprecated\b"
    r"|^\s*(?://+|/?\*+|#)?\s*Deprecated:",
    re.IGNORECASE | re.MULTILINE,
)
_DEPRECATION_WORD_RE = re.compile(r"deprecat|Removed\w*Warning", re.IGNORECASE)
_WARN_CALLEE_RE = re.compile(r"warn", re.IGNORECASE)
_HELPER_NAME_RE = re.compile(r"deprecat|warn", re.IGNORECASE)

# ``annotat`` covers Kotlin's top-level ``annotated_expression``: the grammar
# reads a file-level ``@Deprecated(...)`` as an expression beside the ``fun``.
_PRECEDING_KINDS = ("comment", "attribute", "annotat", "decorator")
_WRAPPER_KINDS = frozenset({"export_statement", "decorated_definition"})
_STATEMENT_LISTS = frozenset({"statement_list", "body_statement"})


def _text(node: Node | None) -> str:
    return (node.text or b"").decode("utf-8", errors="replace") if node is not None else ""


def _preceding_text(anchor: Node) -> list[str]:
    """Comments and attributes directly above *anchor*, nearest first."""
    out: list[str] = []
    sib = anchor.prev_sibling
    while sib is not None and any(k in sib.type for k in _PRECEDING_KINDS):
        out.append(_text(sib))
        sib = sib.prev_sibling
    return out


def _declaration_text(fn_node: Node, body: Node, source: bytes) -> list[str]:
    texts: list[str] = []
    # The head stops at the parameters, whose inline types can carry a JSDoc
    # ``@deprecated`` for one option.
    if head := declaration_head(fn_node, body, source):
        texts.append(head)
    anchor = fn_node
    while anchor.parent is not None and anchor.parent.type in _WRAPPER_KINDS:
        anchor = anchor.parent
        # Python decorators sit on the wrapper, beside the definition.
        texts.extend(_text(c) for c in anchor.children if "decorator" in c.type)
    texts.extend(_preceding_text(anchor))
    return texts


_TOP_STATEMENTS = 2
_IGNORED_PARAMETERS = frozenset({"self", "cls", "this"})
_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _top_level_statements(body: Node) -> list[Node]:
    """The first statements of the body, after a leading docstring."""
    named = [c for c in body.named_children if "comment" not in c.type]
    if len(named) == 1 and named[0].type in _STATEMENT_LISTS:
        named = [c for c in named[0].named_children if "comment" not in c.type]
    if named and named[0].type == "expression_statement" and named[0].named_children and (
        named[0].named_children[0].type == "string"
    ):
        named = named[1:]
    return named[:_TOP_STATEMENTS]


def _parameter_names(fn_node: Node) -> set[str]:
    params = fn_node.child_by_field_name("parameters") or next(
        (c for c in fn_node.children if "parameter" in c.type), None
    )
    names: set[str] = set()
    for param in params.named_children if params is not None else ():
        name = (
            param
            if param.type == "identifier"
            else param.child_by_field_name("name")
            or param.child_by_field_name("pattern")
            or next((c for c in param.named_children if c.type == "identifier"), None)
        )
        if name is not None and name.text:
            names.add(name.text.decode("utf-8", errors="replace"))
    return names - _IGNORED_PARAMETERS


def _call_in(statement: Node, lmap: LanguageNodeMap) -> Node | None:
    """The call a statement consists of. ``return f()`` is a value, not a warning."""
    if statement.type in lmap.call_kinds:
        return statement
    named = statement.named_children
    if (
        statement.type.endswith("expression_statement")
        and len(named) == 1
        and named[0].type in lmap.call_kinds
    ):
        return named[0]
    return None


def _warns_deprecation(call: Node, source: bytes) -> bool:
    args = call.child_by_field_name("arguments")
    end = args.start_byte if args is not None else call.end_byte
    callee = source[call.start_byte : end].decode("utf-8", errors="replace").strip()
    if not _WARN_CALLEE_RE.search(callee):
        return False
    # ``warn_deprecated(...)`` / ``deprecated.warn(...)`` say it in the name;
    # ``warnings.warn(...)`` / ``console.warn(...)`` say it in the arguments.
    return bool(
        _DEPRECATION_WORD_RE.search(callee)
        or (args is not None and _DEPRECATION_WORD_RE.search(_text(args)))
    )


def is_deprecated(
    fn_node: Node, body: Node, name: str, lmap: LanguageNodeMap, source: bytes
) -> bool:
    """Whether the function at *fn_node* declares or announces its deprecation."""
    if any(_MARKER_RE.search(t) for t in _declaration_text(fn_node, body, source)):
        return True
    if _HELPER_NAME_RE.search(name) or body is fn_node:
        return False
    parameters = _parameter_names(fn_node)
    for statement in _top_level_statements(body):
        call = _call_in(statement, lmap)
        if call is None or not _warns_deprecation(call, source):
            continue
        message = set(_WORD_RE.findall(_text(call.child_by_field_name("arguments"))))
        return not (message & parameters)
    return False
