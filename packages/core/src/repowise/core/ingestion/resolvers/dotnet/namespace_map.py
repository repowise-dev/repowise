"""Build namespace → file and type-name → file mappings.

We use regexes rather than re-parsing the AST because the resolver runs
after parsing has finished and ``parsed_files`` does not preserve raw
namespace text in a uniform shape across grammar versions. The regexes
cover both block-form and file-scoped namespaces (C# 10+) and the
canonical type declaration forms.
"""

from __future__ import annotations

import re
from pathlib import Path

# `namespace Foo.Bar.Baz {` (block-form)
# `namespace Foo.Bar.Baz;`  (file-scoped, C# 10+)
_NAMESPACE_RE = re.compile(
    r"^\s*namespace\s+([A-Za-z_][\w.]*)\s*[;{]",
    re.MULTILINE,
)

# Captures `class Foo`, `interface IFoo`, `struct Foo`, `enum Foo`, `record Foo`.
# Permits leading modifier soup (`public partial sealed class`) and an
# optional generic-parameter list / inheritance clause after the name.
# The name is captured up to (but excluding) the first `<`, `:`, `{`,
# `(`, `;` or whitespace — covering generics, primary ctors, base
# clauses, and braces / file-scoped forms uniformly.
# The leading alternation accepts start-of-line OR semicolon as the
# preceding context so file-scoped namespaces like
# ``namespace Foo; class Bar {}`` (single-line, common in tests and
# small samples) match as well as the canonical line-per-decl form.
# A comment line like ``// class Foo {}`` is ruled out because the
# alternation does not include ``/`` and the modifier-soup group is
# anchored on whitespace, not arbitrary text.
_TYPE_DECL_RE = re.compile(
    r"(?:^|;)\s*((?:(?:public|private|internal|protected|static|sealed|abstract|partial|"
    r"readonly|ref|unsafe|new|file)\s+)*)"
    r"(?:class|interface|struct|enum|record(?:\s+(?:class|struct))?)\s+"
    r"([A-Za-z_]\w*)",
    re.MULTILINE,
)


def declared_namespaces(cs_text: str) -> list[str]:
    """Return every namespace declared in *cs_text*, in source order.

    A single .cs file may declare multiple namespaces (rare but legal).
    Duplicates are preserved so callers can count them if they care.
    """
    return [m.group(1) for m in _NAMESPACE_RE.finditer(cs_text)]


# ---------------------------------------------------------------------------
# VB.NET — same lookups, different syntax (block keywords, no braces).
# ---------------------------------------------------------------------------

# `Namespace Foo.Bar.Baz` — always block-form (`End Namespace`), no
# file-scoped/semicolon variant the way C# 10 has.
_VB_NAMESPACE_RE = re.compile(
    r"^[ \t]*Namespace\s+([A-Za-z_][\w.]*)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Captures `Class Foo`, `Interface IFoo`, `Structure Foo`, `Enum Foo`,
# `Module Foo`. Name is captured up to (but excluding) `(` (generic
# `(Of T)` clauses), matching _TYPE_DECL_RE's stop-before-generics behaviour.
_VB_TYPE_DECL_RE = re.compile(
    r"^[ \t]*((?:(?:Public|Private|Protected|Friend|MustInherit|NotInheritable|"
    r"Partial|Shadows|NotOverridable|Overridable)\s+)*)"
    r"(?:Class|Interface|Structure|Enum|Module)\s+"
    r"([A-Za-z_]\w*)",
    re.MULTILINE | re.IGNORECASE,
)

# VB has no braces, so nesting depth is tracked via the block-closing
# keyword instead — deliberately narrow (only the 5 type-closing forms,
# never `End Sub`/`End If`/`End Property`/etc, which don't affect type
# nesting depth).
_VB_TYPE_CLOSE_RE = re.compile(
    r"^[ \t]*End\s+(?:Class|Interface|Structure|Enum|Module)\b",
    re.MULTILINE | re.IGNORECASE,
)


def declared_vbnet_namespaces(vb_text: str) -> list[str]:
    """Return every namespace declared in *vb_text*, in source order."""
    return [m.group(1) for m in _VB_NAMESPACE_RE.finditer(vb_text)]


def scan_vbnet_type_declarations(vb_text: str) -> list[TypeDecl]:
    """Scan *vb_text* for type declarations with nesting + partial info.

    Mirrors :func:`scan_type_declarations`'s one-level nesting compromise,
    but VB has no braces to count — instead, every type-declaration match
    and every `End Class`/`Module`/`Structure`/`Interface`/`Enum` match is
    merged into one position-ordered event stream and walked with a simple
    stack: an open event pushes, a close event pops. The stack top (if any)
    when an open event fires is that declaration's immediate parent.
    """
    ns_positions = [(m.start(), m.group(1)) for m in _VB_NAMESPACE_RE.finditer(vb_text)]

    events: list[tuple[int, str, re.Match[str]]] = []
    for m in _VB_TYPE_DECL_RE.finditer(vb_text):
        events.append((m.start(), "open", m))
    for m in _VB_TYPE_CLOSE_RE.finditer(vb_text):
        events.append((m.start(), "close", m))
    events.sort(key=lambda e: e[0])

    decls: list[TypeDecl] = []
    stack: list[str] = []  # bare names of enclosing types, outermost first
    for _pos, kind, m in events:
        if kind == "close":
            if stack:
                stack.pop()
            continue

        name = m.group(2)
        parent = stack[-1] if stack else None
        qualified = f"{parent}.{name}" if parent else name
        namespace = ""
        for ns_start, ns in ns_positions:
            if ns_start < m.start():
                namespace = ns
            else:
                break
        is_partial = "partial" in (m.group(1) or "").lower().split()
        decls.append(TypeDecl(name, qualified, namespace, is_partial))
        stack.append(name)
    return decls


class TypeDecl:
    """One type declaration: bare + one-level-qualified name, partial flag."""

    __slots__ = ("is_partial", "name", "namespace", "qualified")

    def __init__(self, name: str, qualified: str, namespace: str, is_partial: bool):
        self.name = name
        self.qualified = qualified
        self.namespace = namespace
        self.is_partial = is_partial

    @property
    def fqn(self) -> str:
        return f"{self.namespace}.{self.qualified}" if self.namespace else self.qualified


def scan_type_declarations(cs_text: str) -> list[TypeDecl]:
    """Scan *cs_text* for type declarations with nesting + partial info.

    Nesting is tracked one level via raw brace depth at each match
    position (strings/comments are not lexed — same pragmatics as the
    rest of this module): a declaration whose depth exceeds the previous
    declaration's gets ``Outer.Inner`` as its qualified name. Deeper
    nesting collapses onto the immediate parent (recorded cut). The
    namespace is the nearest declaration preceding the match.
    """
    ns_positions = [(m.start(), m.group(1)) for m in _NAMESPACE_RE.finditer(cs_text)]

    decls: list[TypeDecl] = []
    stack: list[tuple[int, str]] = []  # (brace depth at decl, bare name)
    pos = 0
    depth = 0
    for m in _TYPE_DECL_RE.finditer(cs_text):
        depth += cs_text.count("{", pos, m.start()) - cs_text.count("}", pos, m.start())
        pos = m.start()

        while stack and stack[-1][0] >= depth:
            stack.pop()

        name = m.group(2)
        parent = stack[-1][1] if stack else None
        qualified = f"{parent}.{name}" if parent else name
        namespace = ""
        for ns_start, ns in ns_positions:
            if ns_start < m.start():
                namespace = ns
            else:
                break
        is_partial = "partial" in (m.group(1) or "").split()
        decls.append(TypeDecl(name, qualified, namespace, is_partial))
        stack.append((depth, name))
    return decls


def declared_type_names(cs_text: str) -> list[str]:
    """Return every type name declared in *cs_text* (bare, unqualified).

    Generic parameters and base clauses are stripped. ``partial`` types
    declared across multiple files yield one match per file — the caller
    builds a list-valued map so all defining files are surfaced.
    """
    return [m.group(2) for m in _TYPE_DECL_RE.finditer(cs_text)]


def build_namespace_map(
    cs_files: list[Path],
    *,
    texts: dict[Path, str] | None = None,
    vb_root_namespaces: dict[Path, str] | None = None,
) -> tuple[dict[str, list[Path]], dict[str, list[Path]], dict[str, list[Path]]]:
    """Return ``(namespace_map, type_map, partial_map)`` for the .cs/.vb files.

    * ``namespace_map[ns]`` → files declaring that namespace.
    * ``type_map[type_name]`` → files declaring that type. Keyed by both
      the unqualified name and (for one-level nested types) the
      ``Outer.Inner`` qualified form. Multiple files per name is
      expected (partial types, same-named types in different
      namespaces) — callers disambiguate by project enclosure. Never
      namespace-qualified, so VB's ``RootNamespace`` prepending (below)
      doesn't affect this map at all.
    * ``partial_map[fqn]`` → files carrying a ``partial`` declaration of
      that fully-qualified type. Fragments of one class across several
      files are literally one type — the graph links them.

    Dispatches per file by extension: ``.vb`` files use the VB.NET
    keyword-based scanners, everything else uses the (default, C#-shaped)
    brace-based scanners.

    *vb_root_namespaces*, keyed by resolved absolute ``.vb`` file path,
    supplies that file's project ``RootNamespace`` (VB.NET prepends it to
    *every* declaration, not just namespace-less files — see
    docs/architecture/vbnet-support.md D4). Only affects VB's namespace_map
    entries and partial-type FQN grouping — the type_map is unaffected
    (see above). C# files / files with no entry here are unaffected.

    When *texts* is provided, file contents are read from the dict
    rather than the filesystem — this is the hot path used by
    ``DotNetProjectIndex.build_index`` to share one read with the
    global-usings collector. Files missing from *texts* (or that fail
    to read when ``texts`` is None) are skipped silently.
    """
    namespaces: dict[str, list[Path]] = {}
    types: dict[str, list[Path]] = {}
    partials: dict[str, list[Path]] = {}
    vb_root_namespaces = vb_root_namespaces or {}
    for path in cs_files:
        if texts is not None:
            text = texts.get(path)
            if text is None:
                continue
        else:
            try:
                text = path.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue

        is_vb = path.suffix.lower() == ".vb"
        root_ns = vb_root_namespaces.get(path) if is_vb else None

        declared_fn = declared_vbnet_namespaces if is_vb else declared_namespaces
        scan_fn = scan_vbnet_type_declarations if is_vb else scan_type_declarations

        seen_ns: set[str] = set()
        file_namespaces = declared_fn(text)
        if root_ns:
            file_namespaces = (
                [f"{root_ns}.{ns}" for ns in file_namespaces] if file_namespaces else [root_ns]
            )
        for ns in file_namespaces:
            if ns in seen_ns:
                continue
            seen_ns.add(ns)
            namespaces.setdefault(ns, []).append(path)

        seen_t: set[str] = set()
        seen_p: set[str] = set()
        for decl in scan_fn(text):
            if root_ns:
                decl.namespace = f"{root_ns}.{decl.namespace}" if decl.namespace else root_ns
            for key in {decl.name, decl.qualified}:
                if key in seen_t:
                    continue
                seen_t.add(key)
                types.setdefault(key, []).append(path)
            if decl.is_partial and decl.fqn not in seen_p:
                seen_p.add(decl.fqn)
                partials.setdefault(decl.fqn, []).append(path)
    return namespaces, types, partials
