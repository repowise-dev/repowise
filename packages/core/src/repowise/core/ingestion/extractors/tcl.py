"""Tcl naming helpers for the declarative parser."""

from __future__ import annotations

from functools import lru_cache

from tree_sitter import Node


def tcl_leaf_name(raw_name: str, _node_type: str) -> str:
    """``::ns::f`` / ``ns::f`` -> ``f``: a proc's name, its namespace aside."""
    return unwrap_word(raw_name).rpartition("::")[2]


def tcl_call_name(raw_name: str, _node_type: str) -> str:
    """A command name as written, qualifier kept: the resolver reads it."""
    return unwrap_word(raw_name)


def unwrap_word(word: str) -> str:
    """A literal word without its surrounding braces or double quotes."""
    word = word.strip()
    if len(word) >= 2 and (word[0], word[-1]) in (("{", "}"), ('"', '"')):
        return word[1:-1].strip()
    return word


def _join_namespace(outer: str, inner: str) -> str:
    """*inner* resolved in *outer*: an absolute ``::a`` stands alone."""
    if inner.startswith("::"):
        return inner.strip(":")
    return "::".join(part for part in (outer, inner) if part)


def _namespace_eval_name(node: Node) -> str | None:
    """``a`` for a ``namespace eval a {..}`` node; None for any other namespace command."""
    words = next((c for c in node.named_children if c.type == "word_list"), None)
    args = words.named_children if words is not None else []
    if len(args) < 3 or args[0].text != b"eval" or args[-1].type != "braced_word":
        return None
    return unwrap_word((args[1].text or b"").decode("utf-8", "replace"))


def _eval_blocks(node: Node | None, stop: str | None = None) -> list[str]:
    """The ``namespace eval`` names around *node*, innermost first, up to a *stop* node type."""
    blocks: list[str] = []
    while node is not None and node.type != stop:
        if node.type == "namespace":
            name = _namespace_eval_name(node)
            if name is not None:
                blocks.append(name)
        node = node.parent
    return blocks


def tcl_call_namespace(site_node: Node) -> str | None:
    """The ``namespace eval`` blocks around a call, up to its proc; ``::a`` if absolute.

    A proc's body runs in the proc's namespace, so blocks outside it do not
    apply; the resolver joins this onto that namespace.
    """
    current, absolute = "", False
    for name in reversed(_eval_blocks(site_node.parent, stop="procedure")):
        absolute = absolute or name.startswith("::")
        current = _join_namespace(current, name)
    if not current:
        # ``namespace eval :: {..}`` runs in the global namespace.
        return "::" if absolute else None
    return f"::{current}" if absolute else current


def tcl_scope(proc_namespace: str | None, call_namespace: str | None) -> str | None:
    """The namespace a call runs in: its proc's, moved by the blocks around the call."""
    if call_namespace is None:
        return proc_namespace
    return _join_namespace(proc_namespace or "", call_namespace) or None


def _execution_namespace(node: Node | None, base: str = "") -> str:
    """The namespace code at *node* runs in; ``""`` for the global one.

    Inside a proc that is the proc's own namespace, moved by the
    ``namespace eval`` blocks between it and *node*; outside any proc, the
    blocks move it from *base*, the namespace in which the file was sourced.
    """
    blocks: list[str] = []
    while node is not None and node.type != "procedure":
        if node.type == "namespace" and (name := _namespace_eval_name(node)) is not None:
            blocks.append(name)
        node = node.parent
    current = base
    if node is not None:
        name_node = node.child_by_field_name("name")
        raw = (name_node.text or b"").decode("utf-8", "replace") if name_node else ""
        current = tcl_proc_namespace(node, raw, base) or ""
    for name in reversed(blocks):
        current = _join_namespace(current, name)
    return current


def tcl_proc_namespace(def_node: Node, raw_name: str, base: str = "") -> str | None:
    """The namespace a ``proc`` defines its command in; None for the global one.

    The ``namespace eval`` blocks around it set the current namespace, and a
    qualified name (``proc a::f`` relative, ``proc ::x::f`` absolute) moves
    the command from there. *base* is the namespace in which the file was
    sourced. ``namespace eval`` bodies built at run time (``namespace eval
    $ns``) are read literally.
    """
    current = _execution_namespace(def_node.parent, base)
    name = unwrap_word(raw_name)
    qualifier = name.rpartition("::")[0]
    if name.startswith("::"):
        qualifier = "::" + qualifier.lstrip(":")
    namespace = _join_namespace(current, qualifier) if qualifier else current
    return namespace or None


def is_tcl_switch_arm(command: Node) -> bool:
    """True for a ``command`` the grammar made of a ``switch`` arm's pattern.

    The grammar parses a braced ``switch`` body as commands, so each arm
    (``pattern { body }``) reads as a command named by its pattern.
    """
    return _owning_switch(command) is not None


# ``switch`` options that take the next word as their value.
_SWITCH_VALUED_OPTIONS = frozenset({b"-matchvar", b"-indexvar"})


def _is_switch(node: Node) -> bool:
    name = node.child_by_field_name("name") if node.type == "command" else None
    return name is not None and name.text == b"switch"


def _switch_operands(switch: Node) -> list[Node]:
    """The words after a ``switch``'s options and the string it matches.

    Options run up to ``--`` or the first word not starting with ``-``. What
    is left is one braced list of every arm, or each arm as its own words
    (``switch $x a {..} b {..}``).
    """
    words = switch.child_by_field_name("arguments")
    args = words.named_children if words is not None else []
    i = 0
    while i < len(args) and (args[i].text or b"").startswith(b"-"):
        option = args[i].text
        i += 2 if option in _SWITCH_VALUED_OPTIONS else 1
        if option == b"--":
            break
    return args[i + 1 :]


def _switch_list_body(switch: Node) -> Node | None:
    """The one braced ``{pattern body ...}`` list of a ``switch``; None for separate words."""
    operands = _switch_operands(switch)
    if len(operands) == 1 and operands[0].type == "braced_word":
        return operands[0]
    return None


def _owning_switch(command: Node) -> Node | None:
    """The ``switch`` whose arm list parsed *command* out of an arm's words."""
    body = command.parent
    words = body.parent if body is not None else None
    owner = words.parent if words is not None else None
    if body is None or body.type != "braced_word" or owner is None or not _is_switch(owner):
        return None
    listed = _switch_list_body(owner)
    return owner if listed is not None and listed.id == body.id else None


def tcl_switch_arm_bodies(switch: Node) -> list[Node]:
    """The body of each arm of a ``switch``, fall-through arms folded.

    Arms are read as ``pattern body`` pairs: from separate words, or from a
    braced list. The grammar parses that list as script, so a pattern such as
    ``set`` or ``proc`` reshapes it; its text is split into Tcl words instead
    and each body mapped back to the node spanning it. A body may be a bare
    word (``switch $a x p``); a ``-`` body falls through to the next arm and
    is not an arm of its own.
    """
    body = _switch_list_body(switch)
    if body is None:
        return [arm for arm in _switch_operands(switch)[1::2] if arm.text != b"-"]
    spans: dict[tuple[int, int], Node] = {}
    stack = list(reversed(body.named_children))
    while stack:
        node = stack.pop()
        spans.setdefault((node.start_byte, node.end_byte), node)
        stack.extend(reversed(node.named_children))
    text = body.text or b""
    words = _tcl_words(text[1:-1], body.start_byte + 1)
    return [
        spans[span]
        for span in words[1::2]
        if span in spans and text[span[0] - body.start_byte : span[1] - body.start_byte] != b"-"
    ]


def _tcl_words(text: bytes, base: int) -> list[tuple[int, int]]:
    """The ``(start, end)`` byte spans of the Tcl words in *text*, offset by *base*."""
    words: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        if text[i : i + 3] == b"\\\r\n":
            i += 3
            continue
        if text[i : i + 2] == b"\\\n":
            i += 2
            continue
        if text[i : i + 1].isspace():
            i += 1
            continue
        end = _word_end(text, i)
        words.append((base + i, base + end))
        i = end
    return words


def _word_end(text: bytes, i: int) -> int:
    """Where the Tcl word starting at *i* ends: a balanced ``{..}``, a ``"..."``, or bare."""
    opener = text[i : i + 1]
    if opener == b"{":
        return _braced_end(text, i)
    if opener == b'"':
        end = i + 1
        while end < len(text) and text[end : end + 1] != b'"':
            end += 2 if text[end : end + 1] == b"\\" else 1
        return min(end + 1, len(text))
    end = i
    while end < len(text) and not text[end : end + 1].isspace():
        if text[end : end + 2] == b"\\\n" or text[end : end + 3] == b"\\\r\n":
            break
        end += 2 if text[end : end + 1] == b"\\" else 1
    return min(end, len(text))


def _braced_end(text: bytes, i: int) -> int:
    """Just past the ``}`` closing the ``{`` at *i*."""
    depth = 0
    while i < len(text):
        char = text[i : i + 1]
        if char == b"\\":
            i += 2
            continue
        depth += (char == b"{") - (char == b"}")
        i += 1
        if depth == 0:
            return i
    return len(text)


@lru_cache(maxsize=32)
def _arm_ids(switch: Node) -> frozenset[int]:
    """The ids of *switch*'s arm bodies, read once per switch, not once per arm."""
    return frozenset(arm.id for arm in tcl_switch_arm_bodies(switch))


def is_tcl_switch_arm_body(node: Node) -> bool:
    """True for the body of one arm of a ``switch``.

    A body sits at most five levels under its switch: in a braced arm list the
    grammar wraps it in a command and the list's own word list.
    """
    if not node.is_named:
        return False
    owner = node.parent
    for _ in range(5):
        if owner is None:
            return False
        if _is_switch(owner):
            return node.id in _arm_ids(owner)
        owner = owner.parent
    return False
