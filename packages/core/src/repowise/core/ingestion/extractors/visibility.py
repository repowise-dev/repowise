"""Per-language visibility determination functions.

Most languages can determine visibility from a symbol's name + modifier
text alone (the ``visibility_fn`` shape). C/C++ is the exception: its
visibility comes from surrounding AST context — ``public:`` / ``private:``
access specifier siblings inside a class body, ``static`` storage class
at file scope, or ``__declspec(dllexport)`` / GCC visibility attributes.
``refine_cpp_visibility`` handles that node-aware refinement; the
parser calls it after the generic ``visibility_fn`` for C/C++ files.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tree_sitter import Node


def py_visibility(name: str, _mods: list[str]) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder
    if name.startswith("_"):
        return "private"
    return "public"


def ts_visibility(_name: str, mods: list[str]) -> str:
    mods_lower = [m.lower() for m in mods]
    if "private" in mods_lower:
        return "private"
    if "protected" in mods_lower:
        return "protected"
    return "public"


def go_visibility(name: str, _mods: list[str]) -> str:
    return "public" if name and name[0].isupper() else "private"


def rust_visibility(_name: str, mods: list[str]) -> str:
    combined = " ".join(mods).strip()
    if not combined:
        return "private"
    if "pub(crate)" in combined:
        return "internal"
    if "pub(super)" in combined or "pub(in " in combined:
        return "protected"
    if "pub" in combined:
        return "public"
    return "private"


def java_visibility(_name: str, mods: list[str]) -> str:
    combined = " ".join(mods).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


def public_by_default(_name: str, _mods: list[str]) -> str:
    return "public"


def kotlin_visibility(_name: str, modifier_texts: list[str]) -> str:
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "internal" in combined:
        return "internal"
    return "public"


def csharp_visibility(_name: str, modifier_texts: list[str]) -> str:
    """C# visibility — public/private/protected/internal, default internal."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "internal" in combined:
        return "internal"
    if "public" in combined:
        return "public"
    return "internal"  # C# default is internal


def swift_visibility(_name: str, modifier_texts: list[str]) -> str:
    """Swift visibility — public/private/fileprivate/internal/open."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined or "fileprivate" in combined:
        return "private"
    if "public" in combined or "open" in combined:
        return "public"
    return "internal"  # Swift default is internal


def scala_visibility(_name: str, modifier_texts: list[str]) -> str:
    """Scala visibility — public/private/protected, default public."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


def php_visibility(_name: str, modifier_texts: list[str]) -> str:
    """PHP visibility — public/private/protected, default public."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    return "public"


# ---------------------------------------------------------------------------
# C / C++ node-aware visibility refinement
# ---------------------------------------------------------------------------

_CPP_EXPORT_MARKERS: tuple[str, ...] = (
    "__declspec(dllexport)",
    "__declspec( dllexport )",
    'visibility("default")',
    "visibility(\"default\")",
    # WebAssembly / emscripten / WASI export surfaces. A function compiled
    # to WASM and called across the JS<->WASM boundary carries one of these
    # markers; without them the export reads as an unused symbol because no
    # in-binary caller exists in the static graph (the caller is the host
    # runtime). ``EMSCRIPTEN_KEEPALIVE`` / ``WASM_EXPORT`` appear as a bare
    # macro token preceding the return type (parsed as the function's
    # leading ``type_identifier``); ``export_name(...)`` / ``used`` appear
    # inside an ``__attribute__((...))`` specifier — both are caught by the
    # leading-children scan in ``_has_export_marker``.
    "EMSCRIPTEN_KEEPALIVE",
    "WASM_EXPORT",
    "export_name(",
    "__attribute__((used))",
    "__attribute__((visibility(\"default\")))",
)


def _preceding_access_specifier(def_node: Node) -> str | None:
    """Walk back through siblings to find the most recent ``access_specifier``.

    Inside a ``field_declaration_list`` (class / struct body), C++ groups
    members under ``public:`` / ``private:`` / ``protected:`` access
    specifiers that appear as ordinary siblings. The visibility of a
    given member is dictated by the most recent specifier before it.
    """
    sibling = def_node.prev_sibling
    while sibling is not None:
        if sibling.type == "access_specifier":
            # The specifier's text is "public" / "private" / "protected".
            children = [c for c in sibling.children if c.is_named or c.type in ("public", "private", "protected")]
            for c in children:
                if c.type in ("public", "private", "protected"):
                    return c.type
            # Fall back to raw text for grammars that don't name the child.
            return None
        sibling = sibling.prev_sibling
    return None


def _enclosing_class_default_access(def_node: Node) -> str:
    """Default access in the enclosing aggregate: ``private`` for class, ``public`` for struct."""
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type == "class_specifier":
            return "private"
        if ancestor.type == "struct_specifier":
            return "public"
        ancestor = ancestor.parent
    return "public"


def _has_export_marker(def_node: Node, src: str) -> bool:
    """Return True if any ``__declspec(dllexport)`` / ``visibility("default")`` precedes the def."""
    # Walk back through siblings and check for attribute / declspec nodes
    # whose text contains an export marker. The tree-sitter-cpp grammar
    # exposes these as ``ms_declspec_modifier`` or ``attribute_specifier``
    # nodes — but textual matching is robust across grammar versions.
    sibling = def_node.prev_sibling
    seen = 0
    while sibling is not None and seen < 4:
        text = src[sibling.start_byte : sibling.end_byte]
        if any(marker in text for marker in _CPP_EXPORT_MARKERS):
            return True
        sibling = sibling.prev_sibling
        seen += 1
    # Also check the def_node's own leading children — some grammars
    # nest the declspec inside the function_definition.
    for child in def_node.children[:3]:
        text = src[child.start_byte : child.end_byte]
        if any(marker in text for marker in _CPP_EXPORT_MARKERS):
            return True
    return False


def _has_file_scope_static(def_node: Node, src: str) -> bool:
    """Return True if a ``static`` storage-class specifier appears in the leading declarators."""
    for child in def_node.children[:4]:
        if child.type == "storage_class_specifier":
            text = src[child.start_byte : child.end_byte]
            if "static" in text:
                return True
    return False


def refine_cpp_visibility(
    def_node: Node, current_visibility: str, src: str
) -> tuple[str, bool]:
    """Return ``(visibility, is_exported)`` for a C/C++ symbol.

    Inputs:
      * *def_node* — the captured ``@symbol.def`` node.
      * *current_visibility* — what ``public_by_default`` returned; used
        as the fallback when no specifier / marker applies.
      * *src* — full file source text.

    Behaviour:
      * Inside a ``class_specifier`` body, look back for the nearest
        ``access_specifier`` sibling. Absent one, fall back to
        ``private`` (the C++ class default) — ``struct`` defaults to
        ``public``.
      * Free function at namespace / file scope with ``static`` storage
        class → ``private`` (translation-unit local; not importable).
      * ``__declspec(dllexport)`` or ``__attribute__((visibility("default")))``
        → forces ``public`` and sets ``is_exported = True`` so a future
        "exported entry point" check can whitelist it.
      * Otherwise keep *current_visibility*.
    """
    # 1. Export markers always win.
    if _has_export_marker(def_node, src):
        return "public", True

    # 2. Class / struct member visibility comes from access specifiers.
    parent = def_node.parent
    if parent is not None and parent.type == "field_declaration_list":
        access = _preceding_access_specifier(def_node)
        if access is not None:
            return access, False
        # No access specifier — use the enclosing aggregate's default.
        return _enclosing_class_default_access(def_node), False

    # 3. File-scope ``static`` is translation-unit local.
    if _has_file_scope_static(def_node, src):
        return "private", False

    return current_visibility, False


VISIBILITY_FNS: dict[str, Callable[[str, list[str]], str]] = {
    "python": py_visibility,
    "typescript": ts_visibility,
    "javascript": public_by_default,
    "go": go_visibility,
    "rust": rust_visibility,
    "java": java_visibility,
    "cpp": public_by_default,
    "c": public_by_default,
    "kotlin": kotlin_visibility,
    "ruby": public_by_default,
    "csharp": csharp_visibility,
    "swift": swift_visibility,
    "scala": scala_visibility,
    "php": php_visibility,
}
