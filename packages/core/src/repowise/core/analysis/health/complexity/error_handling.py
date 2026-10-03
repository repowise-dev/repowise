"""Error-handling anti-pattern detection (``error_handling`` biomarker).

Ported from the bench-validated detector (24/24 fixtures across 11
languages). Precision-first: every detector targets the unambiguous
shape and degrades to "no signal" rather than guessing.

``_eh_visit`` runs on each node of the walker's single file scan, emitting one
``ErrorHandlingHit`` per swallowed catch / bare except / Rust panic-unwrap /
Go err-swallow it finds, anywhere in the file (not just function bodies).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from . import rust_unwrap
from .languages import LanguageNodeMap
from .models import ErrorHandlingHit

if TYPE_CHECKING:
    from tree_sitter import Node

# A bare ``test`` cfg predicate token: preceded by ``(`` / ``,`` / start and
# followed by ``)`` / ``,`` / end, so ``feature = "test"`` (a string) does not match.
_RUST_TEST_CFG_TOKEN = re.compile(r"(?:^|[(,])test(?:[),]|$)")

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


def _eh_real_stmts(block: Node, language: str) -> list[Node]:
    return [c for c in _eh_named(block) if not _eh_is_trivial_stmt(c, language)]


def _eh_body_is_swallowed(block: Node, language: str) -> bool:
    return not _eh_real_stmts(block, language)


def _eh_body_ends_in_raise(block: Node | None, language: str) -> bool:
    """The handler's last real statement is a ``raise``: it cleans up or
    translates, then propagates, so nothing is swallowed however broad it is."""
    real = _eh_real_stmts(block, language) if block is not None else []
    return bool(real) and real[-1].type == "raise_statement"


def _eh_except_catch_all_name(clause: Node) -> tuple[bool, str | None]:
    """``(is_catch_all, leading_type_name)`` for a Python ``except`` clause.

    ``is_catch_all`` is True for bare ``except:`` and for a single leading
    ``Exception`` / ``BaseException`` identifier (with or without an ``as``
    binding). ``leading_type_name`` is that identifier's text, or ``None`` for
    truly bare ``except:``. A tuple of specific types, or any specific type,
    yields ``(False, None)`` — not a catch-all.
    """
    kids = [c for c in clause.children if c.type != "comment"]
    after = [c for c in kids if c.type not in ("except", ":") and c.type not in _EH_BLOCK_KINDS]
    if not after:
        return True, None  # bare ``except:``
    # ``except Exception as e:`` wraps the type in an ``as_pattern`` — unwrap to
    # the leading type identifier so the binding does not hide a catch-all.
    first = after[0]
    target = first
    if first.type == "as_pattern":
        named = [c for c in first.children if c.is_named]
        target = named[0] if named else first
    if target.type == "identifier" and _eh_text(target) in ("Exception", "BaseException"):
        return True, _eh_text(target)
    return False, None


def _eh_is_bare_except(clause: Node) -> bool:
    """Python catch-all: ``except:`` / ``except Exception:`` / ``except BaseException:``."""
    return _eh_except_catch_all_name(clause)[0]


def _eh_catches_base(clause: Node) -> bool:
    """True only when the catch-all also catches ``BaseException`` subclasses.

    Bare ``except:`` and ``except BaseException:`` catch ``KeyboardInterrupt`` /
    ``SystemExit`` (both derive from ``BaseException``); ``except Exception:``
    provably cannot, so it is *broad* rather than truly catch-all.
    """
    is_catch_all, name = _eh_except_catch_all_name(clause)
    return is_catch_all and name != "Exception"


def _eh_rust_attr_is_test(attr_text: str) -> bool:
    """True when a Rust attribute marks test-only code.

    Recognizes test-runner attributes (``#[test]``, ``#[tokio::test]``,
    ``#[rstest]`` …) whose name path ends in ``test``, and ``cfg`` test gates
    (``#[cfg(test)]``, ``#[cfg(all(test, feature = "x"))]``). Deliberately does
    NOT match ``#[cfg(not(test))]`` — that gates non-test builds, where an
    ``.unwrap()`` is still a real smell.
    """
    t = attr_text.replace(" ", "")
    if t.startswith("#[") and t.endswith("]"):
        t = t[2:-1]
    name = t.split("(", 1)[0]
    if name == "cfg":
        args = t[len("cfg") :]
        if "not(test)" in args:
            return False
        return bool(_RUST_TEST_CFG_TOKEN.search(args))
    # ``test`` / ``tokio::test`` / ``async_std::test`` / ``rstest`` …
    return name.endswith("test")


def _eh_rust_in_test(node: Node) -> bool:
    """True when *node* sits inside a Rust test item.

    ``.unwrap()`` / ``.expect()`` and the panic-family macros are the intended
    failure signal inside a test, not a smell. Walks the enclosing
    ``function_item`` / ``mod_item`` chain and checks each item's preceding
    ``attribute_item`` siblings for a test-runner or ``cfg(test)`` marker.
    """
    cur: Node | None = node.parent
    while cur is not None:
        if cur.type in ("function_item", "mod_item"):
            sib = cur.prev_sibling
            while sib is not None and sib.type in (
                "attribute_item",
                "line_comment",
                "block_comment",
            ):
                if sib.type == "attribute_item" and _eh_rust_attr_is_test(_eh_text(sib)):
                    return True
                sib = sib.prev_sibling
        cur = cur.parent
    return False


def _eh_rust_hit(node: Node) -> bool:
    """True when *node* is an unwrap/expect call or a panic-family macro."""
    if node.type == "call_expression":
        return rust_unwrap.method_call_parts(node)[0] in _RUST_UNWRAP_METHODS
    if node.type == "macro_invocation":
        mac = node.child_by_field_name("macro")
        return mac is not None and _eh_text(mac) in _RUST_PANIC_MACROS
    return False


def _eh_rust_append(node: Node, hits: list[ErrorHandlingHit]) -> None:
    """Record a Rust hit unless it provably cannot panic, labelled with its idiom."""
    if node.type == "macro_invocation":
        kind = "panic_macro"
    elif rust_unwrap.cannot_panic(node):
        return
    else:
        kind = "unsafe_unwrap"
    hits.append(
        ErrorHandlingHit(kind, rust_unwrap.anchor_line(node), rust_unwrap.idiom(node))
    )


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
        # Only the LAST LHS target is Go's conventional error slot. A leading or
        # middle ``_`` discards a value, not the error (``_, err := f()`` keeps
        # the error), so it is not an error-swallow.
        last_is_blank = bool(left_kids) and (
            left_kids[-1].type == "blank_identifier" or _eh_text(left_kids[-1]) == "_"
        )
        right_is_call = any(c.type == "call_expression" for c in _eh_named(right)) or (
            right.type == "expression_list"
            and any(c.type == "call_expression" for c in _eh_named(right))
        )
        # Multi-return discard: ≥2 LHS targets, a call on the RHS, blank in the
        # trailing (error) slot.
        return last_is_blank and len(left_kids) >= 2 and right_is_call
    return False


def _eh_java_try_ends_in_fail(catch_clause: Node, language: str) -> bool:
    """True when the enclosing Java try block's last real statement is a fail(...) call."""
    parent = catch_clause.parent
    if parent is None or parent.type not in ("try_statement", "try_with_resources_statement"):
        return False
    try_body = parent.child_by_field_name("body")
    if try_body is None:
        return False
    real = _eh_real_stmts(try_body, language)
    if not real:
        return False
    last = real[-1]
    if last.type != "expression_statement":
        return False
    inv = last.child_by_field_name("expression") or (
        _eh_named(last)[0] if _eh_named(last) else None
    )
    if inv is None or inv.type != "method_invocation":
        return False
    name_node = inv.child_by_field_name("name")
    return name_node is not None and _eh_text(name_node) == "fail"


# The node types ``_eh_rust_hit`` / ``_eh_go_hit`` can fire on.
_EH_RUST_KINDS = frozenset({"call_expression", "macro_invocation"})
_EH_GO_KINDS = frozenset({"if_statement", "short_var_declaration", "assignment_statement"})


def _eh_node_kinds(language: str, lmap: LanguageNodeMap) -> frozenset[str]:
    """Every node type ``_eh_visit`` can emit a hit on, so the shared file scan
    skips the call for every other node."""
    kinds = set(lmap.catch_kinds)
    if language == "rust":
        kinds |= _EH_RUST_KINDS
    elif language == "go":
        kinds |= _EH_GO_KINDS
    return frozenset(kinds)


def _eh_visit(
    node: Node, language: str, lmap: LanguageNodeMap, hits: list[ErrorHandlingHit]
) -> None:
    """Append the error-handling anti-pattern at *node*, if it is one.

    Called by ``file_scan`` for each node anywhere in the file (module-level
    code too: anti-patterns are not confined to function bodies), which sorts
    the hits by line. Catch-clause shapes reuse the ``LanguageNodeMap`` catch
    kinds (Python ``except_clause``; JS/TS/Java/C++/C# ``catch_clause``; Kotlin
    ``catch_block``); Rust and Go have no catch nodes and use their own
    recognizers.
    """
    catch_kinds = lmap.catch_kinds
    if catch_kinds and node.type in catch_kinds:
        block = _eh_find_body_block(node)
        if (
            block is not None
            and _eh_body_is_swallowed(block, language)
            and not (language == "java" and _eh_java_try_ends_in_fail(node, language))
        ):
            hits.append(ErrorHandlingHit("swallowed_catch", node.start_point[0] + 1))
        if (
            language == "python"
            and _eh_is_bare_except(node)
            and not _eh_body_ends_in_raise(block, language)
        ):
            # ``except:`` / ``except BaseException:`` also swallow
            # KeyboardInterrupt & SystemExit; ``except Exception:`` cannot.
            kind = "bare_except" if _eh_catches_base(node) else "broad_except"
            hits.append(ErrorHandlingHit(kind, node.start_point[0] + 1))
    elif language == "rust" and _eh_rust_hit(node):
        # A panic-family macro aborts unconditionally; unwrap/expect converts
        # a Result/Option into a panic. Different claims → different kinds.
        # ``.unwrap()`` inside a ``#[test]`` is the intended failure signal.
        if not _eh_rust_in_test(node):
            _eh_rust_append(node, hits)
    elif language == "go" and _eh_go_hit(node):
        hits.append(ErrorHandlingHit("go_swallow", node.start_point[0] + 1))
