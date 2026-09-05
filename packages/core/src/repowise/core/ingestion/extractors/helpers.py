"""Shared AST helpers used by extractor modules."""

from __future__ import annotations

from tree_sitter import Node


def node_text(node: Node | None, src: str) -> str:
    """Return the text of a tree-sitter *node*, or ``""`` if *node* is None."""
    if node is None:
        return ""
    if node.text is not None:
        return node.text.decode("utf-8", errors="replace")
    return src[node.start_byte : node.end_byte]


def extract_go_receiver_type(receiver_text: str) -> str | None:
    """Extract 'Calculator' from '(c *Calculator)' or '(c Calculator)'.

    A Go receiver is ``name Type`` or a bare ``Type``, so the type is always
    the last part. Selecting it by position rather than by an uppercase first
    letter is what lets an unexported receiver — ``func (s *startEnd) add()`` —
    carry a parent at all; the export convention says nothing about whether a
    name is a type.
    """
    text = receiver_text.strip("() ")
    parts = text.split()
    if not parts:
        return None
    clean = parts[-1].lstrip("*")
    return clean or None


def refine_go_type_kind(type_spec_node: Node, src: str) -> str:
    """Refine the generic 'struct' kind for Go type_spec nodes."""
    type_node = type_spec_node.child_by_field_name("type")
    if type_node is None:
        return "struct"
    type_text = node_text(type_node, src).strip()
    if type_text.startswith("struct"):
        return "struct"
    if type_text.startswith("interface"):
        return "interface"
    return "type_alias"


def refine_kotlin_class_kind(class_node: Node) -> str:
    """Refine 'class' kind for Kotlin class_declaration nodes.

    In tree-sitter-kotlin v1.x, interfaces and enum classes all use
    ``class_declaration`` — the keyword child (``class``, ``interface``,
    ``enum``) distinguishes them.
    """
    for child in class_node.children:
        if child.type == "interface":
            return "interface"
        if child.type == "enum":
            return "enum"
    return "class"


# Elixir definition keyword -> SymbolKind. Every definition is a `call` node,
# so the node type says nothing; the keyword in the call's target says all.
_ELIXIR_DEFINITION_KINDS = {
    "defmodule": "module",
    "defprotocol": "interface",
    "defimpl": "impl",
    "def": "function",
    "defp": "function",
    "defdelegate": "function",
    "defmacro": "macro",
    "defmacrop": "macro",
    "defguard": "macro",
    "defguardp": "macro",
}


def refine_elixir_call_kind(call_node: Node, src: str) -> str:
    """Refine the placeholder ``module`` kind for an Elixir ``call`` node.

    ``LANGUAGE_CONFIGS["elixir"]`` maps the one node type Elixir has to
    ``module`` rather than to a callable kind on purpose: a ``def`` nested in
    a ``defmodule`` has a ``call`` ancestor, so a callable mapping would make
    ``_has_callable_ancestor`` drop every function in every module. The real
    kind is read back here, after that filter has run.
    """
    target = call_node.child_by_field_name("target")
    if target is None:
        return "module"
    keyword = node_text(target, src).strip()
    return _ELIXIR_DEFINITION_KINDS.get(keyword, "module")


def refine_pascal_type_kind(decl_type_node: Node) -> str:
    """Refine the generic ``class`` kind for Pascal ``declType`` nodes.

    ``declType`` wraps class / record / object / interface / class-helper
    / enum / set / array / plain-alias in one node shape (see
    ``languages/specs/pascal.py``'s spec docstring). Its ``type`` field is
    one of two shapes:

    * a class-like node directly -- ``declClass`` (covers *both* the
      ``class`` and ``record``/``object`` keyword forms; the grammar has
      no separate "record" node, so the first child's keyword token
      (``kRecord`` vs. ``kClass``/``kObject``) is what actually
      distinguishes them), ``declIntf``, or ``declHelper``;
    * a generic ``type`` wrapper around ``declEnum``, ``declSet``,
      ``declArray``, or a bare ``typeref`` (a plain alias, e.g.
      ``TMyInt = Integer;``).

    ``object`` and class-helper both collapse to ``"class"`` -- Pascal's
    pre-OOP ``object`` type and a class helper both declare members the
    same shape a class does, and neither has a closer match in
    :data:`~repowise.core.ingestion.models.SymbolKind`. ``set``/``array``/
    plain-alias all collapse to ``"type_alias"`` for the same reason:
    none of them declare members of their own, so ``"class"`` would be
    actively misleading.
    """
    type_node = decl_type_node.child_by_field_name("type")
    if type_node is None:
        return "class"
    if type_node.type == "declClass":
        first_child = type_node.children[0] if type_node.children else None
        if first_child is not None and first_child.type == "kRecord":
            return "struct"
        return "class"
    if type_node.type == "declIntf":
        return "interface"
    if type_node.type == "declHelper":
        return "class"
    if type_node.type == "type":
        inner = next(iter(type_node.named_children), None)
        if inner is not None and inner.type == "declEnum":
            return "enum"
        return "type_alias"
    return "class"


def fsharp_type_name(simple_type_node: Node, src: str) -> str | None:
    """The bare name of an F# ``simple_type``, qualifier dropped.

    ``inherit System.Exception()`` names the same type as ``inherit
    Exception()`` and F# writes the type name last, so the last segment of
    the dotted path is the name the symbol index is keyed by. Shared by the
    heritage extractor and the type-reference head walk, which ask the same
    question of the same node.
    """
    head = simple_type_node
    if head.type == "simple_type":
        head = next(iter(head.named_children), None)
        if head is None:
            return None
    if head.type == "long_identifier":
        idents = [c for c in head.named_children if c.type == "identifier"]
        if not idents:
            return None
        head = idents[-1]
    if head.type != "identifier":
        return None
    return node_text(head, src).strip() or None


def refine_fsharp_type_kind(anon_type_defn_node: Node) -> str:
    """Tell an F# interface apart from a class inside ``anon_type_defn``.

    The grammar gives classes, structs and interfaces the same node: an
    interface is written ``type IFoo = abstract member Bar: ...`` with no
    constructor and nothing but abstract members. Those two facts together
    are what F# itself compiles to an interface, so both are required; a
    class with one abstract member and a constructor stays a class.
    """
    members = [
        node
        for child in anon_type_defn_node.named_children
        for node in ([child] if child.type == "member_defn" else child.named_children)
        if node.type == "member_defn"
    ]
    if not members:
        return "class"
    if any(child.type == "primary_constr_args" for child in anon_type_defn_node.named_children):
        return "class"
    for member in members:
        if not any(child.type == "abstract" for child in member.children):
            return "class"
    return "interface"


def clean_string_literal(text: str) -> str:
    """Strip quote characters from a Python string literal."""
    text = text.strip()
    for triple in ('"""', "'''"):
        if text.startswith(triple) and text.endswith(triple) and len(text) >= 6:
            return text[3:-3].strip()
    for q in ('"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2:
            return text[1:-1].strip()
    return text


def find_preceding_jsdoc(node: Node, src: str) -> str | None:
    """Return the JSDoc comment immediately before *node*, if any."""
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type == "comment":
        text = node_text(prev, src).strip()
        if text.startswith("/**"):
            return clean_jsdoc(text)
    return None


def find_preceding_block_comment(node: Node, src: str, prefix: str) -> str | None:
    """Return the block comment immediately before *node* that starts with *prefix*."""
    parent = node.parent
    if parent is None:
        return None
    siblings = list(parent.children)
    idx = next((i for i, s in enumerate(siblings) if s.id == node.id), -1)
    if idx <= 0:
        return None
    prev = siblings[idx - 1]
    if prev.type in ("block_comment", "comment"):
        text = node_text(prev, src).strip()
        if text.startswith(prefix):
            return clean_jsdoc(text)
    return None


def clean_jsdoc(text: str) -> str:
    """Strip JSDoc / block-comment delimiters and leading asterisks."""
    lines = text.splitlines()
    cleaned: list[str] = []
    for line in lines:
        line = line.strip().lstrip("/*").lstrip()
        if line:
            cleaned.append(line)
    return "\n".join(cleaned).strip()
