"""Build a namespace → file mapping by scanning .cs files for namespace declarations.

We use a regex rather than re-parsing the AST because the resolver runs
after parsing has finished and ``parsed_files`` does not preserve raw
namespace text in a uniform shape across grammar versions. The regex
covers both block-form and file-scoped namespaces (C# 10+).
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


def declared_namespaces(cs_text: str) -> list[str]:
    """Return every namespace declared in *cs_text*, in source order.

    A single .cs file may declare multiple namespaces (rare but legal).
    Duplicates are preserved so callers can count them if they care.
    """
    return [m.group(1) for m in _NAMESPACE_RE.finditer(cs_text)]


def build_namespace_map(cs_files: list[Path]) -> dict[str, list[Path]]:
    """Return {namespace: [files declaring it]} for every .cs file given.

    Files that fail to read or declare no namespace are skipped silently.
    """
    out: dict[str, list[Path]] = {}
    for path in cs_files:
        try:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        seen: set[str] = set()
        for ns in declared_namespaces(text):
            if ns in seen:
                continue
            seen.add(ns)
            out.setdefault(ns, []).append(path)
    return out
