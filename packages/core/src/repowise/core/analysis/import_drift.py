"""Whether a file's graph edges can differ between two versions of its text.

An index built at an older commit still describes every file whose edges did
not move since. In Python and JavaScript / TypeScript a file reaches another
only through an import the parser reads (``import``, ``from``, ``require``,
``import()``, a re-export) or through a string a loader resolves (a dotted
module name in a plugin table, a mocked or spawned path). Two versions with the
same imports and the same strings every string-reading edge producer reads
(each producer's own extractor, so a shape one resolves is never missed)
therefore have the same edges, whatever else changed in them.

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

For a file whose edges may have moved, :func:`rewiring` says what its new
version may reach that the indexed one did not: the files the imports it
gained resolve to, through the indexer's own resolvers, and the words of the
strings it gained. Selection keeps the file's tests only when one of those
leads to the change.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from ..ingestion.dynamic_hints import django as django_hints
from ..ingestion.dynamic_hints.python_imports import string_inputs
from ..ingestion.dynamic_hints.python_package_listing import listing_inputs
from ..ingestion.framework_edges.test_path_strings import path_literals
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
    if _names(path, language, before) != _names(path, language, after):
        return True
    if not _touches_imports(language, before, after):
        return False
    old = _imports(path, language, before)
    return old is None or old != _imports(path, language, after)


def _names(path: str, language: str, source: bytes) -> tuple:
    """The strings of one version that a loader or an edge producer may resolve."""
    found = {m.group(2) for m in _NAME_LITERAL.finditer(source)}
    names = sorted(n for n in found if b"." in n or b"/" in n)
    paths = sorted(set(path_literals(source)))
    if language != "python":
        return names, paths
    return names, paths, string_inputs(path, source), django_hints.edge_strings(path, source)


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
    imports = _parsed_imports(path, language, source)
    return None if imports is None else sorted(_import_key(i) for i in imports)


def _parsed_imports(path: str, language: str, source: bytes) -> list[Any] | None:
    from ..ingestion.parser import ASTParser

    try:
        return ASTParser().parse_imports(_file_info(path, language, source), source)
    except Exception:
        return None


def _import_key(imp: Any) -> tuple:
    return imp.module_path, tuple(sorted(imp.imported_names)), imp.is_reexport


@dataclass(frozen=True)
class Rewiring:
    """What a file's new version may reach that its indexed version did not.

    *imports* are the repository files the imports it gained resolve to;
    *names* the words of the strings it gained, any of which may name a module
    or file; *dirs* those of the strings that may name a directory (a path, a
    templated load, a Django app or URLconf).
    """

    imports: frozenset[str] = frozenset()
    names: frozenset[str] = frozenset()
    dirs: frozenset[str] = frozenset()


#: Resolves one parsed import of a file, ``(import, path, language)``, to its targets.
Resolve = Callable[[Any, str, str], Iterable[str]]

# What separates the words of a module name, a path or a templated load.
_WORD_SPLIT = re.compile(r"""[./\\:{}()\[\]'"`\s,=]+""")


def rewiring(
    path: str, before: bytes | None, after: bytes | None, resolve: Resolve, *, whole: bool = False
) -> Rewiring | None:
    """What *after* may reach that *before* did not, or ``None`` when that cannot be told.

    It cannot outside Python and JavaScript / TypeScript (other languages reach
    code without naming it), when *after* does not parse, when an import will
    not resolve, or when *after* gained a dynamic-load marker or a
    package-listing loop, which make strings already there resolve. Imports are
    compared whole: one that gained a name resolves again in full. With
    *whole* every import and string counts as gained: files added since the
    index can make one that did not change resolve. Ceiling: a string's words
    are matched to file names, so a string reaching a file through an alias
    the words do not spell is not seen.
    """
    language = _language(path)
    if before is None or after is None or language not in _IMPORT_WORD:
        return None
    try:
        added = _added_strings(path, language, b"" if whole else before, after)
        new = _parsed_imports(path, language, after)
        if added is None or new is None:
            return None
        old = () if whole else _parsed_imports(path, language, before) or ()
        seen = {_import_key(i) for i in old}
        targets: set[str] = set()
        for imp in new:
            if _import_key(imp) not in seen:
                targets.update(resolve(imp, path, language))
    except Exception:
        return None
    names: set[str] = set()
    dirs: set[str] = set()
    for text, may_name_dir in added:
        words = {w for w in _WORD_SPLIT.split(text) if w}
        names |= words
        if may_name_dir or "/" in text or "{" in text:
            dirs |= words
    return Rewiring(
        imports=frozenset(t for t in targets if not t.startswith("external:")),
        names=frozenset(names),
        dirs=frozenset(dirs),
    )


def _added_strings(
    path: str, language: str, before: bytes, after: bytes
) -> set[tuple[str, bool]] | None:
    """The strings *after* holds that *before* did not, each with whether it may
    name a directory; ``None`` when strings already there may start resolving."""

    def literals(source: bytes) -> set[bytes]:
        found = {m.group(2) for m in _NAME_LITERAL.finditer(source)}
        return {n for n in found if b"." in n or b"/" in n} | set(path_literals(source))

    added: set[tuple[bytes | str, bool]] = {(s, False) for s in literals(after) - literals(before)}
    if language != "python":
        return None if _gained(_JS_DYNAMIC_LOADS, before, after) else _decoded(added)
    old_gates, old_found, old_live, _ = string_inputs(path, before)
    gates, found, live, _ = string_inputs(path, after)
    if set(gates) - set(old_gates) or set(listing_inputs(after)) - set(listing_inputs(before)):
        return None
    added |= {(s, False) for s, _ in set(found) - set(old_found)}
    added |= {(s, False) for s in set(live) - set(old_live)}
    django = django_hints.edge_strings(path, after)
    if django is None:
        return None
    old_django = _leaves(django_hints.edge_strings(path, before) or ())
    added |= {(s, True) for s in _leaves(django) - old_django}
    return _decoded(added)


# Loads whose target a literal does not spell: a glob, a directory listing,
# a webpack context, a templated dynamic import.
_JS_DYNAMIC_LOADS = (b"import.meta.glob", b"require.context", b"readdirSync", b"readdir(", b"import(`")


def _gained(markers: tuple[bytes, ...], before: bytes, after: bytes) -> bool:
    return any(m in after and m not in before for m in markers)


def _decoded(added: set[tuple[bytes | str, bool]]) -> set[tuple[str, bool]]:
    return {
        (s.decode("utf-8", "replace") if isinstance(s, bytes) else s, may_name_dir)
        for s, may_name_dir in added
    }


def _leaves(value: Any) -> set[str]:
    """Every string in a nest of lists and tuples."""
    if isinstance(value, str):
        return {value}
    if isinstance(value, (list, tuple)):
        return {leaf for item in value for leaf in _leaves(item)}
    return set()


class _Externals:
    """Where the resolvers register the ``external:`` nodes they create. Only
    their answers are kept, and a networkx graph would cost its import."""

    def __init__(self) -> None:
        self.nodes: set[str] = set()

    def add_node(self, key: str, **_: Any) -> None:
        self.nodes.add(key)


def import_resolver(repo_path: str | Path, files: Collection[str]) -> Resolve:
    """Resolve an import the way the indexer does, against *files* (the paths the
    change's base holds). TypeScript path aliases are read on first use."""
    from ..ingestion.graph._import_targets import resolve_import_targets
    from ..ingestion.graph._stem import build_stem_map
    from ..ingestion.resolvers import ResolverContext
    from ..ingestion.resolvers.typescript import detect_platform_files

    path_set = set(files)
    ctx = ResolverContext(
        path_set=path_set,
        stem_map=build_stem_map(path_set),
        graph=_Externals(),  # type: ignore[arg-type]
        repo_path=Path(repo_path),
        has_sfc_files=any(p.endswith((".vue", ".svelte", ".astro")) for p in path_set),
        has_platform_files=detect_platform_files(path_set),
    )
    aliases_read = False

    def resolve(imp: Any, path: str, language: str) -> tuple[str, ...]:
        nonlocal aliases_read
        if language in ("typescript", "javascript") and not aliases_read:
            from ..ingestion.tsconfig_resolver import TsconfigResolver

            aliases_read = True
            ctx.tsconfig_resolver = TsconfigResolver(repo_path=Path(repo_path), path_set=path_set)
        return resolve_import_targets(imp, path, language, ctx)

    return resolve


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
