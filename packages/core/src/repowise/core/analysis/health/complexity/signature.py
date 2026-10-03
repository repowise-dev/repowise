"""What a function's parameter list says about who owns it.

Facts read by ``primitive_obsession`` and nothing else. The per-language
knowledge (constructor kinds, the markers that fix a signature, the scalar type
names, C#'s public-API rule) lives on ``LanguageNodeMap``; this module only
reads the tree with it.

- **constructor**: a constructor node or conventional constructor name of the
  language, or a function named like its enclosing type, which is how C++ and
  Java spell one (``Foo::Foo`` out of line).
- **signature fixed elsewhere**: the parameter list is set by something other
  than this declaration, so "group these parameters" is advice nobody can take
  here. Read from the declaration's modifier, annotation, attribute and
  decorator nodes only, never from its name or from string arguments, so a
  function *named* ``override`` is not one.
- **typed / scalar parameters**: how many parameters declare a type, and how
  many of those are a scalar or a string. An untyped parameter is in neither
  count.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

    from .languages import LanguageNodeMap

# A type declaration whose ``name`` a constructor repeats. Broader than any
# one language's ``class_kinds``, which leave out records, structs and enums.
_TYPE_DECL_RE = re.compile(r"class|struct|record|enum|interface|object")

# Nodes holding a declaration's modifiers, annotations and attributes across
# the grammars, and the subtrees inside them that are arguments, not markers.
_MARKER_CONTAINERS = frozenset(
    {
        "modifiers",
        "modifier",
        "attribute_list",
        "override_modifier",
        "accessibility_modifier",
        "virtual_specifier",
        "decorator",
    }
)
_MARKER_ARGUMENTS = re.compile(r"argument|string|literal")

# Words that qualify a type without changing what it holds, nullability
# spelled as a union or as ``Optional[...]`` included.
_TYPE_QUALIFIERS = frozenset(
    {
        "const", "volatile", "final", "ref", "in", "out", "readonly", "scoped", "mut", "params",
        "None", "null", "undefined", "Optional",
    }
)
_TYPE_PUNCT_RE = re.compile(r"[*&?|\[\]]|'\w+")
# Nodes of a parameter list that are not parameters, as ``_count_parameters``
# skips them.
_NON_PARAMS = frozenset(
    {"comment", "keyword_separator", "positional_separator", "self_parameter"}
)


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _marker_words(node: Node) -> set[str]:
    """Leaf words of *node*'s marker containers, arguments and strings left out."""
    stack = [c for c in node.children if c.type in _MARKER_CONTAINERS]
    # C++ keeps ``override`` on the declarator, after the parameters.
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        stack += [c for c in declarator.children if c.type in _MARKER_CONTAINERS]
    words: set[str] = set()
    while stack:
        cur = stack.pop()
        if _MARKER_ARGUMENTS.search(cur.type):
            continue
        if cur.child_count == 0:
            words.add(_text(cur))
        stack.extend(cur.children)
    return words


def _declaration_markers(fn_node: Node, lmap: LanguageNodeMap) -> set[str]:
    words = _marker_words(fn_node)
    # Python keeps decorators on a wrapper beside the definition.
    if fn_node.parent is not None and fn_node.parent.type in lmap.decorated_definition_kinds:
        words |= _marker_words(fn_node.parent)
    return words


def _enclosing_type_name(fn_node: Node) -> str | None:
    node = fn_node.parent
    while node is not None:
        if _TYPE_DECL_RE.search(node.type):
            name = node.child_by_field_name("name")
            if name is not None:
                return _text(name)
        node = node.parent
    return None


def is_constructor(fn_node: Node, name: str, lmap: LanguageNodeMap) -> bool:
    if fn_node.type in lmap.ctor_kinds or name in lmap.ctor_names:
        return True
    parts = name.split("::")
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        return True
    return name == _enclosing_type_name(fn_node)


def is_trait_impl(node: Node, lmap: LanguageNodeMap) -> bool:
    """Whether *node* implements a trait (Rust ``impl Trait for T``): its
    members are the trait's, not its own."""
    return node.type in lmap.trait_impl_kinds and node.child_by_field_name("trait") is not None


def _in_trait_impl(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    node = fn_node.parent
    while node is not None and node.type not in lmap.trait_impl_kinds:
        if node.type in lmap.function_kinds:
            return False
        node = node.parent
    return node is not None and is_trait_impl(node, lmap)


def _is_published(node: Node, lmap: LanguageNodeMap) -> bool:
    access = _marker_words(node)
    return bool(access & lmap.public_api_modifiers) and "private" not in access


def _is_public_api(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """A published member whose every enclosing type is published too."""
    if not lmap.public_api_modifiers or not _is_published(fn_node, lmap):
        return False
    node = fn_node.parent
    seen_type = False
    while node is not None:
        if node.type in lmap.public_api_type_kinds:
            if not _is_published(node, lmap):
                return False
            seen_type = True
        node = node.parent
    return seen_type


def is_signature_fixed(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """Whether *fn_node*'s parameters are dictated by another declaration."""
    if _declaration_markers(fn_node, lmap) & lmap.fixed_signature_markers:
        return True
    if any(c.type in lmap.explicit_impl_kinds for c in fn_node.children):
        return True
    return _in_trait_impl(fn_node, lmap) or _is_public_api(fn_node, lmap)


def _param_type(param: Node) -> Node | None:
    declared = param.child_by_field_name("type")
    if declared is not None:
        return declared
    # Kotlin, Dart and Pascal keep the type as a child with no field name.
    return next((c for c in param.named_children if "type" in c.type), None)


def _is_scalar(type_node: Node, scalars: frozenset[str]) -> bool:
    text = _text(type_node)
    if "<" in text or "(" in text:
        return False
    words = [
        w.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        for w in _TYPE_PUNCT_RE.sub(" ", text.lstrip(":")).split()
        if w not in _TYPE_QUALIFIERS
    ]
    return bool(words) and all(w in scalars for w in words)


def _arity(param: Node) -> int:
    """Names one parameter node declares. Pascal declares ``A, B: Integer`` as
    one node, and ``_count_parameters`` counts its names."""
    if param.type != "declArg":
        return 1
    return sum(1 for c in param.children if c.type == "identifier")


def typed_param_counts(params: Node | None, lmap: LanguageNodeMap) -> tuple[int, int]:
    """``(typed, scalar)``: parameters of *params* that declare a type, and the
    ones among them declared as a scalar or a string. ``(0, 0)`` for a language
    with no scalar vocabulary: its types cannot be read, so none count."""
    typed = scalar = 0
    if not lmap.scalar_type_names or params is None:
        return typed, scalar
    for param in params.named_children:
        if param.type in _NON_PARAMS or (type_node := _param_type(param)) is None:
            continue
        arity = _arity(param)
        typed += arity
        if _is_scalar(type_node, lmap.scalar_type_names):
            scalar += arity
    return typed, scalar
