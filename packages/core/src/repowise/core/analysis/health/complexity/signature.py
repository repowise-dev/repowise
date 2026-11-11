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
  function *named* ``override`` is not one. A Python method whose class's
  base, defined in the same file, declares the same name is an override too.
- **typed / scalar parameters**: how many parameters declare a type, and how
  many of those are a scalar or a string. An untyped parameter is in neither
  count.
- **bound parameters**: parameters whose own annotation says a framework fills
  them (``@PathVariable``), read the same way as a declaration's markers. They
  stay in the declared count and leave the typed and scalar ones.
- **keyword-only parameters**: Python parameters after ``*`` or ``*args``,
  and Dart ``{named}`` parameters. Every call site names them, so they carry
  no argument-order hazard. Python's ``*args`` / ``**kwargs`` are no
  parameters at all: they collect what is left over.
- **defaulted parameters**: Kotlin and Scala parameters with a default value,
  which a call site leaves out or names.
"""

from __future__ import annotations

import re
from itertools import pairwise
from typing import TYPE_CHECKING

from .ast_utils import (
    _dart_signature_sibling,
    _inner_declarator,
    _parameter_nodes,
    is_rest_parameter,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

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
    {"keyword_separator", "positional_separator", "self_parameter"}
)
# C and C++ spell indirection on the declarator, not the type.
_POINTER_DECLARATORS = frozenset(
    {"pointer_declarator", "abstract_pointer_declarator", "array_declarator", "abstract_array_declarator"}
)
_REFERENCE_DECLARATORS = frozenset({"reference_declarator", "abstract_reference_declarator"})
# Character types whose pointer is a C string, unless a length follows it.
_C_CHARS = frozenset({"char", "wchar_t", "char8_t", "char16_t", "char32_t", "WCHAR", "TCHAR"})
# A buffer's length: an integer type, named as a size or a count.
_INTEGER_RE = re.compile(r"u?int\d*(_t)?|s?size_t|ptrdiff_t|u?intptr_t|long|short|signed|unsigned|U?INT\d*|U?LONG|D?WORD|SIZE_T")
_LENGTH_NAME_RE = re.compile(r"(?i:len|size|count|cnt|bytes|^num|^cap(acity)?$)|Num|^n$|^(cch|cb)[A-Z]")


def _text(node: Node) -> str:
    return (node.text or b"").decode("utf-8", errors="replace")


def _marker_words(node: Node) -> set[str]:
    """Leaf words of *node*'s marker containers, arguments and strings left out."""
    stack = [c for c in node.children if c.type in _MARKER_CONTAINERS]
    # C++ keeps ``override`` on the declarator, after the parameters.
    declarator = node.child_by_field_name("declarator")
    if declarator is not None:
        stack += [c for c in declarator.children if c.type in _MARKER_CONTAINERS]
    return _leaf_words(stack)


def _leaf_words(stack: list[Node]) -> set[str]:
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
    if fn_node.type == "function_body":
        words |= _leaf_words(_dart_annotations(fn_node))
    return words


def _dart_annotations(body: Node) -> list[Node]:
    """Dart keeps a member's ``@override`` as a sibling before its signature,
    which is itself the sibling before the body."""
    found: list[Node] = []
    sig = body.prev_named_sibling
    node = sig.prev_named_sibling if sig is not None else None
    while node is not None and node.type == "annotation":
        found.append(node)
        node = node.prev_named_sibling
    return found


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
    # Dart: the kind is on the signature beside the body (``A.named`` is one).
    sig = _dart_signature_sibling(fn_node) if fn_node.type == "function_body" else None
    if sig is not None and sig.type in lmap.ctor_kinds:
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
    if _is_delegate_callback(fn_node, lmap):
        return True
    return (
        _in_trait_impl(fn_node, lmap)
        or _is_public_api(fn_node, lmap)
        or _in_fixed_abi(fn_node, lmap)
        or _overrides_local_base(fn_node, lmap)
    )


def _in_fixed_abi(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """Inside a block whose ABI fixes the parameter list (C++ ``extern "C"``)."""
    if not lmap.fixed_abi_kinds:
        return False
    node = fn_node.parent
    # A member function keeps C++ linkage even inside an ``extern "C"`` block.
    stops = lmap.function_kinds | lmap.class_kinds
    while node is not None and node.type not in stops:
        if node.type in lmap.fixed_abi_kinds:
            # ``extern "C++" {`` re-enters C++ linkage inside an ``extern "C"`` header.
            value = node.child_by_field_name("value")
            return value is None or _text(value) != '"C++"'
        node = node.parent
    return False


def _is_delegate_callback(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """A Swift delegate or data source callback, which Cocoa names after the
    object calling it, passed first and unlabelled (``func urlSession(_
    session: URLSession, task: ...)``, ``func tableView(_ tableView:
    UITableView, ...)``). The protocol it implements owns the signature, and
    its type says so: an extension, or a type with a supertype clause."""
    if not _conforms_to_something(fn_node):
        return False
    first = next((c for c in fn_node.children if c.type == "parameter"), None)
    label = first.child_by_field_name("external_name") if first is not None else None
    if label is None or _text(label) != "_":
        return False
    type_node = _param_type(first)
    name = _field_text(fn_node, "name") or ""
    if type_node is None or _is_scalar(type_node, lmap.scalar_type_names):
        return False
    return _named_after(name, _text(type_node).rstrip("?!"))


def _named_after(name: str, caller: str) -> bool:
    """*name* starts with the word for *caller*: ``UITableView`` is
    ``tableView``, ``URLSession`` is ``urlSession``."""
    stems = {_lower_camel(caller), _lower_camel(_TYPE_PREFIX_RE.sub("", caller, count=1))}
    return any(
        name.startswith(stem) and (len(name) == len(stem) or name[len(stem)].isupper())
        for stem in stems
    )


_TYPE_PREFIX_RE = re.compile(r"^[A-Z]{2,3}(?=[A-Z][a-z])")


def _conforms_to_something(fn_node: Node) -> bool:
    body = fn_node.parent
    decl = body.parent if body is not None else None
    if decl is None or decl.type != "class_declaration":
        return False
    kind = decl.child_by_field_name("declaration_kind")
    return (kind is not None and _text(kind) == "extension") or any(
        c.type == "inheritance_specifier" for c in decl.children
    )


def _lower_camel(type_name: str) -> str:
    """``URLSession`` -> ``urlSession``, ``TableView`` -> ``tableView``."""
    run = len(type_name) - len(type_name.lstrip("ABCDEFGHIJKLMNOPQRSTUVWXYZ"))
    if 1 < run < len(type_name):
        run -= 1  # the last capital starts the next word
    return type_name[:run].lower() + type_name[run:]


def _is_inout(type_node: Node) -> bool:
    """A Swift ``inout`` parameter is the caller's variable, not a raw value."""
    modifiers = type_node.prev_named_sibling
    return modifiers is not None and modifiers.type == "parameter_modifiers" and (
        "inout" in _text(modifiers).split()
    )


def _class_of_method(fn_node: Node, lmap: LanguageNodeMap) -> Node | None:
    holder = fn_node
    if holder.parent is not None and holder.parent.type in lmap.decorated_definition_kinds:
        holder = holder.parent
    body = holder.parent
    if body is None or body.parent is None or body.parent.type not in lmap.class_kinds:
        return None
    return body.parent


def _undecorated(node: Node, lmap: LanguageNodeMap) -> Node:
    if node.type in lmap.decorated_definition_kinds:
        return node.child_by_field_name("definition") or node
    return node


def _defines(cls: Node, name: str, lmap: LanguageNodeMap) -> bool:
    body = cls.child_by_field_name("body")
    members = (_undecorated(c, lmap) for c in (body.named_children if body is not None else ()))
    return any(m.type in lmap.function_kinds and _field_text(m, "name") == name for m in members)


def _field_text(node: Node, field: str) -> str | None:
    child = node.child_by_field_name(field)
    return _text(child) if child is not None else None


def _module_classes(node: Node, lmap: LanguageNodeMap) -> dict[str | None, Node]:
    root = node
    while root.parent is not None:
        root = root.parent
    tops = (_undecorated(c, lmap) for c in root.named_children)
    return {_field_text(c, "name"): c for c in tops if c.type in lmap.class_kinds}


def _local_bases(cls: Node, module: dict[str | None, Node]) -> Iterator[Node]:
    """Every ancestor of *cls* named by a plain identifier and defined in *module*.

    A base must be defined above the class naming it: ``class Foo(Foo)`` that
    shadows an imported ``Foo`` names the import, not itself.
    """
    seen: set[str] = set()
    pending = [cls]
    while pending:
        current = pending.pop()
        bases = current.child_by_field_name("superclasses")
        for base in bases.named_children if bases is not None else ():
            name = _text(base)
            found = module.get(name) if base.type == "identifier" and name not in seen else None
            if found is not None and found.start_byte < current.start_byte:
                seen.add(name)
                pending.append(found)
                yield found


def _overrides_local_base(fn_node: Node, lmap: LanguageNodeMap) -> bool:
    """A Python method whose name a base class in the same module declares.

    Reads the grammar's ``superclasses`` field, which only Python has, and
    module-level classes only; a base imported from elsewhere is not followed.
    """
    cls = _class_of_method(fn_node, lmap)
    name = _field_text(fn_node, "name")
    if cls is None or name is None or cls.child_by_field_name("superclasses") is None:
        return False
    module = _module_classes(cls, lmap)
    return any(_defines(base, name, lmap) for base in _local_bases(cls, module))


def _param_type(param: Node) -> Node | None:
    declared = param.child_by_field_name("type")
    if declared is not None:
        return declared
    # Kotlin, Dart and Pascal keep the type as a child with no field name.
    return next((c for c in param.named_children if "type" in c.type), None)


def _is_scalar(type_node: Node, scalars: frozenset[str]) -> bool:
    if _is_inout(type_node):
        return False
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


def _indirection(param: Node, type_node: Node) -> str | None:
    """How a C or C++ parameter reaches its value, read off the declarator
    (``char *buf`` has type ``char``): ``None`` by value or by const
    reference, ``"text"`` for a plain character pointer (a C string),
    ``"buffer"`` for any other pointer or an array, ``"out"`` for a mutable
    reference. A buffer or an out-parameter is memory the caller lends, not a
    raw value."""
    decl = param.child_by_field_name("declarator")
    pointers = 0
    mutable_ref = False
    const = any(c.type == "type_qualifier" and _text(c) == "const" for c in param.children)
    while decl is not None:
        if decl.type in _POINTER_DECLARATORS:
            pointers += 1
        elif decl.type in _REFERENCE_DECLARATORS:
            # ``T&&`` takes ownership of a value; only ``T&`` writes back.
            mutable_ref = not const and all(c.type != "&&" for c in decl.children)
        decl = _inner_declarator(decl)
    if pointers == 1 and _text(type_node) in _C_CHARS:
        return "text"
    if pointers:
        return "buffer"
    return "out" if mutable_ref else None


def _is_length(param: Node, type_node: Node) -> bool:
    """An integer parameter named like the size of the buffer before it."""
    name = param.child_by_field_name("declarator")
    return (
        name is not None
        and name.type == "identifier"
        and any(_INTEGER_RE.fullmatch(w) for w in _text(type_node).split())
        and bool(_LENGTH_NAME_RE.search(_text(name)))
    )


def _param_row(param: Node, type_node: Node, lmap: LanguageNodeMap) -> list:
    """``[arity, can have a length, scalar, is a length]`` for one typed parameter."""
    held = _indirection(param, type_node)
    raw = _is_scalar(type_node, lmap.scalar_type_names)
    # A pointer to raw memory (``uint8_t *``, ``void *``) can have a length;
    # one to an object (``client *c``) is that object.
    sized = held in ("text", "buffer") and (raw or _text(type_node) == "void")
    scalar = raw and held in (None, "text")
    return [_arity(param), sized, scalar, held is None and _is_length(param, type_node)]


def typed_param_counts(params: Node | None, lmap: LanguageNodeMap) -> tuple[int, int]:
    """``(typed, scalar)``: parameters of *params* that declare a type, and the
    ones among them declared as a scalar or a string. ``(0, 0)`` for a language
    with no scalar vocabulary: its types cannot be read, so none count.

    A C or C++ buffer, out-parameter or mutable reference is no scalar
    (``_indirection``). A pointer followed by its length (``char *buf, size_t
    len``) is one value spelled in two parameters, so neither is a scalar."""
    if not lmap.scalar_type_names or params is None:
        return 0, 0
    rows: list[list] = []
    for param in _parameter_nodes(params):
        if param.type in _NON_PARAMS or (type_node := _param_type(param)) is None:
            continue
        if not (_is_bound(param, lmap) or is_rest_parameter(param)):
            rows.append(_param_row(param, type_node, lmap))
    _pair_lengths(rows)
    return sum(r[0] for r in rows), sum(r[0] for r in rows if r[2])


def _pair_lengths(rows: list[list]) -> None:
    """A buffer and the length right after it are no scalars."""
    for before, row in pairwise(rows):
        if before[1] and row[3]:
            before[2] = row[2] = False


def _is_bound(param: Node, lmap: LanguageNodeMap) -> bool:
    if not lmap.bound_param_markers:
        return False
    words = _marker_words(param)
    # Kotlin keeps a parameter's annotations on the sibling before it.
    prev = param.prev_named_sibling
    if prev is not None and prev.type == "parameter_modifiers":
        words |= _leaf_words([prev])
    return bool(words & lmap.bound_param_markers) or _bound_by_call(param, lmap)


def _bound_by_call(param: Node, lmap: LanguageNodeMap) -> bool:
    """A Python parameter a framework fills through a marker call, as its
    default (``x: int = Query(0)``) or in its ``Annotated[...]`` type."""
    stack = [n for n in (param.child_by_field_name("value"), param.child_by_field_name("type")) if n]
    while stack:
        node = stack.pop()
        if node.type == "call":
            callee = _text(node.child_by_field_name("function") or node)
            if callee.rsplit(".", 1)[-1] in lmap.bound_param_markers:
                return True
        if node.type in ("type", "generic_type", "subscript", "type_parameter"):
            stack.extend(node.named_children)
    return False


def keyword_only_param_count(params: Node | None, lmap: LanguageNodeMap) -> int:
    """Python parameters after a bare ``*`` or ``*args`` (``**kwargs`` is no
    parameter, ``_count_parameters`` leaves it out), Ruby ``key:`` and Dart
    ``{named}`` ones.
    A framework-bound one is left out: ``bound_param_count`` has it."""
    if params is None:
        return 0
    count = _dart_named_count(params)
    seen_star = False
    for param in _parameter_nodes(params):
        if seen_star or param.type == "keyword_parameter":
            # Ruby ``key:`` parameters are named at every call site too.
            count += not (_is_bound(param, lmap) or is_rest_parameter(param))
        elif param.type == "keyword_separator" or (
            # Python's ``*args``; a Ruby splat leaves later parameters positional.
            param.type in ("list_splat_pattern", "typed_parameter") and is_rest_parameter(param)
        ):
            seen_star = True
    return count


def _dart_named_count(params: Node) -> int:
    """Dart parameters inside ``{...}``: every call site names them."""
    group = next((c for c in params.children if c.type == "optional_formal_parameters"), None)
    if group is None or group.child(0) is None or group.child(0).type != "{":
        return 0
    return sum(1 for c in group.named_children if c.type == "formal_parameter")


def defaulted_param_count(params: Node | None, lmap: LanguageNodeMap) -> int:
    """Kotlin and Scala parameters with a default value. A caller leaves them
    out or names them, so they carry no argument-order hazard. A
    framework-bound one is left out: ``bound_param_count`` has it."""
    if params is None:
        return 0
    return sum(
        1 for p in _parameter_nodes(params) if _has_default(p) and not _is_bound(p, lmap)
    )


def _has_default(param: Node) -> bool:
    if param.type != "parameter":
        return False
    # Kotlin keeps ``= value`` beside the parameter, Scala inside it.
    sibling = param.next_sibling
    return param.child_by_field_name("default_value") is not None or (
        sibling is not None and sibling.type == "=" and param.parent.type == "function_value_parameters"
    )


def bound_param_count(params: Node | None, lmap: LanguageNodeMap) -> int:
    """Parameters of *params* a framework binds by annotation."""
    if not lmap.bound_param_markers or params is None:
        return 0
    return sum(
        _arity(p) for p in params.named_children if p.type not in _NON_PARAMS and _is_bound(p, lmap)
    )
