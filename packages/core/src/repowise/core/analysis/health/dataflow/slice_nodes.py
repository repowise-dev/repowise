"""Tree walkers the Extract Method slicer reads statement structure with."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from ..complexity.languages import LanguageNodeMap


def _function_lines(fn_node: Node) -> list[str]:
    """Source rows indexed by absolute row, rebuilt from the function's own text.

    Lets the walker's NLOC rule (``_code_line_numbers``) run here without the
    file's bytes; rows above the function are never read, so they stay empty.
    """
    text = (fn_node.text or b"").decode("utf-8", errors="replace")
    return [""] * fn_node.start_point[0] + text.splitlines()


def _holds_a_named_nested_function(span: list[Node], lmap: LanguageNodeMap) -> bool:
    """True when the span contains a nested function that binds its own name.

    Def/use does not descend into nested scopes, so a sibling closure calling
    that helper is invisible; lifting the declaration would strand the call.
    An anonymous lambda bound to a local is a value and follows the IN/OUT rules.
    """
    kinds = lmap.function_kinds
    if not kinds:
        return False
    for st in span:
        stack = [st]
        while stack:
            node = stack.pop()
            if node.type in kinds and node.child_by_field_name("name") is not None:
                return True
            stack.extend(node.children)
    return False


def _receiver_root(node: Node) -> str | None:
    """The base name a call is made on: ``entries`` in ``entries.remove(x)``.

    Only the root counts. Matching any name in the callee read
    ``existing.pages.push(page)`` as touching the ``pages`` the loop iterates,
    when the receiver is ``existing`` and the collision is in a member name.
    """
    cur = node
    while True:
        nxt = None
        for field in ("object", "operand", "value", "argument", "function"):
            child = cur.child_by_field_name(field)
            if child is not None:
                nxt = child
                break
        if nxt is None:
            break
        cur = nxt
    if not cur.children and cur.type.endswith("identifier"):
        text = cur.text
        return text.decode("utf-8", "replace") if text else None
    return None


# Fields a loop header binds through. Read structurally: a line-range test
# cannot tell the header's binder from a variable reassigned on the body's
# first line, and getting that wrong drops the iterated collection out of the
# carried set, which is exactly the name the mutation check exists to catch.
_BINDER_FIELDS = ("left", "pattern", "declarator", "name")


def _loop_header_names(loop: Node, lmap: LanguageNodeMap) -> set[str]:
    """Names the loop header reads, minus the ones it binds (the loop variable)."""
    body = loop.child_by_field_name("body")
    names: set[str] = set()
    # The binder is a field of the loop itself in most grammars, and of a
    # header clause it wraps in Go's ``range``.
    bound: set[str] = _binder_names(loop)
    for child in loop.children:
        if body is not None and child.id == body.id:
            continue
        names |= _identifiers(child)
        bound |= _binder_names(child)
    return names - bound


def _binder_names(node: Node) -> set[str]:
    """Identifiers in the binding position of *node* or of a header clause it
    wraps (Go nests its range clause inside the ``for``)."""
    out: set[str] = set()
    for field in _BINDER_FIELDS:
        target = node.child_by_field_name(field)
        if target is not None:
            out |= _identifiers(target)
    if not out:
        for child in node.named_children:
            for field in _BINDER_FIELDS:
                target = child.child_by_field_name(field)
                if target is not None:
                    out |= _identifiers(target)
    return out


def _identifiers(node: Node) -> set[str]:
    out: set[str] = set()
    stack = [node]
    while stack:
        cur = stack.pop()
        if not cur.children and cur.type.endswith("identifier"):
            text = cur.text
            if text:
                out.add(text.decode("utf-8", "replace"))
        elif not cur.children and cur.type == "name":
            text = cur.text
            if text:
                s = text.decode("utf-8", "replace")
                out.add(s[1:] if s.startswith("$") else s)
        stack.extend(cur.children)
    return out


def _descend(root: Node, kinds: frozenset[str], skip: frozenset[str]) -> list[Node]:
    found: list[Node] = []
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur.type in kinds:
            found.append(cur)
        for child in cur.children:
            if child.type in skip:
                continue
            stack.append(child)
    return found


def _unwrap_container(node: Node, block_kinds: frozenset[str]) -> Node:
    """Descend through a single nested statement-container (Go's
    ``block`` -> ``statement_list``) to the node whose named children are the
    actual statements; returns *node* unchanged when it is already that node."""
    cur = node
    while True:
        named = cur.named_children
        if len(named) == 1 and named[0].type in block_kinds:
            cur = named[0]
        else:
            return cur


def all_blocks(
    fn_node: Node,
    block_kinds: frozenset[str],
    scope_kinds: frozenset[str],
    loop_kinds: frozenset[str],
) -> list[tuple[Node, Node | None]]:
    """Every statement container in the function (body + nested), excluding the
    bodies of nested functions / lambdas, each paired with the innermost loop
    enclosing it (``None`` at loop-free depth)."""
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    blocks: list[tuple[Node, Node | None]] = []
    stack: list[tuple[Node, Node | None]] = [(body, None)]
    while stack:
        node, loop = stack.pop()
        if node.type in block_kinds:
            blocks.append((node, loop))
        is_loop = node.type in loop_kinds
        body = node.child_by_field_name("body") if is_loop else None
        for child in node.children:
            if child.type in scope_kinds:
                continue
            # Only the loop's body repeats. A ``for ... else`` clause runs once.
            in_loop = node if (is_loop and body is not None and child.id == body.id) else loop
            stack.append((child, in_loop))
    return blocks


def _exit_macros(lmap: LanguageNodeMap) -> tuple[frozenset[str], frozenset[str]]:
    """The macro node kinds and the macro names that exit the function."""
    return lmap.exit_macro_kinds, lmap.exit_macro_names


def _awaits(lmap: LanguageNodeMap) -> tuple[frozenset[str], frozenset[str]]:
    """The await tokens, and the nodes that own the awaits inside them."""
    return lmap.await_kinds, lmap.await_scope_kinds
