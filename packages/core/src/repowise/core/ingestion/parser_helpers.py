"""Stateless AST helper functions used by :class:`~.parser.ASTParser`.

Pure tree-sitter node utilities (query execution, qualified-name building,
C# type-head extraction, call-argument counting, enclosing-symbol lookup,
…) extracted from ``parser.py`` so that module holds the parser class and
this one holds the free functions it calls. No state, no imports from
``parser`` — keeping this a leaf so there is no import cycle.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from tree_sitter import Node

from .extractors import fsharp_type_name, node_text
from .language_data import get_builtin_types
from .type_names import is_resolvable_type_name

if TYPE_CHECKING:
    from .models import Symbol

log = structlog.get_logger(__name__)

# Private alias for internal use (mirrors the one in parser.py)
_node_text = node_text

_WHITESPACE_RE = re.compile(r"\s+")


def _run_query(query: object, root_node: Node) -> list[dict[str, list[Node]]]:
    """Execute a tree-sitter query and return a list of capture dicts."""
    results: list[dict[str, list[Node]]] = []
    try:
        from tree_sitter import QueryCursor  # type: ignore[attr-defined]

        cursor = QueryCursor(query)  # type: ignore[call-arg]
        for match in cursor.matches(root_node):
            if hasattr(match, "captures"):
                results.append(match.captures)
            elif isinstance(match, tuple) and len(match) == 2:
                _, caps = match
                results.append(caps)
    except Exception:
        try:
            for item in query.matches(root_node):  # type: ignore[attr-defined]
                if isinstance(item, tuple) and len(item) == 2:
                    _, caps = item
                    results.append(caps)
        except Exception as exc:
            log.warning("query.matches() failed", error=str(exc))
    return results


def _collect_error_nodes(root: Node) -> list[str]:
    """Return error descriptions for any ERROR nodes in the tree."""
    errors: list[str] = []

    def _walk(node: Node) -> None:
        if node.type == "ERROR":
            errors.append(f"Parse error at line {node.start_point[0] + 1}")
        for child in node.children:
            _walk(child)

    _walk(root)
    return errors


def _is_async_node(node: Node, src: str) -> bool:
    return node.type == "async_function_definition" or any(c.type == "async" for c in node.children)


_CALLABLE_KINDS: frozenset[str] = frozenset({"function", "method"})


def _has_callable_ancestor(
    node: Node,
    symbol_kinds: dict[str, str],
    ignored_node_ids: frozenset[int] = frozenset(),
) -> bool:
    """True if ``node`` has any function/method ancestor in the AST.

    Used to filter out helpers defined inside another function's body
    (React event handlers, async-method-local coroutines, JS closures)
    from the top-level symbol list. Class bodies don't count — methods
    inside classes have only a ``class`` ancestor before the module root.

    ``ignored_node_ids`` covers grammar-recovery nodes that a language query
    has positively identified as type containers rather than callables.
    """
    ancestor = node.parent
    while ancestor is not None:
        if (
            ancestor.id not in ignored_node_ids
            and symbol_kinds.get(ancestor.type) in _CALLABLE_KINDS
        ):
            return True
        ancestor = ancestor.parent
    return False


def _qualified_cpp_parent(name_node: Node, src: str) -> str | None:
    """Return the parent class for a C/C++ ``Class::method`` definition.

    The captured ``@symbol.name`` for a qualified function definition
    is the bare ``method`` identifier whose parent is a
    ``qualified_identifier`` carrying the class / namespace as its
    ``scope`` field. For multi-level qualifications (``NS::Foo::method``)
    the relevant parent is still the innermost qualifier — namespaces
    above it are not the symbol's containing type. Tree-sitter-cpp
    represents this by nesting ``qualified_identifier`` left-recursively,
    so the immediate parent's ``scope`` is always the right answer.

    Returns ``None`` when the name node is not inside a qualified
    identifier (i.e. plain free function).
    """
    parent = name_node.parent
    if parent is None or parent.type != "qualified_identifier":
        return None
    scope = parent.child_by_field_name("scope")
    if scope is None:
        return None
    text = node_text(scope, src).strip()
    # ``scope`` may itself be a qualified path (``NS::Foo``); take the
    # last component — that's the immediate enclosing type.
    return text.rsplit("::", 1)[-1] or None


# Elixir keywords that DEFINE rather than call. Every one of them parses as a
# `call` whose target identifier holds the keyword text, so the head it takes
# (`add(a, b)` in `def add(a, b)`) is a `call` node too.
_ELIXIR_DEFINITION_KEYWORDS = frozenset(
    {
        "def",
        "defp",
        "defmacro",
        "defmacrop",
        "defguard",
        "defguardp",
        "defdelegate",
        "defmodule",
        "defprotocol",
        "defimpl",
        "defexception",
        "defstruct",
    }
)

# Module attributes whose body is a type expression, not code. `@spec
# add(integer(), integer()) :: integer()` holds three `call` nodes and calls
# nothing; every other attribute (`@config Application.compile_env(…)`) holds
# real code and keeps its edges.
_ELIXIR_TYPESPEC_ATTRIBUTES = frozenset(
    {"spec", "type", "typep", "opaque", "callback", "macrocallback"}
)

# Elixir keywords that own a nested definition -- the parent a `def` reports.
# `defimpl` is here because its definitions belong to the implementation module
# the compiler generates, not to whatever module the block happens to sit in.
_ELIXIR_MODULE_KEYWORDS = frozenset({"defmodule", "defprotocol", "defimpl"})

# Node kinds that hold a list of statements. Reaching one while walking out of
# an expression means the enclosing statement has been left.
_ELIXIR_STATEMENT_HOLDERS = frozenset({"do_block", "block", "source", "stab_clause"})


def _elixir_call_target_name(call_node: Node, src: str) -> str | None:
    """The keyword or function name in an Elixir ``call`` node's target."""
    if call_node.type != "call":
        return None
    target = call_node.child_by_field_name("target")
    if target is None or target.type != "identifier":
        return None
    return node_text(target, src).strip() or None


def _elixir_call_is_definitional(node: Node, src: str) -> bool:
    """True when an Elixir ``call`` node defines or describes, never invokes.

    Two shapes reach the call queries and must not become graph edges, and
    neither can be excluded by a query predicate, which sees only the node's
    own text and not its parent:

    * a definition head -- ``def add(a, b)`` puts ``add(a, b)`` in the
      ``arguments`` of a call to ``def``, so the head reads as a call to the
      function being defined (a self-edge on every function in the repo). The
      ``when``-guard form wraps that head in a ``binary_operator`` first.
    * a module attribute -- ``@doc "…"`` is a call to ``doc``, and any
      ``@whatever value`` is a call to ``whatever``. Inside a typespec
      attribute the body is types, so its calls are dropped too; inside any
      other attribute the body is ordinary code and keeps its edges.
    """
    head = node
    parent = head.parent
    if parent is not None and parent.type == "binary_operator":
        operator = parent.child_by_field_name("operator")
        if (
            operator is not None
            and operator.type == "when"
            and parent.child_by_field_name("left") is not None
            and parent.child_by_field_name("left").id == head.id
        ):
            head, parent = parent, parent.parent
    if parent is not None and parent.type == "arguments":
        outer = parent.parent
        named = parent.named_children
        if (
            outer is not None
            and named
            and named[0].id == head.id
            and _elixir_call_target_name(outer, src) in _ELIXIR_DEFINITION_KEYWORDS
        ):
            return True

    # Walk out to the `@` unary_operator that opens this statement. Bounded by
    # the statement rather than by a hop count: a union return type nests one
    # binary_operator per member, so `@spec f(t) :: a() | b() | c() | d()` sits
    # deeper than any fixed budget and used to leak its type names as calls.
    ancestor: Node | None = node
    while ancestor is not None:
        holder = ancestor.parent
        if holder is None or holder.type in _ELIXIR_STATEMENT_HOLDERS:
            return False
        if holder.type == "unary_operator":
            operator = holder.child_by_field_name("operator")
            if operator is not None and operator.type == "@":
                if ancestor.id == node.id:
                    return True
                return _elixir_call_target_name(ancestor, src) in _ELIXIR_TYPESPEC_ATTRIBUTES
        ancestor = holder
    return False


def _elixir_definition_alias(call_node: Node, src: str) -> str | None:
    """The first ``alias`` argument of a definition call, i.e. its module name."""
    arguments = next(
        (child for child in call_node.named_children if child.type == "arguments"), None
    )
    if arguments is None or not arguments.named_children:
        return None
    first = arguments.named_children[0]
    if first.type != "alias":
        return None
    return node_text(first, src).strip() or None


def _elixir_defimpl_for_type(call_node: Node, src: str) -> str | None:
    """The ``for:`` type of a ``defimpl Proto, for: Type`` block."""
    arguments = next(
        (child for child in call_node.named_children if child.type == "arguments"), None
    )
    if arguments is None:
        return None
    for child in arguments.named_children:
        if child.type != "keywords":
            continue
        for pair in child.named_children:
            key = pair.child_by_field_name("key")
            value = pair.child_by_field_name("value")
            if key is None or value is None:
                continue
            if node_text(key, src).strip().rstrip(":") == "for":
                return node_text(value, src).strip() or None
    return None


def _elixir_enclosing_module_alias(node: Node, src: str) -> str | None:
    """The nearest enclosing ``defmodule``/``defprotocol`` name."""
    ancestor = node.parent
    while ancestor is not None:
        if _elixir_call_target_name(ancestor, src) in ("defmodule", "defprotocol"):
            return _elixir_definition_alias(ancestor, src)
        ancestor = ancestor.parent
    return None


def _elixir_defimpl_name(call_node: Node, src: str) -> str | None:
    """The module name the compiler generates for a ``defimpl`` block.

    ``defimpl Jason.Encoder, for: Tuple`` compiles to a module called
    ``Jason.Encoder.Tuple``, and that name is used here. Without it the
    definitions inside the block are attributed to whatever module encloses it,
    where they read as unused exports of a module that never declared them, and
    two implementations of one protocol in one file mint two symbols under one
    id.

    The ``for:``-less form is only legal inside a ``defmodule`` and implements
    the protocol for that module, so the enclosing module supplies the type.
    """
    protocol = _elixir_definition_alias(call_node, src)
    if protocol is None:
        return None
    target_type = _elixir_defimpl_for_type(call_node, src) or _elixir_enclosing_module_alias(
        call_node, src
    )
    return f"{protocol}.{target_type}" if target_type else protocol


def _elixir_symbol_name(def_node: Node, default_name: str, src: str) -> str:
    """The name a captured Elixir definition carries.

    Only ``defimpl`` differs from its captured text; everything else names
    itself. See ``_elixir_defimpl_name``.
    """
    if _elixir_call_target_name(def_node, src) != "defimpl":
        return default_name
    return _elixir_defimpl_name(def_node, src) or default_name


def _elixir_is_template_definition(def_node: Node, src: str) -> bool:
    """True for a definition inside ``quote do ... end``.

    A ``def`` in a ``quote`` block is the body of a macro: it defines nothing in
    the module that writes it and everything in the module that later invokes
    the macro, which is a module no parser can name. Captured as a symbol it
    reads as a method of the wrong module, and then as an unused export of it.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if _elixir_call_target_name(ancestor, src) == "quote":
            return True
        ancestor = ancestor.parent
    return False


def _elixir_module_parent(def_node: Node, src: str) -> str | None:
    """Return the enclosing ``defmodule``/``defprotocol`` name, or None.

    The generic nesting walk in ``ASTParser._find_parent`` reads
    ``ancestor.child_by_field_name("name")``, and an Elixir ``call`` has no
    ``name`` field -- the module name is the first argument of a call to
    ``defmodule``. Nesting is the only thing that says which module a
    function belongs to, so without this every ``def`` reads as a free
    function.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        keyword = _elixir_call_target_name(ancestor, src)
        if keyword in _ELIXIR_MODULE_KEYWORDS:
            if keyword == "defimpl":
                return _elixir_defimpl_name(ancestor, src)
            return _elixir_definition_alias(ancestor, src)
        ancestor = ancestor.parent
    return None


def _qualified_pascal_parent(name_node: Node, src: str) -> str | None:
    """Return the owning class for a Pascal out-of-line method header.

    ``function TCalculator.Add(...): Integer;`` in an implementation
    section is captured by pascal.scm's ``genericDot`` patterns, where the
    bare ``@symbol.name`` is the ``rhs`` (``Add``) and the qualifying type
    lives in the sibling ``lhs`` field (``TCalculator``). Nesting-based
    ``_find_parent`` can't see this: the ``defProc`` node sits in the
    unit's implementation section, physically outside the class's
    ``declType`` body declared in the interface section. Handles both the
    plain (``genericDot rhs: identifier``) and generic-method
    (``genericDot rhs: genericTpl entity: identifier``) query shapes.

    Uses ``node_text`` (tree-sitter's own byte-accurate decode), not raw
    ``src`` byte-offset slicing — Pascal identifiers and unit names are
    frequently non-ASCII (Cyrillic) in this codebase's real-world sources,
    and slicing a decoded ``str`` by *byte* offsets misaligns on any
    multi-byte character.

    Returns ``None`` when the name node isn't inside a qualified header
    (i.e. a free function/procedure).
    """
    parent = name_node.parent
    if parent is not None and parent.type == "genericTpl":
        parent = parent.parent
    if parent is None or parent.type != "genericDot":
        return None
    lhs = parent.child_by_field_name("lhs")
    if lhs is None:
        return None
    text = node_text(lhs, src).strip()
    return text or None


def _build_qualified_name(file_path: str, parent_name: str | None, name: str) -> str:
    module = Path(file_path).with_suffix("").as_posix().replace("/", ".")
    if parent_name:
        return f"{module}.{parent_name}.{name}"
    return f"{module}.{name}"


_PASCAL_USES_IN_CLAUSE_RE = re.compile(rb"\bin\b[ \t]*'(?:[^'\r\n]|'')*'")


def _sanitize_pascal_project_source(source: bytes) -> bytes:
    """Blank Delphi/FPC project-file ``unit in 'path.pas'`` clauses.

    ``.dpr``/``.dpk``/``.lpr`` project files map each unit to its source
    path right in the ``uses`` clause -- ``uses SysUtils, MyUnit in
    'src\\MyUnit.pas';`` -- and Delphi's IDE writes this automatically for
    every unit added to a project, making it the norm rather than the
    exception in real ``.dpr``/``.dpk`` files (confirmed against this
    repo's own ``MTN2.dpr``: every non-RTL unit uses it).

    tree-sitter-pascal's grammar has no rule for the trailing ``in
    '...'`` at all. Hitting it mid-``declUses`` doesn't just fail that
    one unit -- the parser's error recovery folds the ``in``, the path
    string, and every subsequent comma-separated unit into one corrupted
    ``moduleName`` node spanning to wherever it happens to resync, so a
    single ``in`` clause was silently swallowing the rest of the ``uses``
    list (observed on ``MTN2.dpr``: 4 imports extracted instead of ~80,
    the 4th holding several KB of raw multi-line garbage as its
    ``module_path``). This is an upstream grammar gap, not something a
    ``.scm`` query can route around -- the AST itself is malformed before
    any query runs.

    Blanks the matched span with spaces (never a raw newline -- a Pascal
    string literal can't contain one, so no line is fully consumed) to
    preserve every other byte offset in the file, so line numbers for
    symbols/imports/calls elsewhere are unaffected. `'ABC'` doesn't need
    the doubled-quote (`''`) escape handled specially for *finding* the
    end of the string here (the regex already treats `''` as staying
    inside the literal), only for correctness of the match's own extent.

    Scoped to project files specifically: this syntax is invalid outside
    a ``uses`` clause and ``.pas``/``.pp`` unit files can't legally carry
    it, so there's nothing to blank there and no reason to run the regex
    over every unit file in a codebase.
    """
    return _blank_matches(source, _PASCAL_USES_IN_CLAUSE_RE)


def _blank_matches(source: bytes, pattern: re.Pattern[bytes]) -> bytes:
    """Overwrite every match of *pattern* with spaces, keeping the length.

    The shared half of every byte-preserving source sanitizer: a construct the
    grammar has no rule for is replaced in place, never removed, so line
    numbers and byte offsets for everything else in the file survive. Spaces
    and never a newline, so no pattern can consume a line break it did not
    match.
    """
    if not pattern.search(source):
        return source
    out = bytearray(source)
    for match in pattern.finditer(source):
        start, end = match.span()
        out[start:end] = b" " * (end - start)
    return bytes(out)


_PASCAL_PROJECT_EXTENSIONS = (".dpr", ".dpk", ".lpr")


def prepare_pascal_source(source: bytes, path: str | None) -> bytes:
    """Single entry point for every Pascal byte-preserving sanitizer.

    Called from :func:`~.sfc_source.prepare_source` -- the same
    registry-dispatched hook every other tree-sitter consumer (the
    ingestion parser, plus the complexity/dataflow/duplication health
    walkers) already calls before handing bytes to a ``Parser`` -- rather
    than parser.py special-casing Pascal in its own if-blocks. That keeps
    ``docs/architecture/language-support.md``'s "zero changes to
    parser.py" promise for a new language, and means the health walkers
    get the same clean projection the ingestion parser does instead of
    parsing raw bytes.

    Only wraps ``_sanitize_pascal_project_source`` (``.dpr``/``.dpk``/
    ``.lpr`` ``in '...'`` clauses), gated on *path*'s extension since that
    syntax is invalid in a plain unit file. An earlier revision of this
    function also blanked whatever an anonymous ``array[...] of record``
    element type's parse errors touched, discovered via ERROR-node spans
    from a throwaway parse. Dropped after review (PR #1353): tree-sitter's
    error recovery for that construct doesn't cleanly wrap the bad
    construct in one ERROR node -- on the reviewer's repro, one of the
    spans it found was the class's own legitimate closing ``end;``, and
    blanking it produced the exact same broken structure (the following
    method detached from its class) as running no sanitizer at all. A
    correct fix needs a nesting-aware nested-record/variant-part scanner,
    which is more surface area than one occurrence in one file (see the
    dropped function's own docstring) justifies; the anon-record case is
    left to degrade to a wrong parent for that one class, same as any
    other unhandled grammar gap.
    """
    if path and path.lower().endswith(_PASCAL_PROJECT_EXTENSIONS):
        return _sanitize_pascal_project_source(source)
    return source


def _dedupe_pascal_interface_symbols(
    symbols: list[Symbol], node_types: list[str]
) -> list[Symbol]:
    """Drop an interface-section method signature once its implementation
    is also present, so the two don't become two graph nodes for one method.

    Pascal declares a method's signature once in the ``interface`` section
    (``declProc``, no body) and its full body once in the
    ``implementation`` section (``defProc``) — two distinct physical AST
    nodes pascal.scm both legitimately captures (see the query file's
    comment). Once ``_find_parent`` (nesting) and ``_qualified_pascal_parent``
    (the ``TFoo.Method`` header) resolve both to the same
    ``(parent_name, name)``, keep only the ``defProc`` version: it carries
    the real body, which is what ``get_symbol`` should return, and the
    ``declProc`` duplicate would otherwise leave two ``Add`` nodes in the
    graph for one logical method.

    Keyed on ``(parent_name, signature)`` — normalized, see
    ``_pascal_dedupe_key`` — rather than just ``(parent_name, name)`` so
    that Pascal ``overload;`` siblings (same name, different parameter
    lists) are told apart: an interface-only overload must survive even
    when a *different* overload of the same name has a same-file
    implementation (verified against a reproduction where a 2-overload
    class with only one variant implemented was silently losing the
    other variant's interface declaration).

    Normalization matters in practice, not just in theory: scanned
    against a real ~150-file Delphi codebase, 168 method pairs shared a
    class+name but escaped a raw-signature-text match — almost all of
    them a long parameter list wrapped across lines differently between
    the compact interface declaration and the implementation (extremely
    common Delphi formatting), a handful differing only by identifier
    case (Pascal is case-insensitive, so ``TFoo.Add`` and
    ``TFOO.ADD`` name the same method). ``_pascal_dedupe_key`` strips all
    whitespace and lowercases before comparing so both collapse
    correctly.

    Still imperfect: Pascal's compiler doesn't require parameter *names*
    to match between an interface declaration and its implementation
    (only the types, for overload resolution), so a same-file rename
    between the two still produces different normalized keys and defeats
    this dedup, leaving both symbols. Unlike the whitespace/case cases
    above, no evidence of this actually happening was found in the real
    codebase this was checked against — left as a documented gap rather
    than parsing parameter types out of ``declArgs`` for an exact match.
    """
    impl_keys = {
        _pascal_dedupe_key(s.parent_name, s.signature)
        for s, nt in zip(symbols, node_types, strict=True)
        if nt == "defProc"
    }
    return [
        s
        for s, nt in zip(symbols, node_types, strict=True)
        if not (nt == "declProc" and _pascal_dedupe_key(s.parent_name, s.signature) in impl_keys)
    ]


def _pascal_dedupe_key(parent_name: str | None, signature: str) -> tuple[str | None, str]:
    """Normalize a (parent, signature) pair for Pascal's interface/impl dedup.

    Whitespace-insensitive (multi-line parameter lists get reformatted
    between the interface declaration and the implementation constantly
    in real Delphi code) and case-insensitive (identifiers are
    case-insensitive in Pascal, so this needs to hold for the *class*
    name half of the key too, not just the signature).
    """
    parent_key = parent_name.lower() if parent_name else None
    sig_key = _WHITESPACE_RE.sub("", signature).lower()
    return (parent_key, sig_key)


# ---------------------------------------------------------------------------
# Objective-C helpers
# ---------------------------------------------------------------------------

_OBJC_METHOD_NODE_TYPES = frozenset({"method_declaration", "method_definition"})
_OBJC_CLASS_NODE_TYPES = frozenset({"class_interface", "class_implementation"})

# A declaration and the definition that answers it, keyed the way the dedup
# below pairs them.
_OBJC_DEFINITION_NODE_TYPES = frozenset({"class_implementation", "method_definition"})
_OBJC_DECLARATION_NODE_TYPES = frozenset({"class_interface", "method_declaration"})

# Macros that expand to nothing but are spelled as a bare identifier, so the
# grammar makes them a keyword child of the method they trail.
_OBJC_TRAILING_MACRO_NAMES = frozenset({
    "NS_DESIGNATED_INITIALIZER", "NS_UNAVAILABLE", "NS_REQUIRES_SUPER",
    "NS_RETURNS_INNER_POINTER", "NS_REFINED_FOR_SWIFT", "NS_SWIFT_UI_ACTOR",
    "NS_REQUIRES_NIL_TERMINATION", "UNAVAILABLE_ATTRIBUTE", "DEPRECATED_ATTRIBUTE",
})

# ``typedef NS_ENUM(NSInteger, Kind) { KindA }`` shapes. See _objc_is_macro_enum.
_OBJC_ENUM_MACRO_NAMES = frozenset({
    "NS_ENUM", "NS_OPTIONS", "NS_CLOSED_ENUM", "NS_ERROR_ENUM",
    "CF_ENUM", "CF_OPTIONS", "CF_CLOSED_ENUM",
})


def _objc_selector_name(def_node: Node, default_name: str, src: str) -> str:
    """Join a method's keyword parts into its full selector.

    A keyword is an ``identifier`` child that a ``method_parameter`` follows;
    anything trailing the last parameter is an attribute macro, not part of
    the name. See the Objective-C section of the architecture doc.
    """
    if def_node.type not in _OBJC_METHOD_NODE_TYPES:
        return default_name
    parts: list[str] = []
    pending: str | None = None
    for child in def_node.children:
        if child.type == "identifier":
            # Only the identifier immediately before a method_parameter is a
            # keyword; a bare trailing one is a macro like
            # NS_DESIGNATED_INITIALIZER, which would otherwise join into the
            # selector and stop the header pairing with its implementation.
            pending = _node_text(child, src).strip() or None
        elif child.type == "method_parameter":
            if pending is not None:
                parts.append(pending)
                pending = None
        elif child.type in ("compound_statement", ";"):
            break
    if parts:
        return "".join(f"{p}:" for p in parts)
    # No parameters at all: the selector is the first keyword, unadorned.
    first = next((c for c in def_node.children if c.type == "identifier"), None)
    if first is None:
        return default_name
    text = _node_text(first, src).strip()
    if not text or text in _OBJC_TRAILING_MACRO_NAMES:
        return default_name
    return text


def _objc_is_macro_enum(def_node: Node, src: str) -> bool:
    """True for a ``typedef NS_ENUM(Type, Name) { … }`` typedef.

    This grammar has no rule for the macro, so the name argument and the brace
    body land in ERROR nodes and only the *enumerators* survive as
    ``declarator`` children. Capturing those mints a symbol per enum case named
    as though it were the type, and never one for the type itself, so the whole
    typedef is skipped: no symbol beats a wrong one.
    """
    if def_node.type != "type_definition":
        return False
    for child in def_node.children:
        if child.type != "macro_type_specifier":
            continue
        name = child.child_by_field_name("name")
        if name is not None and _node_text(name, src).strip() in _OBJC_ENUM_MACRO_NAMES:
            return True
    return False


def _objc_category_name(def_node: Node, default_name: str, src: str) -> str:
    """Name a category for the class it extends plus its own name.

    Two categories on one class are two declarations about it, so naming both
    for the class alone would merge them. A class extension (``Foo ()``) binds
    no ``category`` field and keeps the plain name.
    """
    if def_node.type not in _OBJC_CLASS_NODE_TYPES:
        return default_name
    category = def_node.child_by_field_name("category")
    if category is None:
        return default_name
    text = _node_text(category, src).strip()
    return f"{default_name}({text})" if text else default_name


def _objc_symbol_name(def_node: Node, default_name: str, src: str) -> str:
    """The name a captured Objective-C definition carries."""
    if def_node.type in _OBJC_METHOD_NODE_TYPES:
        return _objc_selector_name(def_node, default_name, src)
    return _objc_category_name(def_node, default_name, src)


def _objc_container_node(def_node: Node, container_types: frozenset[str]) -> Node | None:
    """The ``@interface`` / ``@implementation`` / ``@protocol`` around a member."""
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in container_types:
            return ancestor
        ancestor = ancestor.parent
    return None


def _objc_container_parent(def_node: Node, container_types: frozenset[str], src: str) -> str | None:
    """The name of the container a method or property is declared in.

    The generic nesting walk finds the same ancestor and then reads a ``name``
    field these nodes do not have: each carries its name as a bare first
    ``identifier`` child. Members of a category take the category's own name.
    """
    container = _objc_container_node(def_node, container_types)
    if container is None:
        return None
    identifier = next((c for c in container.children if c.type == "identifier"), None)
    if identifier is None:
        return None
    name = _node_text(identifier, src).strip()
    return _objc_category_name(container, name, src) if name else None


def _objc_message_selector(site_node: Node, target_node: Node, src: str) -> str | None:
    """Join a message send's keyword parts, or None for a repeat match.

    ``[view setTitle:t forState:s]`` binds one ``method:`` child per keyword,
    so the one query pattern matches once per keyword. Returns the joined
    selector for the match carrying the first keyword and None for every later
    one, so the extra matches are dropped rather than emitted as targets that
    name nothing. The node's own ``:`` tokens say whether the selector takes
    arguments: ``[obj run]`` is ``run`` and ``[obj run:x]`` is ``run:``.
    """
    methods = site_node.children_by_field_name("method")
    if not methods or methods[0].id != target_node.id:
        return None
    parts = [_node_text(m, src).strip() for m in methods]
    parts = [p for p in parts if p]
    if not parts:
        return None
    takes_arguments = any(c.type == ":" for c in site_node.children)
    if not takes_arguments:
        return parts[0]
    return "".join(f"{p}:" for p in parts)


def _objc_call_is_block_variable(site_node: Node, target_name: str, src: str) -> bool:
    """True when a bare-identifier call invokes a block held in a variable.

    Objective-C invokes a block with C call syntax, so ``completionBlock(hit)``
    is a ``call_expression`` on a bare identifier that nothing in the grammar
    separates from a call to a C function. Names like this are ubiquitous as
    both a parameter and a ``@property``, so without the scope check the
    resolver binds the invocation to an unrelated class's property.
    """
    node: Node | None = site_node
    while node is not None:
        if node.type == "compound_statement":
            for statement in node.named_children:
                if statement.type == "declaration" and _objc_declares_name(
                    statement, target_name, src
                ):
                    return True
        elif node.type == "method_definition":
            return any(
                child.type == "method_parameter"
                and _objc_declares_name(child, target_name, src)
                for child in node.children
            )
        elif node.type == "function_definition":
            declarator = node.child_by_field_name("declarator")
            return declarator is not None and _objc_declares_name(declarator, target_name, src)
        node = node.parent
    return False


def _objc_declares_name(node: Node, name: str, src: str) -> bool:
    """True when *node* binds *name* as a parameter or a variable.

    A declarator nests (``void (^block)(int)``, ``SDBlock done = ^{}``), so
    every identifier under the node is checked rather than only the direct
    children. The node is a parameter or a declaration, never a body, so the
    walk stays small; a nested block body is skipped for the same reason.
    """
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type in ("identifier", "field_identifier"):
            if _node_text(current, src).strip() == name:
                return True
        elif current.type != "compound_statement":
            stack.extend(current.children)
    return False


def _objc_container_family(container_kind: str | None) -> str | None:
    """Which namespace a member belongs to: a protocol's, or a class's.

    An ``@interface`` and its ``@implementation`` are the same class, so a
    declaration in one must still dedupe against a definition in the other; a
    ``@protocol`` of the same name is a different thing entirely.
    """
    return "protocol" if container_kind == "protocol_declaration" else container_kind and "class"


def _dedupe_objc_interface_symbols(
    symbols: list[Symbol], node_types: list[str], container_kinds: list[str | None]
) -> list[Symbol]:
    """Collapse a declaration into the definition standing beside it.

    A ``.m`` routinely opens with a class extension (``@interface Foo ()``)
    declaring the private methods its ``@implementation Foo`` then defines, so
    each builds the same symbol id twice in one file. The definition wins: it
    carries the body. Across two files the split is real and both symbols are
    kept, marked with ``is_declaration`` the way a C header's prototype and its
    ``.c`` definition already are.

    The key carries the container kind because a protocol and a class may share
    a name in one file, and ``-ping`` on the protocol is then a different method
    from ``-ping`` on the class. Keyed on the name and not the signature:
    Objective-C has no overloading, and a declaration and its definition
    routinely differ in parameter names and nullability annotations.
    """
    definition_keys = {
        (_objc_container_family(kind), s.parent_name, s.name)
        for s, nt, kind in zip(symbols, node_types, container_kinds, strict=True)
        if nt in _OBJC_DEFINITION_NODE_TYPES
    }
    seen_declarations: set[tuple[str | None, str | None, str]] = set()
    kept: list[Symbol] = []
    for symbol, node_type, kind in zip(symbols, node_types, container_kinds, strict=True):
        if node_type in _OBJC_DECLARATION_NODE_TYPES:
            key = (_objc_container_family(kind), symbol.parent_name, symbol.name)
            if key in definition_keys or key in seen_declarations:
                continue
            seen_declarations.add(key)
        kept.append(symbol)
    return kept


# Whole-line macros that expand to nothing a parser needs but which this
# grammar reads as the start of a C declaration, swallowing whatever follows
# into an ERROR node. Only bare whole-line forms are listed: a macro that
# opens a real declaration (``FOUNDATION_EXPORT NSString *const kFoo;``) must
# stay, and a call-shaped one (``NS_ENUM(NSInteger, Kind)``) is a different
# problem that blanking a line cannot fix.
_OBJC_BARE_MACRO_LINE_RE = re.compile(
    rb"^[ \t]*(?:NS_ASSUME_NONNULL_BEGIN|NS_ASSUME_NONNULL_END"
    rb"|NS_HEADER_AUDIT_BEGIN\([^)\r\n]*\)|NS_HEADER_AUDIT_END"
    rb"|CF_ASSUME_NONNULL_BEGIN|CF_ASSUME_NONNULL_END"
    rb"|NS_REFINED_FOR_SWIFT|NS_SWIFT_UI_ACTOR)[ \t]*(?=\r?\n|$)",
    re.MULTILINE,
)


def prepare_objectivec_source(source: bytes) -> bytes:
    """Blank whole-line nullability-audit macros, preserving every offset.

    ``NS_ASSUME_NONNULL_BEGIN`` is not a preprocessor directive and this
    grammar has no rule for it, so it parses as the opening of a C declaration
    and error recovery folds the whole ``@interface … @end`` block after it
    into one ERROR node. The macro wraps almost every modern Objective-C
    header, so this is the dominant parse failure in real source.
    """
    return _blank_matches(source, _OBJC_BARE_MACRO_LINE_RE)


# ---------------------------------------------------------------------------
# Type reference helpers (used by _extract_type_refs)
# ---------------------------------------------------------------------------

_PARAM_ORIGIN_BY_ANCESTOR: dict[str, str] = {
    "type_argument_list": "generic_argument",
    "typeof_expression": "typeof",
    "constructor_declaration": "ctor_param",
    "method_declaration": "method_param",
    "delegate_declaration": "delegate_param",
    "record_declaration": "ctor_param",
    "class_declaration": "ctor_param",
    "struct_declaration": "ctor_param",
    # Go type positions — node types are Go-only so they never collide with
    # the C# entries above. The origin is provenance only; resolution treats
    # all type-use edges equally.
    "field_declaration": "field_type",
    "parameter_declaration": "param_type",
    "composite_literal": "composite_literal",
    # TypeScript / JavaScript type positions. Walk-up matches the nearest
    # enclosing declaration; the parameter / field / heritage nodes sit
    # closer than ``class_declaration`` so this dispatch is unambiguous.
    "required_parameter": "param_type",
    "optional_parameter": "param_type",
    "property_signature": "field_type",
    "public_field_definition": "field_type",
    "function_declaration": "return_type",
    "method_definition": "return_type",
    "method_signature": "return_type",
    "arrow_function": "return_type",
    "function_signature": "return_type",
    "type_alias_declaration": "type_alias",
    "type_parameter": "generic_constraint",
    "extends_clause": "extends",
    "extends_type_clause": "extends",
    "implements_clause": "implements",
    # JVM (Java + Kotlin) type positions. Note: ``parameter`` is NOT
    # mapped here because tree-sitter-c-sharp also uses ``parameter``
    # for ctor params and adding it would override C#'s walk to the
    # enclosing ``constructor_declaration``. Kotlin function-parameter
    # origin therefore falls through to ``function_declaration`` →
    # "return_type" (imprecise but harmless — origin is provenance only).
    "formal_parameter": "param_type",
    "object_creation_expression": "composite_literal",
    "local_variable_declaration": "field_type",
    "superclass": "extends",
    "super_interfaces": "implements",
    "type_list": "implements",
    "class_parameter": "ctor_param",  # Kotlin primary-ctor parameter
    "variable_declaration": "field_type",  # Kotlin property declaration
    "delegation_specifier": "extends",  # Kotlin class : Bar()
    # Pascal type positions — node types are Pascal-only, no collision risk.
    "declField": "field_type",
    "declArg": "param_type",
    "declVar": "local_var_type",
    "declProc": "return_type",
    "exprArgs": "framework_ctor",  # Application.CreateForm(TClass, Var)
}


def _head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a C# type expression, or None.

    Examples:
        ``IBasketService``                  → "IBasketService"
        ``IList<Basket>``                   → "IList"
        ``Acme.Catalog.IRepository<T>``     → "IRepository"
        ``ref readonly Span<byte>``         → "Span"
        ``string``                          → None (built-in)
        ``int?``                            → None
        ``T``                               → None (likely a generic param)

    The point of returning the head identifier is that the
    DotNetProjectIndex type-name lookup is keyed by unqualified type
    name. Generic-arg recursion is intentionally NOT done here — each generic
    argument is captured from its own ``type_argument_list`` node, including
    nested arguments and invocation-only type uses.
    """
    head_node: Node | None = type_node

    # Unwrap modifier wrappers: nullable_type, ref_type, pointer_type,
    # array_type, tuple_type. tree-sitter-c-sharp puts the inner type
    # at field "type" or as the first non-trivia child.
    for _ in range(6):
        if head_node is None:
            return None
        if head_node.type in ("nullable_type", "ref_type", "pointer_type", "array_type"):
            inner = head_node.child_by_field_name("type")
            if inner is None:
                # Fall back to first identifier-bearing child
                inner = next(
                    (
                        c
                        for c in head_node.children
                        if c.type not in (",", "?", "*", "&", "ref", "out", "in", "[", "]")
                    ),
                    None,
                )
            head_node = inner
            continue
        break

    if head_node is None:
        return None

    if head_node.type == "identifier" or head_node.type == "predefined_type":
        text = _node_text(head_node, src)
    elif head_node.type == "generic_name":
        name_child = head_node.child_by_field_name("name") or next(
            (c for c in head_node.children if c.type == "identifier"),
            None,
        )
        text = _node_text(name_child, src) if name_child else ""
    elif head_node.type == "qualified_name":
        # `Foo.Bar.Baz` — take the rightmost identifier
        idents = [c for c in head_node.children if c.type == "identifier"]
        text = _node_text(idents[-1], src) if idents else ""
    elif head_node.type == "tuple_type":
        return None  # Tuple elements aren't single types
    else:
        # Unknown shape — fall back to first identifier in the subtree
        ident = _first_descendant(head_node, "identifier")
        text = _node_text(ident, src) if ident else ""

    return text if is_resolvable_type_name(text, "csharp") else None


def _first_descendant(node: Node, type_name: str) -> Node | None:
    stack = [node]
    while stack:
        current = stack.pop()
        if current.type == type_name:
            return current
        stack.extend(current.children)
    return None


def _classify_param_origin(type_node: Node) -> str:
    """Walk up to find the enclosing declaration and map to an origin tag.

    The walk stops at the first matching ancestor or after a small depth
    cap. Falling off the cap means the capture was outside a recognised
    declaration shape (shouldn't happen given the query patterns, but
    guards against grammar drift); we tag those ``method_param``.
    """
    cur: Node | None = type_node
    for _ in range(8):
        if cur is None:
            break
        origin = _PARAM_ORIGIN_BY_ANCESTOR.get(cur.type)
        if origin is not None:
            return origin
        cur = cur.parent
    return "method_param"


# ---------------------------------------------------------------------------
# Go type-reference head extraction
# ---------------------------------------------------------------------------

def _go_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type name of a Go type expression, or None.

    Unwraps the modifier shells Go layers around a named type so the
    resolver sees the bare identifier it indexes by:

        ``Options``                 → "Options"
        ``*Cache``                  → "Cache"
        ``[]Partition``             → "Partition"
        ``map[string]Config``       → "Config"   (value type; string filtered)
        ``chan Event``              → "Event"
        ``dynacache.Cache``         → "Cache"     (qualifier dropped)
        ``List[Inner]``             → "List"      (generic head)
        ``string`` / ``int``        → None        (builtin)
        ``(Foo, error)``            → None        (parameter_list; the inner
                                                   declarations are captured
                                                   separately)
        ``interface{...}`` / ``struct{...}`` / ``func(...)`` → None (anonymous)

    The qualifier in ``pkg.Cache`` is intentionally dropped: the Go type-ref
    strategy resolves the bare name against same-package siblings and
    imported package files, mirroring the Rust strategy. Keeping the head
    name unqualified matches the symbol-index keys.
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind == "qualified_type":
            name = node.child_by_field_name("name") or next(
                (c for c in reversed(node.children) if c.type == "type_identifier"),
                None,
            )
            text = _node_text(name, src) if name else ""
            break
        if kind == "generic_type":
            node = node.child_by_field_name("type")
            continue
        if kind in ("slice_type", "array_type"):
            node = node.child_by_field_name("element")
            continue
        if kind == "map_type":
            node = node.child_by_field_name("value")
            continue
        if kind == "channel_type":
            node = node.child_by_field_name("value")
            continue
        if kind in ("pointer_type", "parenthesized_type"):
            # No field name on the inner type — take the first named child.
            node = node.named_children[0] if node.named_children else None
            continue
        # parameter_list (multi-return), interface_type, struct_type,
        # function_type, and anything else: no single named type to resolve.
        return None
    else:
        return None

    return text if is_resolvable_type_name(text, "go") else None


# ---------------------------------------------------------------------------
# C / C++ type-reference head extraction
# ---------------------------------------------------------------------------

def _c_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type name of a C / C++ type expression, or None.

    In C the pointer / array shells wrap the *declarator*, not the type,
    so the captured ``type:`` field is the bare type node:

        ``JSON_Value``                  → "JSON_Value"
        ``struct JSON_Object``          → "JSON_Object"  (named struct ref)
        ``int`` / ``unsigned long``     → None           (primitive)
        ``size_t``                      → None           (stdlib typedef)
        ``Acme::Widget`` (C++)          → "Widget"       (rightmost name)
        ``std::vector<T>`` (C++)        → "vector"       (template head)
        anonymous ``struct {...}``      → None
    """
    node: Node | None = type_node
    text = ""
    for _ in range(6):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind in ("primitive_type", "sized_type_specifier"):
            return None
        if kind in ("struct_specifier", "union_specifier", "enum_specifier", "class_specifier"):
            name = node.child_by_field_name("name")
            if name is None:
                return None  # anonymous aggregate — no named type to resolve
            text = _node_text(name, src)
            break
        if kind == "template_type":
            node = node.child_by_field_name("name")
            continue
        if kind == "qualified_identifier":
            # ``NS::Type`` — take the rightmost name component.
            name = node.child_by_field_name("name")
            node = name if name is not None else (
                node.named_children[-1] if node.named_children else None
            )
            continue
        # type_qualifier (const/volatile) wrappers and anything else:
        # descend into the first named child.
        node = node.named_children[0] if node.named_children else None
    else:
        return None

    # cpp shares this extractor; the builtin set is identical for both.
    return text if is_resolvable_type_name(text, "c") else None


# ---------------------------------------------------------------------------
# TypeScript / JavaScript type-reference head extraction
# ---------------------------------------------------------------------------

def _ts_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a TypeScript/JavaScript type, or None.

    Unwraps the modifier shells TS layers around a named type so the
    resolver sees the bare identifier:

        ``Foo``                 → "Foo"
        ``Foo[]``               → "Foo"      (array_type)
        ``Promise<Foo>``        → "Promise"  → filtered (builtin)
        ``ns.Foo``              → "Foo"      (nested_type_identifier)
        ``Foo | null``          → None       (union — ambiguous head)
        ``(x: A) => B``         → None       (function_type — A/B are
                                              captured separately as their
                                              own param/return positions)
        ``{ x: number }``       → None       (anonymous object type)
        ``string`` / ``number`` → None       (predefined / builtin)
        ``T``                   → None       (single-uppercase generic)

    Union / intersection / function / object types return None because
    the head isn't a single name; the underlying parameter / field
    captures for each leaf already produced their own ``@param.type``
    captures so the bare leaves are still resolved.
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind == "identifier":
            # ``extends_clause`` of a class uses ``identifier`` (E in
            # ``class D extends E``); treat it as a type name.
            text = _node_text(node, src)
            break
        if kind == "predefined_type":
            return None
        if kind in ("union_type", "intersection_type", "function_type",
                    "constructor_type", "object_type", "literal_type",
                    "tuple_type", "conditional_type", "mapped_type",
                    "index_type_query", "type_query", "lookup_type",
                    "template_literal_type", "infer_type", "readonly_type"):
            return None
        if kind == "generic_type":
            # ``Foo<T>`` — descend to the bare name; generic args are
            # captured separately if they hold user types.
            inner = node.child_by_field_name("name") or next(
                (c for c in node.named_children if c.type != "type_arguments"),
                None,
            )
            node = inner
            continue
        if kind == "nested_type_identifier":
            # ``ns.Foo`` — rightmost name component is the type itself.
            name = node.child_by_field_name("name") or next(
                (c for c in reversed(node.named_children)
                 if c.type == "type_identifier"),
                None,
            )
            node = name
            continue
        if kind == "array_type":
            # ``T[]`` — element is the first named child.
            node = next(iter(node.named_children), None)
            continue
        if kind == "parenthesized_type":
            node = next(iter(node.named_children), None)
            continue
        if kind == "type_annotation":
            # Shouldn't be reached given the query strips the annotation,
            # but defensive: descend past the colon to the type itself.
            node = next(iter(node.named_children), None)
            continue
        if kind == "constraint":
            # ``extends Cons`` inside a type_parameter — the constraint
            # node wraps the actual constraint type.
            node = next(iter(node.named_children), None)
            continue
        # Anything else (type_predicate, asserts, type_assertion, ...)
        # — descend into the first named child and re-classify.
        node = next(iter(node.named_children), None)
    else:
        return None

    return text if is_resolvable_type_name(text, "typescript") else None


# ---------------------------------------------------------------------------
# Java type-reference head extraction
# ---------------------------------------------------------------------------

def _java_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head type identifier of a Java type expression, or None.

    Examples:
        ``Bar``                         → "Bar"
        ``java.util.List<Foo>``         → "List"   → filtered (builtin)
        ``com.x.y.Z``                   → "Z"
        ``Foo.Bar``                     → "Bar"   (inner type)
        ``Foo[]``                       → "Foo"
        ``int`` / ``void`` / ``long``   → None
        ``T``                           → None    (generic parameter)
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind in (
            "void_type", "integral_type", "floating_point_type",
            "boolean_type",
        ):
            return None
        if kind == "scoped_type_identifier":
            # ``com.x.y.Z`` / ``Foo.Bar`` — take the rightmost type_identifier
            inner_ids = [c for c in node.children if c.type == "type_identifier"]
            if not inner_ids:
                return None
            text = _node_text(inner_ids[-1], src)
            break
        if kind == "generic_type":
            # ``Foo<T>`` — descend to the bare name; generic args are
            # captured separately by their own type_arguments inner captures.
            inner = next(
                (c for c in node.named_children
                 if c.type in ("type_identifier", "scoped_type_identifier")),
                None,
            )
            node = inner
            continue
        if kind == "array_type":
            # ``Foo[]`` — element child has no field name; take first named.
            inner = next(iter(node.named_children), None)
            node = inner
            continue
        if kind == "annotated_type":
            # ``@NonNull Foo`` — last named child is the type.
            node = next(
                (c for c in reversed(node.named_children)
                 if c.type not in ("annotation", "marker_annotation")),
                None,
            )
            continue
        # Anything else (wildcard, type_parameter, ...) — descend.
        node = next(iter(node.named_children), None)
    else:
        return None

    return text if is_resolvable_type_name(text, "java") else None


# ---------------------------------------------------------------------------
# Kotlin type-reference head extraction
# ---------------------------------------------------------------------------

def _kotlin_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Kotlin type expression, or None.

    Examples:
        ``Bar``                         → "Bar"
        ``Foo?``                        → "Foo"   (nullable_type unwrapped)
        ``List<Foo>``                   → "List"  → filtered (builtin)
        ``com.x.Foo``                   → "Foo"   (dotted user_type)
        ``Foo.Bar``                     → "Bar"
        ``() -> Foo``                   → None    (function_type — skipped)
        ``Unit`` / ``Any`` / ``String`` → None    (builtin)
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "user_type":
            # ``Foo`` / ``Foo<...>`` / ``ns.Foo``.
            # Children: identifier, type_arguments, possibly more dotted parts.
            # Rightmost identifier is the head; type_arguments contains the
            # generic args (captured separately by their own type-ref).
            inner_ids = [c for c in node.children if c.type == "identifier"]
            if not inner_ids:
                return None
            text = _node_text(inner_ids[-1], src)
            break
        if kind == "identifier":
            text = _node_text(node, src)
            break
        if kind == "nullable_type":
            # Unwrap ``Foo?`` to the underlying user_type.
            inner = next(iter(node.named_children), None)
            node = inner
            continue
        if kind in ("function_type", "parenthesized_type"):
            # () -> Foo, (() -> Foo) — leaf type names are not captured.
            return None
        if kind == "type_reference":
            node = next(iter(node.named_children), None)
            continue
        if kind == "type_projection":
            # `<out Foo>` / `<in Foo>` / `<Foo>` — last named child is the type.
            node = next(iter(node.named_children), None)
            continue
        # Anything else — descend.
        node = next(iter(node.named_children), None)
    else:
        return None

    return text if is_resolvable_type_name(text, "kotlin") else None


# ---------------------------------------------------------------------------
# Rust type-reference head extraction
# ---------------------------------------------------------------------------

def _rust_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Rust type expression, or None.

    Rust needs its own extractor because the C#-shaped default spells a type
    name ``identifier`` while tree-sitter-rust spells it ``type_identifier``.
    The default therefore returned None for every bare Rust type, and for
    ``std::io::Error`` returned the leftmost segment ``io`` — a crate name,
    not the type.

    Examples:
        ``MyType``              -> "MyType"
        ``&Other`` / ``&mut T`` -> "Other"  (reference_type unwrapped)
        ``dyn MyTrait``         -> "MyTrait"
        ``impl Shape``          -> "Shape"
        ``Box<Inner>``          -> "Box"    -> filtered (builtin)
        ``std::io::Error``      -> "Error"  (rightmost component)
        ``u32`` / ``String``    -> None     (builtin)
        ``T``                   -> None     (single-letter generic param)

    Generic arguments are not recursed into: ``Vec<Foo>`` yields the head
    ``Vec`` only. Foo reaches the resolver through its own capture, matching
    the Go and Kotlin extractors.
    """
    node: Node | None = type_node
    text = ""
    for _ in range(8):
        if node is None:
            return None
        kind = node.type
        if kind == "type_identifier":
            text = _node_text(node, src)
            break
        if kind == "scoped_type_identifier":
            # ``std::io::Error`` — the type is the rightmost component.
            name = node.child_by_field_name("name")
            if name is None:
                return None
            node = name
            continue
        if kind in ("reference_type", "pointer_type"):
            # ``&T`` / ``&mut T`` / ``*const T`` — the type field is the
            # referent; ``mut`` and ``const`` are unnamed children.
            node = node.child_by_field_name("type")
            continue
        if kind == "generic_type":
            # ``Box<Inner>`` — head is the constructor, args are captured
            # separately.
            node = node.child_by_field_name("type")
            continue
        if kind in ("dynamic_type", "abstract_type"):
            # ``dyn Trait`` / ``impl Trait`` — sole named child is the trait.
            node = node.child_by_field_name("trait") or next(
                iter(node.named_children), None
            )
            continue
        if kind in ("primitive_type", "tuple_type", "unit_type", "array_type",
                    "function_type", "never_type", "empty_type"):
            # Builtin, or no single head name to resolve.
            return None
        # Unknown shape - descend into the first named child and re-classify.
        node = next(iter(node.named_children), None)

    return text if is_resolvable_type_name(text, "rust") else None


def _pascal_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head identifier of a Pascal ``typeref`` type expression.

    Examples:
        ``TFoo``                  → "TFoo"
        ``TList<TBar>``           → "TList" (generic head only; the arg
                                     isn't captured separately -- no
                                     ``typerefArgs``-scoped query pattern
                                     exists yet)
        ``Ns.TQualified``         → "TQualified" (rightmost segment;
                                     Pascal's unit-qualification puts the
                                     type name last, same convention as
                                     C#'s ``qualified_name`` handling)
        ``Integer`` / ``TObject`` → None (builtin / VCL root type, never
                                     has an in-repo declaration)

    ``type_node`` is the ``typeref`` node pascal.scm captures directly (a
    ``declProc`` return type) or via the intermediate ``type`` wrapper
    (field/parameter/local-variable positions) -- the wrapper itself is
    never captured, only the ``typeref`` inside it, so this always starts
    from ``typeref`` regardless of which position it came from. A
    ``typeref`` wraps exactly one of: a bare ``identifier``, a generic
    ``typerefTpl`` (``entity`` field holds the head), or a qualified
    ``typerefDot`` (``rhs`` field holds the type name -- ``lhs`` is the
    unit qualifier).
    """
    head = type_node
    if head.type == "typeref":
        head = next(iter(head.named_children), None)
        if head is None:
            return None
    if head.type == "typerefTpl":
        entity = head.child_by_field_name("entity")
        if entity is None:
            return None
        head = entity
    elif head.type == "typerefDot":
        rhs = head.child_by_field_name("rhs")
        if rhs is None:
            return None
        head = rhs

    if head.type != "identifier":
        return None
    text = node_text(head, src).strip()
    if not text or text in get_builtin_types("pascal"):
        return None
    return text


# ---------------------------------------------------------------------------
# F# binding, parent and type-head helpers
# ---------------------------------------------------------------------------

# The two nodes fsharp.scm captures as @symbol.def for a `let` binding. One
# `function_or_value_defn` holds every clause of a `let rec f ... and g ...`
# group, so a clause is delimited by the next one of these, not by the node.
_FSHARP_BINDING_LEFT = ("function_declaration_left", "value_declaration_left")

# A binding whose ancestors include one of these sits inside another
# binding's body, so it is a local, not a member of the module or type.
_FSHARP_BODY_OWNERS = (
    "function_or_value_defn",
    "method_or_prop_defn",
    "fun_expression",
    # A top-level `do` block is executed code, not a declaration site.
    "do",
    "do_expression",
)

# Type shapes whose `type_name` child names the enclosing type of a member.
_FSHARP_TYPE_OWNERS = (
    "anon_type_defn",
    "record_type_defn",
    "union_type_defn",
    "enum_type_defn",
    "delegate_type_defn",
    "type_abbrev_defn",
    "type_extension",
)


def _fsharp_binding_is_nested(left_node: Node) -> bool:
    """True when a ``let`` binding is local to another binding's body.

    A nested ``let`` has the same node shape as a top-level one -- the
    grammar distinguishes them only by what encloses them -- so the generic
    callable-ancestor filter cannot see it: that filter looks for an
    ancestor whose *own* type is a captured callable, and the captured node
    here is the binding's left-hand side, never an ancestor of anything.
    A ``let`` directly inside a type body is deliberately not nested: it is
    that type's field.
    """
    ancestor = left_node.parent  # the binding's own function_or_value_defn
    ancestor = ancestor.parent if ancestor is not None else None
    while ancestor is not None:
        if ancestor.type in _FSHARP_BODY_OWNERS:
            return True
        ancestor = ancestor.parent
    return False


def _fsharp_binding_end_line(left_node: Node) -> int:
    """Last line of the clause introduced by *left_node*, 1-indexed.

    Walks forward to the next clause's left-hand side rather than taking the
    left node's next sibling, because a return-type annotation
    (``let f x : int = ...``) sits between the left node and the body.
    """
    parent = left_node.parent
    if parent is None:
        return left_node.end_point[0] + 1
    end = left_node.end_point[0] + 1
    seen = False
    for child in parent.named_children:
        if child.id == left_node.id:
            seen = True
            continue
        if not seen:
            continue
        if child.type in _FSHARP_BINDING_LEFT or child.type == "xml_doc":
            # A doc comment inside the defn belongs to the clause after it.
            break
        end = max(end, child.end_point[0] + 1)
    return end


def _fsharp_binding_has_params(left_node: Node) -> bool:
    """True when a value_declaration_left actually binds a function.

    let total (xs: int list) : int = ... has parameters, but the return
    type makes the grammar read the whole head as one value pattern: the
    name and every parameter pattern become siblings inside a single
    identifier_pattern. A plain let x: int = 5 leaves that node with
    the name alone, which is what separates the two.
    """
    holder = next(
        (c for c in left_node.named_children if c.type == "identifier_pattern"), None
    )
    return holder is not None and holder.named_child_count > 1


def _fsharp_parent_is_type(def_node: Node) -> bool:
    """True when the innermost owner enclosing an F# symbol is a type rather
    than a nested module.

    A ``let`` inside ``module Inner = ...`` is still a plain function, not a
    method -- only a type body turns a binding into a member. The generic
    function-to-method promotion in parser.py has no notion of "module", so
    it needs this to tell the two owners apart before promoting.
    """
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in _FSHARP_TYPE_OWNERS:
            return True
        if ancestor.type == "module_defn":
            return False
        ancestor = ancestor.parent
    return False


def _fsharp_owner_name(owner: Node, src: str) -> str | None:
    """The declared name of one F# type or nested-module node."""
    if owner.type == "module_defn":
        ident = next((c for c in owner.named_children if c.type == "identifier"), None)
        return node_text(ident, src).strip() if ident is not None else None
    name_node = owner.child_by_field_name("type_name")
    if name_node is None:
        holder = next((c for c in owner.named_children if c.type == "type_name"), None)
        name_node = holder.child_by_field_name("type_name") if holder else None
    if name_node is None:
        return None
    if name_node.type == "long_identifier":
        idents = [c for c in name_node.named_children if c.type == "identifier"]
        name_node = idents[-1] if idents else name_node
    return node_text(name_node, src).strip() or None


def _fsharp_parent_name(def_node: Node, src: str) -> str | None:
    """Return the dotted owner path of an F# symbol.

    No F# type node carries a ``name`` field -- the name lives on a
    ``type_name`` child, tagged with a ``type_name`` field of its own -- so
    the generic nesting walk in ``_find_parent`` finds nothing to read.

    Every enclosing nested module joins the path, not just the nearest
    owner: one file may hold ``module A`` and ``module B`` each declaring a
    ``type Dup`` with a ``Go`` member, and a symbol id is path plus parent
    plus name, so a nearest-owner parent would give both members one id.
    A file-level ``module``/``namespace`` header is skipped, since it is the
    whole file and distinguishes nothing within it.
    """
    parts: list[str] = []
    ancestor = def_node.parent
    while ancestor is not None:
        if ancestor.type in _FSHARP_TYPE_OWNERS or ancestor.type == "module_defn":
            name = _fsharp_owner_name(ancestor, src)
            if name:
                parts.append(name)
        ancestor = ancestor.parent
    return ".".join(reversed(parts)) or None


def _fsharp_head_type_identifier(type_node: Node, src: str) -> str | None:
    """The resolvable head of an F# type annotation.

    ``fsharp_type_name`` does the walk; this adds the filter the type-
    reference pass needs, since a primitive or an FSharp.Core type
    constructor never has an in-repo declaration.
    """
    text = fsharp_type_name(type_node, src)
    if not text or text in get_builtin_types("fsharp"):
        return None
    return text


# Per-language head-identifier extractor for ``@param.type`` captures.
# Defaults to the C#-shaped extractor; languages with a differently-shaped
# type grammar register their own here.
def _elixir_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the module name in an Elixir type position.

    A captured type is always a whole ``alias`` leaf (``%Foo.Bar{}``,
    ``@behaviour GenServer``), and the grammar keeps a dotted module name in
    one node, so the head is the node's own text. No unwrapping to do, and no
    generic-argument shape to recurse into -- unlike every other language
    here, which is why the shared C#-shaped extractor cannot serve.
    """
    if type_node.type != "alias":
        return None
    return node_text(type_node, src).strip() or None


def _objc_head_type_identifier(type_node: Node, src: str) -> str | None:
    """Return the head class name of an Objective-C type position, or None.

    Examples:
        ``(NSString *)``      -> None       (Foundation, never in-repo)
        ``(AFHTTPClient *)``  -> the name
        ``(instancetype)``    -> None       (builtin)
        ``(void)`` / ``(id)`` -> None       (primitive / builtin)
        ``@class Helper;``    -> "Helper"

    The capture is a ``type_name`` wrapper for a method return or parameter
    type, a bare ``type_identifier`` for a property, and a plain
    ``identifier`` for a ``@class`` forward declaration or a protocol name, so
    all four shapes start here. A pointer star lives in an
    ``abstract_pointer_declarator`` inside the ``type_name``, which is skipped
    rather than unwrapped: the star is not part of the name.
    """
    node: Node | None = type_node
    for _ in range(6):
        if node is None:
            return None
        kind = node.type
        if kind in ("type_identifier", "identifier"):
            text = _node_text(node, src).strip()
            return text if is_resolvable_type_name(text, "objectivec") else None
        if kind in ("primitive_type", "sized_type_specifier", "typedefed_specifier"):
            return None
        if kind in ("struct_specifier", "union_specifier", "enum_specifier"):
            name = node.child_by_field_name("name")
            if name is None:
                return None  # anonymous aggregate -- no named type to resolve
            text = _node_text(name, src).strip()
            return text if is_resolvable_type_name(text, "objectivec") else None
        node = node.named_children[0] if node.named_children else None
    return None


TYPE_HEAD_EXTRACTORS: dict[str, Callable[[Node, str], str | None]] = {
    "go": _go_head_type_identifier,
    "c": _c_head_type_identifier,
    "cpp": _c_head_type_identifier,
    "typescript": _ts_head_type_identifier,
    "javascript": _ts_head_type_identifier,
    "java": _java_head_type_identifier,
    "kotlin": _kotlin_head_type_identifier,
    "rust": _rust_head_type_identifier,
    "pascal": _pascal_head_type_identifier,
    "elixir": _elixir_head_type_identifier,
    "fsharp": _fsharp_head_type_identifier,
    "objectivec": _objc_head_type_identifier,
}


# ---------------------------------------------------------------------------
# Call extraction helpers
# ---------------------------------------------------------------------------


def _count_arguments(arg_node: Node) -> int:
    """Count the number of arguments in an argument/argument_list node.

    Comments are children of the argument list, so an argument annotated with
    a trailing ``// name`` counted twice. Grammars spell the node type several
    ways (``comment``, ``line_comment``, ``block_comment``), hence the
    substring test rather than a fixed set.
    """
    skip_types = frozenset({"(", ")", ",", "[", "]"})
    return sum(
        1
        for child in arg_node.children
        if child.type not in skip_types and "comment" not in child.type
    )


def _find_enclosing_symbol(
    line: int,
    symbol_ranges: list[tuple[int, int, str]],
) -> str | None:
    """Find the innermost symbol whose line range contains *line*."""
    best_id: str | None = None
    best_span = float("inf")

    for start, end, sym_id in symbol_ranges:
        if start > line:
            break
        if start <= line <= end:
            span = end - start
            if span < best_span:
                best_span = span
                best_id = sym_id

    return best_id
