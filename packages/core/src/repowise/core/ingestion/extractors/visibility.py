"""Per-language visibility determination functions.

Most languages can determine visibility from a symbol's name + modifier
text alone (the ``visibility_fn`` shape). Some cannot, because the answer
depends on surrounding AST context — C/C++ ``public:`` / ``private:``
access specifier siblings, ``static`` storage class at file scope and
``__declspec(dllexport)`` attributes; the C# and Java no-modifier
defaults, which differ by enclosing declaration; TS/JS export position;
Python's module-level ``__all__``, which can export a name the identifier
alone would call private; Rust's trait items, which may not write a modifier
of their own; Ruby's ``private`` / ``protected``, which are calls in the class
body, not modifiers. Each has a ``refine_*_visibility`` the parser calls after the
generic ``visibility_fn``.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from repowise.core.pyparse import parse_python

from .helpers import node_text

if TYPE_CHECKING:
    from tree_sitter import Node


def py_visibility(name: str, _mods: list[str]) -> str:
    if name.startswith("__") and name.endswith("__"):
        return "public"  # dunder
    if name.startswith("_"):
        return "private"
    return "public"


# ---------------------------------------------------------------------------
# Python module __all__ visibility refinement
# ---------------------------------------------------------------------------

# A definition nested under one of these belongs to that scope, not to the
# module's namespace, so a module-level ``__all__`` does not govern it.
_PY_MEMBER_ANCESTORS = frozenset(
    {"class_definition", "function_definition", "async_function_definition", "lambda"}
)


def _py_all_literal(value: ast.expr | None) -> set[str] | None:
    """The names in a literal ``__all__`` value, or ``None`` when it is not literal.

    List, tuple and set all enumerate statically; every element must be a plain
    string constant. A comprehension, a concatenation, a call or a name is a
    list this module assembles at runtime, and reading part of it would be
    worse than reading none of it.
    """
    if not isinstance(value, (ast.List, ast.Tuple, ast.Set)):
        return None
    names: set[str] = set()
    for element in value.elts:
        if not isinstance(element, ast.Constant) or not isinstance(element.value, str):
            return None
        names.add(element.value)
    return names


def _py_all_has_other_binding(tree: ast.Module, accepted: list[ast.Name]) -> bool:
    """True when ``__all__`` is written or imported anywhere but the literal assignments.

    ``tree.body`` cannot see ``__all__ += [...]``, a binding inside an ``if``
    or a function, an unpacking target, a ``del``, or ``from x import __all__``.
    Each of those makes the list something other than the single literal the
    caller can trust, so the whole signal is dropped rather than half-read.
    """
    accepted_ids = {id(node) for node in accepted}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and node.id == "__all__":
            if isinstance(node.ctx, (ast.Store, ast.Del)) and id(node) not in accepted_ids:
                return True
        elif isinstance(node, ast.alias) and node.name == "__all__":
            return True
    return False


def py_module_all_names(src: str) -> frozenset[str] | None:
    """Names from a literal module-level ``__all__``, or ``None`` for no signal.

    ``None`` means the module gave no answer this pass may act on: no
    ``__all__`` at all, one built at runtime (a comprehension, ``+=``, a
    concatenation, a non-string element), or any shape that leaves the list
    not statically enumerable — a second assignment, an assignment below module
    level, a ``del``, an import of the name. A built list is treated as absent
    rather than half-parsed.
    """
    # A module with no ``__all__`` token has no literal to read, and most
    # modules do not — the parse below is the only cost this pass adds, so the
    # reject keeps it off every Python file in the tree but the ones that use
    # the name (the same shape as ``ts_export_aliases``' ``" as "`` guard).
    if "__all__" not in src:
        return None
    try:
        tree = parse_python(src)
    except (SyntaxError, ValueError):
        return None

    names: set[str] = set()
    accepted: list[ast.Name] = []
    assigned = False
    for stmt in tree.body:
        targets: list[ast.expr]
        value: ast.expr | None
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign):
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for target in targets:
            if not (isinstance(target, ast.Name) and target.id == "__all__"):
                continue
            if assigned:
                return None  # two bindings: the second one wins at runtime
            literal = _py_all_literal(value)
            if literal is None:
                return None
            names = literal
            assigned = True
            accepted.append(target)

    if not assigned or _py_all_has_other_binding(tree, accepted):
        return None
    return frozenset(names)


def refine_py_visibility(
    def_node: Node,
    current_visibility: str,
    name: str,
    all_names: frozenset[str] | None,
) -> str:
    """Raise a module-level name the module lists in ``__all__`` to ``public``.

    ``py_visibility`` reads the identifier alone, so an underscore-prefixed
    name that ``__all__`` explicitly exports reads ``private``. Membership is
    the one thing that changes here, and it is capped at the visibility label:
    it sets no export marker, mints no edge and suppresses no dead-code
    finding, so ``__all__`` alone can never mark a symbol reachable. A name
    the list omits is left exactly as it was — ``__all__`` is often stale, and
    demoting on absence would move a genuinely dead export into the opt-in
    internals pass and hide it from the default report.
    """
    if all_names is None or name not in all_names:
        return current_visibility
    node = def_node.parent
    while node is not None:
        if node.type in _PY_MEMBER_ANCESTORS:
            return current_visibility
        node = node.parent
    return "public"


def ts_visibility(name: str, mods: list[str]) -> str:
    mods_lower = [m.lower() for m in mods]
    # ``#x`` is an ECMAScript private member: private without any modifier.
    if "private" in mods_lower or name.startswith("#"):
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
    """Java visibility; no access keyword is package-private, recorded ``internal``.

    That default is the top-level type one. Interface members and enum
    constructors default differently, which ``refine_java_visibility``
    corrects from the AST.
    """
    combined = " ".join(mods).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "public" in combined:
        return "public"
    return "internal"


def public_by_default(_name: str, _mods: list[str]) -> str:
    return "public"


def apex_visibility(_name: str, _mods: list[str]) -> str:
    """Apex's default access, private; ``refine_apex_visibility`` reads the keyword.

    The modifier text is not searched: it carries the annotations, and
    ``@SuppressWarnings('PMD.AvoidGlobalModifier')`` would read as ``global``.
    """
    return "private"


# ``global`` (managed-package API) and ``webservice`` (SOAP, global only) are
# wider than ``public``; the vocabulary has nothing wider, so they record as it.
_APEX_ACCESS = (
    ("global", "public"),
    ("webservice", "public"),
    ("public", "public"),
    ("protected", "protected"),
    ("private", "private"),
)


def _apex_modifier_keywords(def_node: Node) -> set[str]:
    """The keyword node types under a declaration's ``modifiers``, any case."""
    modifiers = next((c for c in def_node.children if c.type == "modifiers"), None)
    if modifiers is None:
        return set()
    return {
        keyword.type
        for modifier in modifiers.children
        if modifier.type == "modifier"
        for keyword in modifier.children
    }


def refine_apex_visibility(def_node: Node) -> str:
    """The access keyword an Apex declaration writes, else its scope's default.

    Keywords are ``modifier`` children of ``modifiers``, typed by the keyword
    whatever case it is written in. With none, an interface member is public
    and anything else is private. A trigger is public.
    """
    if def_node.type == "trigger_declaration":
        # Run by the platform on DML from any context; it takes no modifier.
        return "public"
    written = _apex_modifier_keywords(def_node)
    for keyword, visibility in _APEX_ACCESS:
        if keyword in written:
            return visibility
    parent = def_node.parent
    return "public" if parent is not None and parent.type == "interface_body" else "private"


# Elixir spells privacy in the definition keyword rather than in a modifier:
# `defp` / `defmacrop` / `defguardp` are module-private, their unsuffixed
# forms are public. elixir.scm captures the keyword as @symbol.modifiers.
_ELIXIR_PRIVATE_KEYWORDS = frozenset({"defp", "defmacrop", "defguardp"})


def elixir_visibility(_name: str, modifier_texts: list[str]) -> str:
    if any(text.strip() in _ELIXIR_PRIVATE_KEYWORDS for text in modifier_texts):
        return "private"
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


def vbnet_visibility(_name: str, modifier_texts: list[str]) -> str:
    """VB.NET visibility: Public/Private/Protected/Friend (internal).

    VB.NET's default access for a top-level type is ``Friend`` (assembly
    scope), which maps to C#'s ``internal``; nested types under a parent
    default to the parent's scope. ``Protected Friend`` is reported as
    protected, the narrower of the two halves, matching how
    ``csharp_visibility`` above reports ``protected internal``.
    """
    combined = " ".join(modifier_texts).lower()
    if "private" in combined:
        return "private"
    if "protected" in combined:
        return "protected"
    if "friend" in combined:
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
# CommonJS is a write to ``exports``, never a read: ``ext.exports``,
# ``const prev = module.exports`` or a local ``exports.enabled`` is ordinary
# code, and treating it as CommonJS kept every non-exported declaration in the
# file public. A write assigns ``module.exports`` (or a member of it) or a
# member of bare ``exports``, or ``Object.assign``/``defineProperty`` onto one.
_CJS_MODULE_EXPORTS = r"""\bmodule\s*(?:\.\s*exports\b|\[\s*["']exports["']\s*\])"""
_CJS_MEMBER = r"(?:\.\s*[A-Za-z_$][\w$]*|\[[^\]\n]*\])"
_CJS_ASSIGN = r"\s*=(?![=>])"
_TS_CJS_EXPORTS_RE = re.compile(
    rf"{_CJS_MODULE_EXPORTS}(?:\s*{_CJS_MEMBER})?{_CJS_ASSIGN}"
    rf"|(?<![.\w$])exports\s*{_CJS_MEMBER}{_CJS_ASSIGN}"
    rf"|\b(?:defineProperty|assign)\(\s*(?:{_CJS_MODULE_EXPORTS}|exports\b)"
)

# The same list form, plus whatever follows the closing brace, so a clause with
# a ``from`` source can be told from one without. Only the source-less form
# publishes a name for a symbol the file declares itself; the other is a
# re-export the import pipeline already carries.
_TS_EXPORT_LIST_SOURCE_RE = re.compile(r"\bexport\s*\{([^}]*)\}\s*(from\b)?")
# ``export default name`` with nothing after the name: the statement form of
# ``export { name as default }``. A declaration (``export default function f``)
# or an expression (``export default f()``) does not match.
_TS_EXPORT_DEFAULT_NAME_RE = re.compile(
    r"^[ \t]*export[ \t]+default[ \t]+([A-Za-z_$][\w$]*)[ \t]*;?[ \t]*\r?$", re.MULTILINE
)

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
    ``export default name`` is the same clause written as a statement, so it is
    recorded as ``{"default": name}``.
    """
    # Every alias is written ``local as exported`` or ``export default name``,
    # so a file with neither token cannot hold one and need not be scanned.
    # Most files do not, and the scan is over the whole source.
    if " as " not in src and "export default" not in src:
        return {}

    # Comments are stripped first, and this map is the reason it is worth the
    # pass. ``ts_deferred_export_names`` reads the same clause unstripped and a
    # commented-out one only ever mislabels a symbol public; here it would name
    # a local symbol as some module's published API and mint a call edge to it.
    cleaned = _TS_LINE_COMMENT.sub("", _TS_BLOCK_COMMENT.sub("", src))

    pairs = [(m.group(1), "default") for m in _TS_EXPORT_DEFAULT_NAME_RE.finditer(cleaned)]
    for m in _TS_EXPORT_LIST_SOURCE_RE.finditer(cleaned):
        if m.group(2):  # ``export { a as b } from "./x"`` — a re-export
            continue
        for part in m.group(1).split(","):
            local, separator, exported = (p.strip() for p in part.partition(" as "))
            if separator:
                pairs.append((local, exported))

    aliases: dict[str, str] = {}
    for local, exported in pairs:
        if not local.isidentifier() or not exported.isidentifier():
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
# Java node-aware visibility refinement
# ---------------------------------------------------------------------------

_JAVA_ACCESS_KEYWORDS = ("private", "protected", "public")

# Members of these bodies are implicitly public (JLS 9.4, 9.5, 9.6).
_JAVA_PUBLIC_BODIES = frozenset({"interface_body", "annotation_type_body"})


_RUBY_ACCESS = frozenset({"private", "protected", "public"})
_RUBY_CLASS_ACCESS = {"private_class_method": "private", "public_class_method": "public"}
_RUBY_BODIES = frozenset({"body_statement", "class", "module", "singleton_class"})
# A body's index: the bare access in force at each def (by start byte), and the
# ``(position, access)`` of every call naming a method, for instance and class methods.
_RubyBody = tuple[
    dict[int, str], dict[str, list[tuple[int, str]]], dict[str, list[tuple[int, str]]]
]


def _ruby_call_access(call: Node, src: str, names: Iterable[str]) -> tuple[str, list[str]] | None:
    """``(keyword, [method names])`` of a ``private :a, :b`` style call, else None.

    A call with no symbol or string arguments (``private def x``,
    ``private attr_reader :y``) lists no names.
    """
    method = call.child_by_field_name("method")
    keyword = node_text(method, src)
    if call.type != "call" or call.child_by_field_name("receiver") is not None:
        return None
    if keyword not in names:
        return None
    args = call.child_by_field_name("arguments")
    listed: list[str] = []
    for arg in args.named_children if args is not None else ():
        if arg.type == "simple_symbol":
            listed.append(node_text(arg, src).lstrip(":"))
        elif arg.type == "string":
            parts = (node_text(c, src) for c in arg.named_children if c.type == "string_content")
            listed.append("".join(parts))
    return keyword, listed


# The file whose class bodies are indexed, and the index: parsing visits every def of a
# class, so each body is read once. The source is held by reference, which keeps its
# identity from being recycled by another file while it is cached.
_ruby_bodies: dict[str, Any] = {"src": None, "index": {}}

_RUBY_ACCESS_CALLS = _RUBY_ACCESS | frozenset(_RUBY_CLASS_ACCESS)


def _ruby_body_access(body: Node, src: str) -> _RubyBody:
    """What a class or module body says about the access of the methods it defines."""
    if _ruby_bodies["src"] is not src:
        _ruby_bodies["src"], _ruby_bodies["index"] = src, {}
    index: dict[tuple[int, int], _RubyBody] = _ruby_bodies["index"]
    key = (body.start_byte, body.end_byte)
    found = index.get(key)
    if found is not None:
        return found
    defaults: dict[int, str] = {}
    named: dict[str, list[tuple[int, str]]] = {}
    class_named: dict[str, list[tuple[int, str]]] = {}
    bare: str | None = None
    for child in body.children:
        if child.type == "identifier":
            word = node_text(child, src)
            if word in _RUBY_ACCESS:
                bare = word
        elif child.type == "method":
            if bare is not None:
                defaults[child.start_byte] = bare
        elif child.type == "call":
            hit = _ruby_call_access(child, src, _RUBY_ACCESS_CALLS)
            if hit is None:
                continue
            keyword, names = hit
            table, access = (
                (class_named, _RUBY_CLASS_ACCESS[keyword])
                if keyword in _RUBY_CLASS_ACCESS
                else (named, keyword)
            )
            for name in names:
                table.setdefault(name, []).append((child.start_byte, access))
    found = index[key] = (defaults, named, class_named)
    return found


def refine_ruby_visibility(def_node: Node, current_visibility: str, src: str) -> str:
    """Give a Ruby method the access its class body gives it.

    ``private`` and ``protected`` are method calls, not modifiers: a bare one
    sets the access of every ``def`` after it in the same body (``class << self``
    bodies included), and ``private :a, :b`` or ``private def a`` names what it
    covers. A later call naming the method wins over the bare default, and
    ``def self.x`` ignores a bare ``private`` (``private_class_method`` is its
    own). A ``def`` outside a class or module body keeps *current_visibility*.
    """
    if def_node.type not in ("method", "singleton_method"):
        return current_visibility
    parent = def_node.parent
    if parent is None:
        return current_visibility
    # ``private def x`` wraps the definition in the call that names the access.
    if parent.type == "argument_list" and parent.parent is not None:
        wrapped = _ruby_call_access(parent.parent, src, _RUBY_ACCESS)
        if wrapped is not None and def_node.type == "method":
            return wrapped[0]
        return current_visibility
    if parent.type not in _RUBY_BODIES:
        return current_visibility
    defaults, named, class_named = _ruby_body_access(parent, src)
    if def_node.type == "method":
        visibility = defaults.get(def_node.start_byte, current_visibility)
        later = named
    else:
        visibility, later = current_visibility, class_named
    name = node_text(def_node.child_by_field_name("name"), src)
    for position, access in later.get(name, ()):
        if position > def_node.start_byte:
            visibility = access
    return visibility


def refine_java_visibility(def_node: Node) -> str:
    """Give a Java declaration the access it writes, else its scope's default.

    Read from the AST because the query's modifier-less pattern matches first
    and the parser keeps the first match, so ``private class X`` reaches
    ``java_visibility`` with no modifier text at all. With no access keyword a
    declaration is package-private, except that interface and annotation
    members are public and an enum constructor is private.
    """
    modifiers = next((c for c in def_node.children if c.type == "modifiers"), None)
    if modifiers is not None:
        written = {c.type for c in modifiers.children}
        for keyword in _JAVA_ACCESS_KEYWORDS:
            if keyword in written:
                return keyword
    body = def_node.parent
    if body is None:
        return "internal"
    if body.type in _JAVA_PUBLIC_BODIES:
        return "public"
    if def_node.type == "constructor_declaration" and body.type == "enum_body_declarations":
        return "private"
    return "internal"


# ---------------------------------------------------------------------------
# Rust node-aware visibility refinement
# ---------------------------------------------------------------------------


_RUST_TYPE_DECL_KINDS: frozenset[str] = frozenset(
    {
        "struct_item",
        "enum_item",
        "union_item",
        "trait_item",
        "type_item",
    }
)


def _rust_extract_type_name(node: Node | None, src: str) -> str | None:
    """Extract the base type identifier from a Rust type AST node."""
    if node is None:
        return None
    if node.type == "type_identifier":
        return node_text(node, src).strip() or None
    if node.type == "scoped_type_identifier":
        name_node = node.child_by_field_name("name")
        if name_node is not None:
            return _rust_extract_type_name(name_node, src)
    if node.type in ("generic_type", "reference_type"):
        inner = node.child_by_field_name("type")
        if inner is not None:
            return _rust_extract_type_name(inner, src)
    for child in node.children:
        if child.type == "type_identifier":
            return node_text(child, src).strip() or None
        nested = _rust_extract_type_name(child, src)
        if nested:
            return nested
    return None


def refine_rust_visibility(def_node: Node, current_visibility: str, src: str) -> str:
    """Give a trait's items or an impl block the visibility the target declares.

    Rust forbids a visibility modifier on trait items and impl blocks, so
    ``rust_visibility`` reads empty modifier text and calls them private by default.
    - For trait items: the enclosing trait's own modifier provides the visibility.
    - For impl blocks: the target type (or trait) declaration in the same scope provides
      the visibility.
    """
    if any(c.type == "visibility_modifier" for c in def_node.children):
        return current_visibility

    # Case 1: An `impl_item` block inherits the visibility of its target type or trait.
    if def_node.type == "impl_item":
        parent = def_node.parent
        if parent is None:
            return current_visibility

        target_names: list[str] = []
        type_name = _rust_extract_type_name(def_node.child_by_field_name("type"), src)
        if type_name:
            target_names.append(type_name)
        trait_name = _rust_extract_type_name(def_node.child_by_field_name("trait"), src)
        if trait_name and trait_name not in target_names:
            target_names.append(trait_name)

        if not target_names:
            return current_visibility

        for target in target_names:
            for sibling in parent.children:
                if sibling.type in _RUST_TYPE_DECL_KINDS:
                    name_child = sibling.child_by_field_name("name")
                    if name_child is None:
                        name_child = next(
                            (c for c in sibling.children if c.type == "type_identifier"),
                            None,
                        )
                    if name_child is not None and node_text(name_child, src).strip() == target:
                        modifier = next(
                            (c for c in sibling.children if c.type == "visibility_modifier"),
                            None,
                        )
                        if modifier is not None:
                            return rust_visibility("", [node_text(modifier, src)])
                        return "private"

        return current_visibility

    # Case 2: Trait items sit directly in the trait's ``declaration_list``. Matching
    # that exact shape rather than walking ancestors keeps an item nested
    # inside a defaulted method's body out of the trait's bucket.
    decls = def_node.parent
    if decls is None or decls.type != "declaration_list":
        return current_visibility
    trait = decls.parent
    if trait is None or trait.type != "trait_item":
        return current_visibility
    modifier = next((c for c in trait.children if c.type == "visibility_modifier"), None)
    if modifier is None:
        return current_visibility
    return rust_visibility("", [node_text(modifier, src)])


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


def _in_anonymous_namespace(def_node: Node) -> bool:
    """Return True if an unnamed ``namespace { ... }`` encloses the def."""
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type == "namespace_definition" and ancestor.child_by_field_name("name") is None:
            return True
        ancestor = ancestor.parent
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
        class, or anything in an anonymous namespace → ``private``
        (translation-unit local; not importable).
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

    # 3. File-scope ``static`` and an anonymous namespace are both internal
    # linkage: translation-unit local.
    if _has_file_scope_static(def_node, src) or _in_anonymous_namespace(def_node):
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
    "elixir": elixir_visibility,
    # F# has no `protected` binding form and spells assembly scope
    # `internal`, which is exactly what kotlin_visibility answers.
    "fsharp": kotlin_visibility,
}
