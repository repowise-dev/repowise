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
) -> tuple[dict[str, list[Path]], dict[str, list[Path]], dict[str, list[Path]]]:
    """Return ``(namespace_map, type_map, partial_map)`` for the .cs files.

    * ``namespace_map[ns]`` → files declaring that namespace.
    * ``type_map[type_name]`` → files declaring that type. Keyed by both
      the unqualified name and (for one-level nested types) the
      ``Outer.Inner`` qualified form. Multiple files per name is
      expected (partial types, same-named types in different
      namespaces) — callers disambiguate by project enclosure.
    * ``partial_map[fqn]`` → files carrying a ``partial`` declaration of
      that fully-qualified type. Fragments of one class across several
      files are literally one type — the graph links them.

    When *texts* is provided, file contents are read from the dict
    rather than the filesystem — this is the hot path used by
    ``DotNetProjectIndex.build_index`` to share one read with the
    global-usings collector. Files missing from *texts* (or that fail
    to read when ``texts`` is None) are skipped silently.
    """
    namespaces: dict[str, list[Path]] = {}
    types: dict[str, list[Path]] = {}
    partials: dict[str, list[Path]] = {}
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
        seen_ns: set[str] = set()
        for ns in declared_namespaces(text):
            if ns in seen_ns:
                continue
            seen_ns.add(ns)
            namespaces.setdefault(ns, []).append(path)
        seen_t: set[str] = set()
        seen_p: set[str] = set()
        for decl in scan_type_declarations(text):
            for key in {decl.name, decl.qualified}:
                if key in seen_t:
                    continue
                seen_t.add(key)
                types.setdefault(key, []).append(path)
            if decl.is_partial and decl.fqn not in seen_p:
                seen_p.add(decl.fqn)
                partials.setdefault(decl.fqn, []).append(path)
    return namespaces, types, partials
