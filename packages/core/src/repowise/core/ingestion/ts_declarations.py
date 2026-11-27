"""TypeScript declaration files: ``.d.ts``, ``.d.mts`` and ``.d.cts``.

The one owner of which file names are declaration files. A leaf with no
imports, so ingestion and analysis can both read it.
"""

from __future__ import annotations

#: A declaration file's endings: the plain one, and the ESM and CommonJS
#: variants TypeScript emits beside ``.mts`` and ``.cts`` sources.
DECLARATION_SUFFIXES: tuple[str, ...] = (".d.ts", ".d.mts", ".d.cts")


def is_declaration_file(path: str) -> bool:
    """Whether *path* names a TypeScript declaration file."""
    return path.lower().endswith(DECLARATION_SUFFIXES)


def declaration_stem(path: str) -> str | None:
    """*path* without its declaration ending (``lib/x.d.mts`` -> ``lib/x``), or ``None``."""
    lowered = path.lower()
    for suffix in DECLARATION_SUFFIXES:
        if lowered.endswith(suffix):
            return path[: -len(suffix)]
    return None
