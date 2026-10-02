"""What a function's parameter list says about who owns it.

Three facts, read by ``primitive_obsession`` and nothing else:

- **constructor**: a constructor node (Java / C# ``constructor_declaration``),
  a conventional constructor name (``__init__``, ``init``, ``constructor``), or
  a function named like its enclosing type, which is how C++ and friends spell
  one (``Foo::Foo`` out of line).
- **signature fixed elsewhere**: the parameter list is set by something other
  than this declaration, so "group these parameters" is advice nobody can
  take here. An override (Java ``@Override``; ``override`` in C#, Kotlin,
  Swift, TypeScript), a C# explicit interface implementation, a Rust trait
  impl, a native or generated binding (``extern``, ``[DllImport]``,
  ``[LibraryImport]``, ``[LoggerMessage]`` whose parameters are the message
  template's holes),
  and a data-driven C# test (``[Theory]``, ``[DataRow]``, ``[TestCase]``...),
  whose arguments the runner supplies. In C#, so is a public or protected
  member of a public type: C# has ``internal`` for code shared inside an
  assembly, so ``public`` there is a published API that callers outside the
  repository compile against. Java has no such level (``public`` is how one
  package reaches another), so a Java ``public`` says nothing about who calls
  it and is not read. An implicit interface implementation (C# without
  ``override``, Java without ``@Override``) is not visible without types and
  is not caught.
- **primitive parameters**: how many parameters are declared as a scalar or a
  string. ``None`` when no parameter declares a type at all, which is every
  unannotated Python or JavaScript signature: there is nothing to count.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node

_CTOR_KINDS = frozenset({"constructor_declaration", "secondary_constructor", "init_declaration"})
_CTOR_NAMES = frozenset({"__init__", "init", "constructor"})

# A type declaration whose ``name`` a constructor repeats. Broader than any
# one language's ``class_kinds``, which leave out records, structs and enums.
_TYPE_DECL_RE = re.compile(r"class|struct|record|enum|interface|object")

_OVERRIDE_RE = re.compile(r"@(?:[\w.]+\.)?Override\b|\boverride\b")
_BINDING_RE = re.compile(
    r"\bextern\b|\[\s*(?:[\w.]+\.)?(?:DllImport|LibraryImport|LoggerMessage)\b"
)
_DATA_TEST_RE = re.compile(
    r"[\[,]\s*(?:[\w.]+\.)?(?:Theory|InlineData|MemberData|ClassData|DataRow|DataTestMethod"
    r"|DynamicData|TestCase|TestCaseSource)\b"
)
_CSHARP_TYPE_DECLS = frozenset(
    {
        "class_declaration",
        "struct_declaration",
        "record_declaration",
        "record_struct_declaration",
        "interface_declaration",
    }
)

# Scalar and string type names, across the typed languages the walker reads.
# Matched against each word of a parameter's type once modifiers, pointers,
# references, nullability and array brackets are stripped; a generic
# (``List<String>``) never matches. Win32 scalar typedefs are listed because
# C/C++ on Windows spells ints and strings that way.
_PRIMITIVE_TYPES = frozenset(
    {
        # Java / Kotlin / C# / Swift / Scala
        "boolean", "Boolean", "bool", "Bool", "byte", "Byte", "sbyte", "char", "Char",
        "Character", "short", "Short", "ushort", "int", "Int", "Integer", "uint", "UInt",
        "long", "Long", "ulong", "ULong", "float", "Float", "double", "Double",
        "decimal", "string", "String", "nint", "nuint",
        # C / C++
        "signed", "unsigned", "wchar_t", "char8_t", "char16_t", "char32_t", "size_t",
        "ssize_t", "ptrdiff_t", "intptr_t", "uintptr_t", "int8_t", "int16_t", "int32_t",
        "int64_t", "uint8_t", "uint16_t", "uint32_t", "uint64_t", "wstring",
        "string_view", "wstring_view", "BOOL", "BYTE", "WORD", "DWORD", "UINT", "ULONG",
        "LONG", "INT", "WCHAR", "LPCWSTR", "LPWSTR", "LPCSTR", "LPSTR", "PCWSTR", "PWSTR",
        # TypeScript / Python / Go / Rust
        "number", "bigint", "str", "bytes", "rune",
        "int8", "int16", "int32", "int64", "uint8", "uint16", "uint32", "uint64",
        "float32", "float64", "i8", "i16", "i32", "i64", "i128", "isize",
        "u8", "u16", "u32", "u64", "u128", "usize", "f32", "f64",
    }
)
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


def _enclosing_type_name(fn_node: Node) -> str | None:
    node = fn_node.parent
    while node is not None:
        if _TYPE_DECL_RE.search(node.type):
            name = node.child_by_field_name("name")
            if name is not None:
                return _text(name)
        node = node.parent
    return None


def is_constructor(fn_node: Node, name: str) -> bool:
    if fn_node.type in _CTOR_KINDS or name in _CTOR_NAMES:
        return True
    parts = name.split("::")
    if len(parts) >= 2 and parts[-1] == parts[-2]:
        return True
    return name == _enclosing_type_name(fn_node)


def _in_trait_impl(fn_node: Node) -> bool:
    node = fn_node.parent
    while node is not None and node.type != "impl_item":
        if node.type == "function_item":
            return False
        node = node.parent
    return node is not None and node.child_by_field_name("trait") is not None


def _csharp_access(node: Node) -> set[str]:
    return {_text(c) for c in node.children if c.type == "modifier"}


def _is_published(access: set[str]) -> bool:
    return bool(access & {"public", "protected"}) and "private" not in access


def _is_csharp_public_api(fn_node: Node) -> bool:
    """A public or protected member whose every enclosing type is public too."""
    if not _is_published(_csharp_access(fn_node)):
        return False
    node = fn_node.parent
    seen_type = False
    while node is not None:
        if node.type in _CSHARP_TYPE_DECLS:
            if not _is_published(_csharp_access(node)):
                return False
            seen_type = True
        node = node.parent
    return seen_type


def is_signature_fixed(fn_node: Node, head: str, language: str) -> bool:
    """Whether *fn_node*'s parameters are dictated by another declaration.

    *head* is the declaration text before the parameter list (modifiers,
    annotations, attributes), from ``ast_utils.declaration_head``.
    """
    if _OVERRIDE_RE.search(head) or _BINDING_RE.search(head):
        return True
    if any(c.type == "explicit_interface_specifier" for c in fn_node.children):
        return True
    if language == "csharp" and (_DATA_TEST_RE.search(head) or _is_csharp_public_api(fn_node)):
        return True
    return _in_trait_impl(fn_node)


def _param_type(param: Node) -> Node | None:
    declared = param.child_by_field_name("type")
    if declared is not None:
        return declared
    # Kotlin and Pascal keep the type as an unnamed child.
    return next(
        (c for c in param.named_children if c.type.endswith("type") or c.type == "type"),
        None,
    )


def _is_primitive(type_node: Node) -> bool:
    text = _text(type_node)
    if "<" in text or "(" in text:
        return False
    words = [
        w.rsplit("::", 1)[-1].rsplit(".", 1)[-1]
        for w in _TYPE_PUNCT_RE.sub(" ", text.lstrip(":")).split()
        if w not in _TYPE_QUALIFIERS
    ]
    return bool(words) and all(w in _PRIMITIVE_TYPES for w in words)


def _arity(param: Node) -> int:
    """Names one parameter node declares. Pascal declares ``A, B: Integer`` as
    one node, and ``_count_parameters`` counts its names."""
    if param.type != "declArg":
        return 1
    return sum(1 for c in param.children if c.type == "identifier")


def primitive_param_count(params: Node | None) -> int | None:
    """Parameters of *params* declared as a scalar or a string.

    ``None`` when no parameter in the list declares a type.
    """
    typed_params = [
        (param, type_node)
        for param in (params.named_children if params is not None else ())
        if param.type not in _NON_PARAMS and (type_node := _param_type(param)) is not None
    ]
    if not typed_params:
        return None
    return sum(_arity(param) for param, type_node in typed_params if _is_primitive(type_node))
