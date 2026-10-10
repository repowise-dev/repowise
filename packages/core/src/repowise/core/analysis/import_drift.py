"""Whether a file's graph edges can differ between two versions of its text.

An index built at an older commit still describes every file whose edges did
not move since. In Python and JavaScript / TypeScript a file reaches another
only through an import the parser reads (``import``, ``from``, ``require``,
``import()``, a re-export) or through a string a loader resolves (a dotted
module name in a plugin table, a mocked or spawned path). Two versions with the
same imports and the same module- or path-shaped string literals therefore
have the same edges, whatever else changed in them.

Cheapest check first: the strings are a regex away, and when no line that
differs can be part of an import statement (one naming an import keyword, or
inside a bracketed or continued statement that does) the imports cannot
differ, so nothing is parsed. Otherwise both versions go through the parser's
import pass alone. Any other language is assumed to have moved whenever its
text changed beyond whitespace: Go, Java, C# and others reach same-package
code with no import at all. Data and prose import nothing.

Ceilings: Python reaching a submodule as an attribute of a package some other
module imported (``import a`` then ``a.b.f()``) moves no import, and a data
file a framework reads as wiring (a class named in YAML) is not compared.
Storing each file's fingerprint at index time would save the old-side work.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime
from pathlib import PurePosixPath

from ..ingestion.models import EXTENSION_TO_LANGUAGE, FileInfo

# Data and prose: they import nothing, so they cannot carry a route.
_NO_EDGES = frozenset({"json", "yaml", "toml", "markdown", "asciidoc"})

# A quoted string with no spaces holding a dot or a slash: a dotted module
# name (what the Python dynamic-import hints resolve) or a file path.
_NAME_LITERAL = re.compile(rb"""(["'`])([A-Za-z_@.$][\w.\-/@$]*)\1""")

# Words that open or continue an import statement, per import-bound language.
_JS_WORD = re.compile(rb"\b(?:import|export|require|from)\b")
_IMPORT_WORD = {
    "python": re.compile(rb"\b(?:import|from|__import__)\b"),
    "typescript": _JS_WORD,
    "javascript": _JS_WORD,
}
_JS_COMMENT = re.compile(rb"//.*")
_LINE_COMMENT = {"python": re.compile(rb"#.*"), "typescript": _JS_COMMENT}
_LINE_COMMENT["javascript"] = _JS_COMMENT
_STRING = re.compile(rb"""(["'`])(?:\\.|(?!\1).)*\1""")
_WHITESPACE = re.compile(rb"\s+")


def _language(path: str) -> str | None:
    return EXTENSION_TO_LANGUAGE.get(PurePosixPath(path).suffix.lower())


def may_carry_edges(path: str) -> bool:
    """Whether *path* is code the graph can hold edges from."""
    language = _language(path)
    return language is not None and language not in _NO_EDGES


def edges_may_differ(path: str, before: bytes | None, after: bytes | None) -> bool:
    """Whether *path*'s edges can differ between *before* and *after*.

    ``None`` on a side means the file is absent there. A file added since has
    no edges in the older index for a test to reach, and a deleted one only
    loses edges, so neither moves a route; a file that cannot be parsed has.
    """
    if before is None or after is None or before == after:
        return False
    language = _language(path)
    if language not in _IMPORT_WORD:
        return _WHITESPACE.sub(b" ", before).strip() != _WHITESPACE.sub(b" ", after).strip()
    if _names(before) != _names(after):
        return True
    if not _touches_imports(language, before, after):
        return False
    old = _imports(path, language, before)
    return old is None or old != _imports(path, language, after)


def _names(source: bytes) -> list[bytes]:
    """The module- or path-shaped string literals of one version."""
    found = {m.group(2) for m in _NAME_LITERAL.finditer(source)}
    return sorted(n for n in found if b"." in n or b"/" in n)


def _touches_imports(language: str, before: bytes, after: bytes) -> bool:
    """Whether a line one version has more of than the other can be in an import.

    Lines are compared as a multiset, so a moved line does not count.
    """
    old, new = before.splitlines(), after.splitlines()
    old_count, new_count = Counter(old), Counter(new)
    changed = {line for line in old_count | new_count if old_count[line] != new_count[line]}
    return _import_line_changed(language, old, changed) or _import_line_changed(
        language, new, changed
    )


def _import_line_changed(language: str, lines: list[bytes], changed: set[bytes]) -> bool:
    word, comment = _IMPORT_WORD[language], _LINE_COMMENT[language]
    depth, open_statement = 0, False
    for line in lines:
        code = comment.sub(b"", _STRING.sub(b'""', line))
        starts = word.search(code) is not None
        if line in changed and (starts or open_statement):
            return True
        if not (starts or open_statement):
            continue
        # Brackets outside strings and comments. A statement left open by a
        # miscount only parses more, never less.
        depth += sum(code.count(c) for c in b"([{") - sum(code.count(c) for c in b")]}")
        open_statement = depth > 0 or code.rstrip().endswith(b"\\")
        if not open_statement:
            depth = 0
    return False


def _imports(path: str, language: str, source: bytes) -> list[tuple] | None:
    """The imports the parser reads in one version, or ``None`` if it will not parse."""
    from ..ingestion.parser import ASTParser

    try:
        imports = ASTParser().parse_imports(_file_info(path, language, source), source)
    except Exception:
        return None
    return sorted((i.module_path, tuple(sorted(i.imported_names)), i.is_reexport) for i in imports)


def _file_info(path: str, language: str, source: bytes) -> FileInfo:
    # The parser reads only the path, language and bytes it is handed.
    return FileInfo(
        path=path,
        abs_path=path,
        language=language,  # type: ignore[arg-type]
        size_bytes=len(source),
        git_hash="",
        last_modified=datetime.now(UTC),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )
