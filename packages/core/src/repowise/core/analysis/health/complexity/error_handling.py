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
#: Shared with the dataflow pass's error exits (``dataflow.raises``).
RUST_PANIC_MACROS = frozenset({"panic", "unreachable", "todo", "unimplemented"})
#: Python handler types that catch every ordinary exception; only the second
#: also catches the interpreter's exit signals.
PY_CATCH_ALL = ("Exception", "BaseException")
# Cancellation is a stop signal, not an error: dropping it after cancelling a
# task is deliberate, so a handler catching only these is not swallowing.
_CANCEL_TYPES = {
    "python": frozenset({"CancelledError"}),
    "csharp": frozenset({"OperationCanceledException", "TaskCanceledException"}),
}
# A docstring or comment that states the handler's fallback: "best-effort",
# "never raises", "returns None on failure", "falls back to X if it cannot".
# The failure condition must follow within one clause, so "returns X if the key
# exists; raises on error" and "does not raise ValueError" do not match.
_FALLBACK_DOC_RE = re.compile(
    r"best[- ]effort"
    r"|\b(?:never|does\s+not|doesn't|will\s+not|won't)\s+raises?"
    r"(?=\s*(?:[.;,]|$|(?:an?|any)\s+(?:exception|error)))"
    r"|\b(?:returns?|returning|fall(?:s|ing)?\s+back)\b[^.;,]{0,60}?"
    r"(?:\b(?:on|upon)\s+(?:any\s+)?(?:failure|errors?|exceptions?)\b"
    r"|\b(?:if|when)\s+(?:\w+\s+){0,4}?(?:fails?|failed|cannot|can't|could\s+not|couldn't)\b)",
    re.IGNORECASE | re.MULTILINE,
)


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
    if target.type == "identifier" and _eh_text(target) in PY_CATCH_ALL:
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
    return is_catch_all and name != PY_CATCH_ALL[0]


def _eh_csharp_caught_types(clause: Node) -> list[Node]:
    decl = next((c for c in clause.children if c.type == "catch_declaration"), None)
    typ = decl.child_by_field_name("type") if decl is not None else None
    return [typ] if typ is not None else []


def _eh_caught_types(clause: Node, language: str) -> list[Node]:
    """The type nodes a Python ``except`` or C# ``catch`` clause names."""
    if language == "csharp":
        return _eh_csharp_caught_types(clause)
    types: list[Node] = []
    for node in _eh_named(clause):
        if node.type in _EH_BLOCK_KINDS or node.type == "comment":
            continue
        if node.type == "as_pattern" and _eh_named(node):
            node = _eh_named(node)[0]
        # ``(A, B)``, or the parenthesis-free ``except A, B:`` of Python 3.14.
        if node.type in ("tuple", "parenthesized_expression", "expression_list"):
            types += _eh_named(node)
        else:
            types.append(node)
    return types


def _eh_catches_only_cancellation(clause: Node, language: str) -> bool:
    """``except asyncio.CancelledError`` / C# ``catch (OperationCanceledException)``."""
    names = _CANCEL_TYPES.get(language)
    if names is None:
        return False
    types = _eh_caught_types(clause, language)
    return bool(types) and all(_eh_text(t).rsplit(".", 1)[-1] in names for t in types)


def _eh_docstring(func: Node) -> str:
    body = func.child_by_field_name("body")
    first = _eh_named(body)[0] if body is not None and _eh_named(body) else None
    if first is None or first.type != "expression_statement":
        return ""
    inner = _eh_named(first)
    return _eh_text(inner[0]) if inner and inner[0].type == "string" else ""


def _eh_comments_above(stmt: Node) -> list[str]:
    """The run of comments directly above *stmt*."""
    sib = stmt.prev_sibling
    if sib is None and stmt.parent is not None:
        # A comment opening a block parses as a sibling of that block.
        sib = stmt.parent.prev_sibling
    texts: list[str] = []
    while sib is not None and sib.type == "comment":
        texts.append(_eh_text(sib))
        sib = sib.prev_sibling
    return texts


def _eh_enclosing_docstring(node: Node) -> str:
    cur: Node | None = node
    while cur is not None and cur.type not in ("function_definition", "class_definition"):
        cur = cur.parent
    return _eh_docstring(cur) if cur is not None and cur.type == "function_definition" else ""


def _eh_fallback_doc_texts(clause: Node, block: Node | None) -> list[str]:
    """Python text that can document a handler's fallback: comments on the
    ``except`` line and in its body, comments directly above the ``try``, and
    the enclosing function's docstring."""
    nodes = list(clause.children) + (list(block.children) if block is not None else [])
    texts = [_eh_text(c) for c in nodes if c.type == "comment"]
    if clause.parent is not None:
        texts += _eh_comments_above(clause.parent)
    texts.append(_eh_enclosing_docstring(clause))
    return texts


def _eh_documented_fallback(clause: Node, block: Node | None) -> bool:
    """The handler ends in ``return`` and its fallback is documented."""
    real = _eh_real_stmts(block, "python") if block is not None else []
    if not real or real[-1].type != "return_statement":
        return False
    return any(_FALLBACK_DOC_RE.search(t) for t in _eh_fallback_doc_texts(clause, block))


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
        return mac is not None and _eh_text(mac) in RUST_PANIC_MACROS
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
    """True when the enclosing Java or Apex try block's last real statement is a
    ``fail(...)`` call, or Apex's ``System.assert(false, ...)``: the test expects
    the throw, and the empty catch is where it lands."""
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
    if name_node is None:
        return False
    if language == "apex":
        return _eh_apex_expects_throw(inv, _eh_text(name_node).lower())
    return _eh_text(name_node) == "fail"


def _eh_apex_expects_throw(inv: Node, name: str) -> bool:
    """``Assert.fail(...)`` or ``System.assert(false, ...)``, any case."""
    if name == "fail":
        return True
    args = inv.child_by_field_name("arguments")
    first = _eh_named(args)[0] if args is not None and _eh_named(args) else None
    return name == "assert" and first is not None and _eh_text(first).lower() == "false"


# The node types ``_eh_rust_hit`` / ``_eh_go_hit`` can fire on.
_EH_RUST_KINDS = frozenset({"call_expression", "macro_invocation"})
_EH_GO_KINDS = frozenset({"if_statement", "short_var_declaration", "assignment_statement"})

_NOQA_RE = re.compile(r"#\s*noqa(?::\s*([A-Za-z0-9,\s]+))?", re.IGNORECASE)


def _eh_noqa_codes(clause: Node) -> set[str] | None:
    """Return uppercase noqa codes on the except clause line, or None if absent.

    Returns an empty set `set()` for blanket `# noqa`.
    Only trailing comments on the except clause line (before the block) count.
    """
    start_row = clause.start_point[0]
    for child in clause.children:
        if child.type == "comment" and child.start_point[0] == start_row:
            text = _eh_text(child).strip()
            m = _NOQA_RE.search(text)
            if m:
                codes_raw = m.group(1)
                if codes_raw is None or not codes_raw.strip():
                    return set()
                return {c.upper() for c in re.split(r"[,\s]+", codes_raw.strip()) if c}
    return None


def _eh_node_kinds(language: str, lmap: LanguageNodeMap) -> frozenset[str]:
    """Every node type ``_eh_visit`` can emit a hit on, so the shared file scan
    skips the call for every other node."""
    kinds = set(lmap.catch_kinds)
    if language == "rust":
        kinds |= _EH_RUST_KINDS
    elif language == "go":
        kinds |= _EH_GO_KINDS
    return frozenset(kinds)


def _eh_swallow_allowed(noqa_codes: set[str] | None) -> bool:
    """Whether the clause's noqa codes leave a swallowed catch reportable."""
    return noqa_codes is None or (len(noqa_codes) > 0 and "S110" not in noqa_codes)


def _eh_broad_except_hit(
    node: Node, block: Node | None, noqa_codes: set[str] | None
) -> ErrorHandlingHit | None:
    """The bare/broad except hit for a Python clause that does not re-raise,
    unless its noqa codes suppress that kind or, for ``except Exception``, the
    fallback it returns is documented."""
    # ``except:`` / ``except BaseException:`` also swallow
    # KeyboardInterrupt & SystemExit; ``except Exception:`` cannot.
    is_base = _eh_catches_base(node)
    kind = "bare_except" if is_base else "broad_except"
    suppress = noqa_codes is not None and (
        len(noqa_codes) == 0
        or (kind == "broad_except" and "BLE001" in noqa_codes)
        or (kind == "bare_except" and bool(noqa_codes & {"BLE001", "E722"}))
    )
    if suppress or (kind == "broad_except" and _eh_documented_fallback(node, block)):
        return None
    return ErrorHandlingHit(kind, node.start_point[0] + 1)


def _eh_swallow_expected(node: Node, language: str) -> bool:
    """An empty handler that is the intended landing spot: a Java/Apex test's
    expected throw, or a caught cancellation."""
    if language in ("java", "apex"):
        return _eh_java_try_ends_in_fail(node, language)
    return _eh_catches_only_cancellation(node, language)


def _eh_catch_visit(node: Node, language: str, hits: list[ErrorHandlingHit]) -> None:
    """Append the swallowed-catch and bare/broad-except hits of one catch clause."""
    noqa_codes = _eh_noqa_codes(node) if language == "python" else None
    block = _eh_find_body_block(node)
    swallowed_unsuppressed = _eh_swallow_allowed(noqa_codes)
    if (
        block is not None
        and _eh_body_is_swallowed(block, language)
        and swallowed_unsuppressed
        and not _eh_swallow_expected(node, language)
    ):
        hits.append(ErrorHandlingHit("swallowed_catch", node.start_point[0] + 1))
    if (
        language == "python"
        and _eh_is_bare_except(node)
        and not _eh_body_ends_in_raise(block, language)
    ):
        hit = _eh_broad_except_hit(node, block, noqa_codes)
        if hit is not None:
            hits.append(hit)


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
        _eh_catch_visit(node, language, hits)
    elif language == "rust" and _eh_rust_hit(node):
        # A panic-family macro aborts unconditionally; unwrap/expect converts
        # a Result/Option into a panic. Different claims → different kinds.
        # ``.unwrap()`` inside a ``#[test]`` is the intended failure signal.
        if not _eh_rust_in_test(node):
            _eh_rust_append(node, hits)
    elif language == "go" and _eh_go_hit(node):
        hits.append(ErrorHandlingHit("go_swallow", node.start_point[0] + 1))
