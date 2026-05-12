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
    r"(?:^|;)\s*(?:(?:public|private|internal|protected|static|sealed|abstract|partial|"
    r"readonly|ref|unsafe|new|file)\s+)*"
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


def declared_type_names(cs_text: str) -> list[str]:
    """Return every top-level type name declared in *cs_text*.

    Generic parameters and base clauses are stripped. ``partial`` types
    declared across multiple files yield one match per file — the caller
    builds a list-valued map so all defining files are surfaced.
    """
    return [m.group(1) for m in _TYPE_DECL_RE.finditer(cs_text)]


def build_namespace_map(
    cs_files: list[Path],
) -> tuple[dict[str, list[Path]], dict[str, list[Path]]]:
    """Return ``(namespace_map, type_map)`` for every .cs file given.

    * ``namespace_map[ns]`` → files declaring that namespace.
    * ``type_map[type_name]`` → files declaring that type (unqualified
      name). Multiple files per name is expected (partial types,
      same-named types in different namespaces) — callers disambiguate
      by project enclosure.

    Files that fail to read or declare nothing are skipped silently.
    """
    namespaces: dict[str, list[Path]] = {}
    types: dict[str, list[Path]] = {}
    for path in cs_files:
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
        for tn in declared_type_names(text):
            if tn in seen_t:
                continue
            seen_t.add(tn)
            types.setdefault(tn, []).append(path)
    return namespaces, types
