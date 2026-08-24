"""Per-language visibility determination functions.

Most languages can determine visibility from a symbol's name + modifier
text alone (the ``visibility_fn`` shape). Some cannot, because the answer
depends on surrounding AST context — C/C++ ``public:`` / ``private:``
access specifier siblings, ``static`` storage class at file scope and
``__declspec(dllexport)`` attributes; C#'s no-modifier default, which
differs by enclosing declaration; TS/JS export position. Each has a
``refine_*_visibility`` the parser calls after the generic
``visibility_fn``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from .helpers import node_text

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
    """C# visibility — public/private/protected/internal.

    The no-modifier default returned here is the *top-level type* one;
    every other declaration site has a different default that depends on
    what encloses it, which this signature cannot see.
    ``refine_csharp_visibility`` corrects it from the AST.
    """
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "internal" in combined:
        return "internal"
    if "public" in combined:
        return "public"
    return "internal"


def swift_visibility(_name: str, modifier_texts: list[str]) -> str:
    """Swift visibility — public/private/fileprivate/internal/open."""
    combined = " ".join(modifier_texts).lower()
    if "private" in combined or "fileprivate" in combined:
        return "private"
    if "public" in combined or "open" in combined:
        return "public"
    return "internal"  # Swift default is internal


def dart_visibility(name: str, _mods: list[str]) -> str:
    """Dart visibility is name-based: a leading underscore is library-private."""
    return "private" if name.startswith("_") else "public"


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
# TS / JS node-aware visibility refinement
# ---------------------------------------------------------------------------

# ``export { a, b as c }`` lists (with or without a ``from`` clause),
# ``export default name``, and TS ``export = name`` — the deferred-export
# forms that make a plainly-declared top-level symbol part of the module's
# public surface.
_TS_EXPORT_LIST_RE = re.compile(r"\bexport\s*\{([^}]*)\}")
_TS_EXPORT_DEFAULT_RE = re.compile(r"\bexport\s+default\s+([A-Za-z_$][\w$]*)")
_TS_EXPORT_ASSIGN_RE = re.compile(r"\bexport\s*=\s*([A-Za-z_$][\w$]*)")
_TS_CJS_EXPORTS_RE = re.compile(r"\bmodule\.exports\b|\bexports\s*[.\[]")

# The same list form, plus whatever follows the closing brace, so a clause with
# a ``from`` source can be told from one without. Only the source-less form
# publishes a name for a symbol the file declares itself; the other is a
# re-export the import pipeline already carries.
_TS_EXPORT_LIST_SOURCE_RE = re.compile(r"\bexport\s*\{([^}]*)\}\s*(from\b)?")

# Dropped outright rather than blanked: nothing downstream of the alias scan
# reads a line number, and collapsing a comment between a clause and its
# ``from`` is what lets the two be told apart at all.
_TS_BLOCK_COMMENT = re.compile(r"/\*(?:.|\n)*?\*/")
_TS_LINE_COMMENT = re.compile(r"//.*")

_TS_CLASSLIKE_ANCESTORS = frozenset(
    {
        "class_declaration",
        "abstract_class_declaration",
        "class_body",
        "interface_body",
        "enum_body",
        "internal_module",  # TS ``namespace X { ... }``
    }
)


def ts_deferred_export_names(src: str) -> frozenset[str] | None:
    """Names exported after their declaration, or ``None`` when unsafe to track.

    CommonJS surfaces (``module.exports`` / ``exports.x``) assign exports
    dynamically, so per-name tracking is unreliable — return ``None`` and the
    caller keeps everything public (the safe direction).
    """
    if _TS_CJS_EXPORTS_RE.search(src):
        return None
    names: set[str] = set()
    for m in _TS_EXPORT_LIST_RE.finditer(src):
        for part in m.group(1).split(","):
            # ``local as exported`` — the local name is what we match against.
            base = part.strip().split(" as ")[0].strip()
            if base:
                names.add(base)
    for regex in (_TS_EXPORT_DEFAULT_RE, _TS_EXPORT_ASSIGN_RE):
        for m in regex.finditer(src):
            names.add(m.group(1))
    return frozenset(names)


def ts_export_aliases(src: str) -> dict[str, str]:
    """``{exported name: local name}`` for renaming, source-less export clauses.

    ``export { stringType as string }`` is the only place a module states that
    the symbol it declares as ``stringType`` is published as ``string``. It
    carries no ``from`` clause, so it is not an import and nothing in the
    import pipeline records it; the file's symbol table keeps the local name,
    and every namespace, barrel and member lookup downstream asks for the
    published one.

    Clauses that do not rename are omitted: the two names agree, so the symbol
    table already answers and an entry would only duplicate it.
    """
    # Every alias is written ``local as exported``, so a file without that
    # token cannot hold one and need not be scanned. Most files do not, and
    # the scan is over the whole source.
    if " as " not in src:
        return {}

    # Comments are stripped first, and this map is the reason it is worth the
    # pass. ``ts_deferred_export_names`` reads the same clause unstripped and a
    # commented-out one only ever mislabels a symbol public; here it would name
    # a local symbol as some module's published API and mint a call edge to it.
    cleaned = _TS_LINE_COMMENT.sub("", _TS_BLOCK_COMMENT.sub("", src))

    aliases: dict[str, str] = {}
    for m in _TS_EXPORT_LIST_SOURCE_RE.finditer(cleaned):
        if m.group(2):  # ``export { a as b } from "./x"`` — a re-export
            continue
        for part in m.group(1).split(","):
            local, separator, exported = (p.strip() for p in part.partition(" as "))
            if not separator or not local.isidentifier() or not exported.isidentifier():
                continue
            # A name published twice under one spelling has no single answer,
            # and guessing costs a wrong edge where refusing costs none.
            aliases[exported] = local if aliases.get(exported, local) == local else ""
    return {exported: local for exported, local in aliases.items() if local}


def refine_ts_visibility(
    def_node: Node, current_visibility: str, name: str, deferred_exports: frozenset[str] | None
) -> str:
    """Demote non-exported TS/JS top-level symbols to ``private``.

    ``ts_visibility`` only sees class-member accessibility modifiers, so every
    top-level declaration lands ``public`` whether or not it carries an
    ``export`` — which makes plainly-private module helpers eligible for the
    dead-code unused-export pass and inflates the file's derived export list.
    A symbol stays public only when its declaration sits under an
    ``export_statement`` or its name appears in a deferred-export form.
    Class/namespace members are left untouched (their visibility is governed
    by the member modifiers and the enclosing declaration's export).
    """
    if current_visibility != "public":
        return current_visibility
    if deferred_exports is None:
        return current_visibility
    node = def_node.parent
    while node is not None:
        if node.type == "export_statement":
            return "public"
        if node.type in _TS_CLASSLIKE_ANCESTORS:
            return current_visibility
        node = node.parent
    if name in deferred_exports:
        return "public"
    return "private"


# ---------------------------------------------------------------------------
# C# node-aware visibility refinement
# ---------------------------------------------------------------------------

_CS_ACCESS_KEYWORDS = frozenset({"public", "private", "protected", "internal"})

# What a declaration with no accessibility modifier defaults to, keyed by the
# declaration that encloses it. Anything not listed (a namespace, or the
# compilation unit) leaves the top-level-type default of ``internal`` standing.
# ``record struct`` is a ``record_declaration`` carrying a ``struct`` token,
# not a node type of its own, so it needs no entry.
_CS_DEFAULT_BY_ENCLOSING: dict[str, str] = {
    "interface_declaration": "public",
    "class_declaration": "private",
    "struct_declaration": "private",
    "record_declaration": "private",
    "enum_declaration": "public",
}


def _csharp_declared_access(def_node: Node) -> str | None:
    """The accessibility the declaration writes, or ``None`` if it writes none.

    Read from the AST rather than from the captured modifier texts. The
    queries capture a single ``(modifier)`` child and the parser's dedup keeps
    the first match, so ``static internal void M()`` arrives at the
    ``visibility_fn`` as ``["static"]`` with the accessibility dropped —
    every declaration that writes a non-accessibility modifier first is
    invisible to text alone.

    The keyword is the ``modifier`` node's own child type, so this never
    slices the source: ``src`` is decoded text and node offsets are byte
    offsets, which diverge after any multi-byte character (a leading UTF-8
    BOM is enough).
    """
    written = {
        keyword.type for c in def_node.children if c.type == "modifier" for keyword in c.children
    } & _CS_ACCESS_KEYWORDS
    if not written:
        return None
    # Same precedence the modifier-text fn uses, so a pair reads identically
    # whichever half the capture happened to keep: ``private protected`` is
    # recorded private, ``protected internal`` protected.
    for keyword in ("private", "protected", "internal", "public"):
        if keyword in written:
            return keyword
    return None


def refine_csharp_visibility(def_node: Node, current_visibility: str) -> str:
    """Give a C# declaration the accessibility it writes, else its scope's default.

    ``csharp_visibility`` sees modifier text only, and that text is both
    truncated (see ``_csharp_declared_access``) and defaulted to the
    top-level-type answer. The real default is ``private`` inside a
    class/struct/record and ``public`` inside an interface or enum — a
    two-bucket error that puts most C# members in the wrong dead-code pool.
    """
    declared = _csharp_declared_access(def_node)
    if declared is not None:
        return declared
    # An explicit interface implementation (``void IFoo.Bar() { }``) forbids
    # modifiers and is unreachable through the class, only through the
    # interface. Calling it private would put a member with a live caller
    # into the narrow dead-code pool.
    if any(c.type == "explicit_interface_specifier" for c in def_node.children):
        return "public"
    node = def_node.parent
    while node is not None:
        default = _CS_DEFAULT_BY_ENCLOSING.get(node.type)
        if default is not None:
            return default
        if node.type in ("namespace_declaration", "file_scoped_namespace_declaration"):
            break
        node = node.parent
    return current_visibility


# ---------------------------------------------------------------------------
# C / C++ node-aware visibility refinement
# ---------------------------------------------------------------------------

_CPP_EXPORT_MARKERS: tuple[str, ...] = (
    "__declspec(dllexport)",
    "__declspec( dllexport )",
    'visibility("default")',
    'visibility("default")',
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
    '__attribute__((visibility("default")))',
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
            children = [
                c
                for c in sibling.children
                if c.is_named or c.type in ("public", "private", "protected")
            ]
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
        text = node_text(sibling, src)
        if any(marker in text for marker in _CPP_EXPORT_MARKERS):
            return True
        sibling = sibling.prev_sibling
        seen += 1
    # Also check the def_node's own leading children — some grammars
    # nest the declspec inside the function_definition.
    for child in def_node.children[:3]:
        text = node_text(child, src)
        if any(marker in text for marker in _CPP_EXPORT_MARKERS):
            return True
    return False


def _has_file_scope_static(def_node: Node, src: str) -> bool:
    """Return True if a ``static`` storage-class specifier appears in the leading declarators."""
    for child in def_node.children[:4]:
        if child.type == "storage_class_specifier":
            text = node_text(child, src)
            if "static" in text:
                return True
    return False


def refine_cpp_visibility(def_node: Node, current_visibility: str, src: str) -> tuple[str, bool]:
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
