"""Error-handling anti-pattern detection (``error_handling`` biomarker).

Ported from the bench-validated detector (24/24 fixtures across 11
languages). Precision-first: every detector targets the unambiguous
shape and degrades to "no signal" rather than guessing.

``_collect_error_handling`` is a whole-tree pass emitting one
``ErrorHandlingHit`` per swallowed catch / bare except / Rust panic-unwrap /
Go err-swallow it finds, anywhere in the file (not just function bodies).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .languages import LanguageNodeMap
from .models import ErrorHandlingHit

if TYPE_CHECKING:
    from tree_sitter import Node

# Block-like body node types of a catch/except clause.
_EH_BLOCK_KINDS = frozenset({"block", "statement_block", "compound_statement"})
# Statement node types that count as "no real handling" inside a catch body.
_EH_TRIVIAL_STMT = frozenset({"comment", "pass_statement", "line_comment", "block_comment"})
# Rust: each of these is a latent panic-on-error.
_RUST_UNWRAP_METHODS = frozenset({"unwrap", "expect", "unwrap_unchecked"})
_RUST_PANIC_MACROS = frozenset({"panic", "unreachable", "todo", "unimplemented"})


def _eh_text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _eh_named(node: Node) -> list[Node]:
    return [c for c in node.children if c.is_named]


def _eh_find_body_block(clause: Node) -> Node | None:
    """The block-like child of a catch/except clause (its handler body)."""
    for c in clause.children:
        if c.type in _EH_BLOCK_KINDS:
            return c
    # Kotlin catch_block / some grammars nest the block one level down.
    for c in clause.children:
        for g in c.children:
            if g.type in _EH_BLOCK_KINDS:
                return g
    return None


def _eh_is_trivial_stmt(stmt: Node, language: str) -> bool:
    if stmt.type in _EH_TRIVIAL_STMT:
        return True
    if language == "python" and stmt.type == "expression_statement":
        inner = _eh_named(stmt)
        if not inner:
            return True
        # ``...`` or a docstring as the entire statement.
        if inner[0].type in ("ellipsis", "string"):
            return True
    return False


def _eh_body_is_swallowed(block: Node, language: str) -> bool:
    real = [c for c in _eh_named(block) if not _eh_is_trivial_stmt(c, language)]
    return len(real) == 0


def _eh_is_bare_except(clause: Node) -> bool:
    """Python ``except:`` / ``except Exception:`` / ``except BaseException:``."""
    kids = [c for c in clause.children if c.type != "comment"]
    after = [c for c in kids if c.type not in ("except", ":") and c.type not in _EH_BLOCK_KINDS]
    if not after:
        return True  # bare ``except:``
    # ``except Exception:`` / ``except BaseException:`` (single catch-all
    # identifier — a tuple of specific types or an ``as`` binding on a
    # specific type does not match).
    first = after[0]
    return first.type == "identifier" and _eh_text(first) in ("Exception", "BaseException")


def _eh_rust_hit(node: Node) -> bool:
    """True when *node* is an unwrap/expect call or a panic-family macro."""
    if node.type == "call_expression":
        fn = node.child_by_field_name("function")
        if fn is not None and fn.type == "field_expression":
            fld = fn.child_by_field_name("field")
            return fld is not None and _eh_text(fld) in _RUST_UNWRAP_METHODS
        return False
    if node.type == "macro_invocation":
        mac = node.child_by_field_name("macro")
        return mac is not None and _eh_text(mac) in _RUST_PANIC_MACROS
    return False


def _eh_go_cond_is_err_check(cond_text: str) -> bool:
    t = cond_text.replace(" ", "")
    return "err!=nil" in t or "err==nil" in t


def _eh_go_hit(node: Node) -> bool:
    """Go: empty ``if err != nil {}`` or blank-identifier discard of a call."""
    if node.type == "if_statement":
        cond = node.child_by_field_name("condition")
        cons = node.child_by_field_name("consequence")
        return (
            cond is not None
            and cons is not None
            and _eh_go_cond_is_err_check(_eh_text(cond))
            and len(_eh_named(cons)) == 0
        )
    if node.type in ("short_var_declaration", "assignment_statement"):
        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return False
        left_kids = _eh_named(left)
        has_blank = any(c.type == "blank_identifier" or _eh_text(c) == "_" for c in left_kids)
        right_is_call = any(c.type == "call_expression" for c in _eh_named(right)) or (
            right.type == "expression_list"
            and any(c.type == "call_expression" for c in _eh_named(right))
        )
        # Multi-return discard: ≥2 LHS targets, a call on the RHS, a blank present.
        return has_blank and len(left_kids) >= 2 and right_is_call
    return False


def _collect_error_handling(
    root: Node, language: str, lmap: LanguageNodeMap
) -> list[ErrorHandlingHit]:
    """Whole-tree pass: every error-handling anti-pattern with its line.

    Catch-clause shapes reuse the ``LanguageNodeMap`` catch kinds (Python
    ``except_clause``; JS/TS/Java/C++/C# ``catch_clause``; Kotlin
    ``catch_block``); Rust and Go have no catch nodes and use their own
    recognizers. Module-level code is covered too — anti-patterns are not
    confined to function bodies.
    """
    hits: list[ErrorHandlingHit] = []
    catch_kinds = lmap.catch_kinds
    is_python = language == "python"
    is_rust = language == "rust"
    is_go = language == "go"
    stack: list[Node] = [root]
    while stack:
        node = stack.pop()
        if catch_kinds and node.type in catch_kinds:
            block = _eh_find_body_block(node)
            if block is not None and _eh_body_is_swallowed(block, language):
                hits.append(ErrorHandlingHit("swallowed_catch", node.start_point[0] + 1))
            if is_python and _eh_is_bare_except(node):
                hits.append(ErrorHandlingHit("bare_except", node.start_point[0] + 1))
        elif is_rust and _eh_rust_hit(node):
            hits.append(ErrorHandlingHit("unsafe_unwrap", node.start_point[0] + 1))
        elif is_go and _eh_go_hit(node):
            hits.append(ErrorHandlingHit("go_swallow", node.start_point[0] + 1))
        stack.extend(node.children)
    hits.sort(key=lambda h: h.line)
    return hits
