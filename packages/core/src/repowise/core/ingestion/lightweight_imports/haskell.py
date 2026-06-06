"""Regex import extraction for Haskell.

Captured forms:

    import Foo.Bar
    import qualified Foo.Bar as FB
    import Foo.Bar (thing, Other)
    import safe qualified Foo.Bar
    import "pkg" Foo.Bar                     — PackageImports

The import list / hiding clause is not parsed — the edge target is the
module, which is all the file graph needs.
"""

from __future__ import annotations

import re

from ..models import Import

_IMPORT_RE = re.compile(
    r"^import[ \t]+"
    r"(?:safe[ \t]+)?"
    r"(?:qualified[ \t]+)?"
    r'(?:"[^"]+"[ \t]+)?'
    r"(?:qualified[ \t]+)?"  # GHC accepts qualified after the package literal too
    r"([A-Z][A-Za-z0-9_.']*)",
    re.M,
)


def extract_haskell_imports(text: str) -> list[Import]:
    imports: list[Import] = []
    seen: set[str] = set()
    for match in _IMPORT_RE.finditer(text):
        module = match.group(1)
        if module in seen:
            continue
        seen.add(module)
        imports.append(
            Import(
                raw_statement=match.group(0).strip(),
                module_path=module,
                imported_names=[],
                is_relative=False,
                resolved_file=None,
            )
        )
    return imports
