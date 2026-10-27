"""Every site an agent edits to rename one symbol or update all its callers.

The graph says where to look (the definition, importers of its file, call
edges with their lines); the live files say what is there now. Each candidate
file is scanned for the symbol's bare name on a word boundary and every hit is
classified, so a stale graph line is dropped instead of served, and a hit the
graph never saw is still listed. ``complete`` is true only when nothing the
scan could not see may hold another site.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
from collections import Counter
from collections.abc import Iterable
from itertools import islice
from pathlib import Path, PurePosixPath
from typing import Any, NamedTuple

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.fs_walk import PRUNED_DIRS, iter_glob
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.symbol_identity import id_segment_name
from repowise.core.persistence.models import GraphEdge, GraphNode, WikiSymbol
from repowise.core.test_paths import is_test_related_path
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._helpers import read_repo_file_text
from repowise.server.mcp_server._meta import _working_tree_dirty_paths, uncommitted_targets
from repowise.server.mcp_server._verify import _MIN_GATE_NAME_LEN, name_at_line, relocate_symbol

MAX_SITES = 200
MAX_TEXT_CHARS = 160
# Plain mentions kept per file when a collector can hold the rest: a mock-heavy
# test can name a symbol hundreds of times and bury its real callers.
MAX_REFERENCES_PER_FILE = 3
# Ceiling on files read per call; past it the set is reported incomplete
# rather than read without bound.
MAX_FILES = 300
_MAX_FILE_BYTES = 2_000_000
_MIN_CALL_CONFIDENCE = 0.7
_MEMBER_KINDS = frozenset({"method", "constructor", "property", "field"})
# Languages where using a symbol from another file takes an import naming that
# file or the symbol, so importers are every file that can use it. Elsewhere
# (same-package or namespace visibility) sites are listed but never complete,
# except in the directory-package languages below.
_IMPORT_SCOPED_LANGUAGES = frozenset({"python", "typescript", "javascript"})
# Languages whose same-package files are found and scanned too, so the set
# can be complete: Go's package is its directory, a JVM package is every file
# declaring it (any of these languages can share it).
_DIRECTORY_PACKAGE_LANGUAGES = frozenset({"go", "java", "kotlin"})
_JVM = ("java", "kotlin", "scala", "groovy")
_PACKAGE_LINE = re.compile(r"^\s*package\s+([\w.]+)")
_JVM_NAME = re.compile(r'^\s*@file\s*:\s*JvmName\s*\(\s*"(\w+)"')
_PYTHON_EXTENSIONS = REGISTRY.extensions_for(("python",))
# A statement that brings a name into a file, in the languages the graph
# resolves imports for. Continuation lines are found by bracket balance.
_IMPORT_START = re.compile(
    r"^\s*(?:from\s+\S+\s+import\b|import\b|export\s.*\bfrom\b|export\s*\{|using\b|use\b"
    r"|#\s*include\b|(?:const|let|var)\s.*\brequire\s*\()"
)
# A statement that can hand a local name to other files: export lists, default
# exports, CommonJS export assignments and Python ``__all__``.
_EXPORT_START = re.compile(
    r"^\s*(?:export\s*(?:\{|\*|default\b)|(?:module\.)?exports\b|__all__\b)"
)
_CJS_EXPORT = re.compile(r"\bmodule\.exports\b|(?<![\w$.])exports\.")
_EXPORT_LINE = re.compile(r"^\s*export\b")
_CALL_AFTER = re.compile(r"\s*(?:\?\.)?\(")
# Uses of a name that cannot hand its binding on: a call, a member access,
# ``new name`` and a JSX tag.
_LOCAL_USE_AFTER = re.compile(r"\s*(?:\(|\?\.|\.(?!\.))")
_LOCAL_USE_BEFORE = re.compile(r"(?:\bnew\s+|</?)$")
# The call forms of the indexed languages, so a line that only mentions a
# callee (prose, a field, a type) is not served as its call. Brackets or type
# arguments after it (``f(``, ``f?.(``, ``f!(``, ``f[``, ``f {``, ``f<T>(``), a
# reference, decorator or Lisp form before it (``T::f``, ``@f``, ``(f``), or a
# bracketless command call at the head of an expression (``run "$x"``).
_CALL_FORM_AFTER = re.compile(r"\s*(?:\?\.|[?!])?\s*[(\[{]|(?:::)?<")
_CALL_FORM_BEFORE = re.compile(r"(?:::|[@(])$")
# Ceiling: a bracketless call on a receiver (Ruby ``obj.foo arg``) is not read
# as a call, so its call line is dropped; widen here if that gap shows up.
_COMMAND_HEAD = re.compile(
    r"(?:^|[=;&|,>!`]|\$\(|\b(?:if|then|do|else|elif|while|until|return|exec|perform))\s*$",
    re.IGNORECASE,
)
_COMMAND_ARGS = re.compile(r"\s+[^\s=:;,.)\]}|&<>+*/%-]|\s*[;.]?\s*$")


def _bare_name(node: GraphNode) -> str:
    return id_segment_name((node.name or node.node_id).split("::")[-1].split(".")[-1])


def _import_lines(lines: list[str], start: re.Pattern[str] = _IMPORT_START) -> set[int]:
    """1-based line numbers that belong to a statement opening with *start*."""
    out: set[int] = set()
    i = 0
    while i < len(lines):
        if not start.match(lines[i]):
            i += 1
            continue
        depth = 0
        while i < len(lines):
            text = lines[i]
            out.add(i + 1)
            depth += sum(text.count(c) for c in "({[") - sum(text.count(c) for c in ")}]")
            i += 1
            if depth <= 0 and not text.rstrip().endswith("\\"):
                break
    return out


def _read_lines(root: Path, rel: str) -> list[str] | None:
    try:
        if (root / rel).stat().st_size > _MAX_FILE_BYTES:
            return None
    except OSError:
        return None
    text = read_repo_file_text(root, rel)
    if text is None:
        return None
    # Only a newline ends a line: splitlines() also splits on form feeds and
    # other separators, which would shift every line number after them.
    return [line.removesuffix("\r") for line in text.removesuffix("\n").split("\n")]


def first_call_line(lines_json: str | None) -> int | None:
    """The earliest line in an edge's ``call_lines_json``, or None when it records none."""
    try:
        lines = [int(n) for n in json.loads(lines_json or "[]")]
    except (TypeError, ValueError):
        return None
    return min((n for n in lines if n > 0), default=None)


def _word(name: str) -> re.Pattern[str]:
    """*name* as a whole identifier."""
    return re.compile(rf"(?<![\w$]){re.escape(name)}(?![\w$])")


def _renames(
    lines: list[str], name: str, imports: set[int], python: bool
) -> tuple[set[str], set[str]]:
    """``(every name *name* is renamed to, those bound by an import)`` in this file."""
    esc = re.escape(name)
    word = _word(name)
    as_rename = re.compile(rf"(?<![\w$.]){esc}\s+as\s+([\w$]+)")
    # ``{ name: other } = require(...)`` or a destructured import; a ternary's
    # ``name : other`` is excluded, anything else colon-shaped counts.
    colon_rename = re.compile(rf"(?<![\w$.?]){esc}\s*:\s*([A-Za-z_$][\w$]*)")
    ternary = re.compile(rf"\?[^:]*(?<![\w$]){esc}\s*:")
    aliases: set[str] = set()
    # Aliases bound by an import; a rename anywhere else is always a hole.
    imported: set[str] = set()
    for n, text in enumerate(lines, 1):
        if not word.search(text):
            continue
        found = [m.group(1) for m in as_rename.finditer(text)] if n in imports else []
        if not python and not ternary.search(text):
            found += [m.group(1) for m in colon_rename.finditer(text)]
        aliases.update(found)
        if n in imports:
            imported.update(found)
    # ``name as default`` is a default export, not a rename.
    aliases.discard("default")
    return aliases, aliases & imported


def _calls(text: str, name: str) -> bool:
    """Whether *text* uses *name* in one of the call forms above."""
    return any(
        _CALL_FORM_AFTER.match(text, m.end())
        or _CALL_FORM_BEFORE.search(text, 0, m.start())
        or _LOCAL_USE_BEFORE.search(text, 0, m.start())
        or (_COMMAND_HEAD.search(text, 0, m.start()) and _COMMAND_ARGS.match(text, m.end()))
        for m in _word(name).finditer(text)
    )


def _call_texts(
    root: Path,
    wanted: dict[str, set[tuple[int, str]]],
    defs: dict[str, set[tuple[int, str]]],
) -> tuple[dict[tuple[str, int, str], str], set[tuple[str, int, str]]]:
    """``(file, line, name) -> text`` for each line that still calls *name* or its
    import alias, and the *defs* ``(file, line, name)`` whose live line no longer
    defines *name*. A file that cannot be read answers neither."""
    out: dict[tuple[str, int, str], str] = {}
    moved: set[tuple[str, int, str]] = set()
    for rel in wanted.keys() | defs.keys():
        lines = _read_lines(root, rel)
        if lines is None:
            continue
        moved.update(
            (rel, n, name) for n, name in defs.get(rel, ()) if not name_at_line(lines, name, n)
        )
        imports: set[int] | None = None
        for n, name in wanted.get(rel, ()):
            if not 0 < n <= len(lines):
                continue
            text = lines[n - 1]
            if not _calls(text, name):
                if imports is None:
                    imports = _import_lines(lines)
                python = Path(rel).suffix in _PYTHON_EXTENSIONS
                _, local = _renames(lines, name, imports, python)
                if not any(_calls(text, a) for a in local):
                    continue
            out[(rel, n, name)] = text.strip()[:MAX_TEXT_CHARS]
    return out, moved


def _leaf(symbol_id: str) -> str:
    return id_segment_name(symbol_id.split("::")[-1].split(".")[-1])


def _row_checks(
    rows: list[dict[str, Any]], callee_id: str | None
) -> tuple[dict[int, tuple[str, int, str]], dict[int, tuple[str, int, str]]]:
    """Per row index, ``(file, call_line, callee)`` and ``(file, line, caller)`` to check live."""
    asks: dict[int, tuple[str, int, str]] = {}
    defs: dict[int, tuple[str, int, str]] = {}
    for i, row in enumerate(rows):
        if not row.get("file"):
            continue
        callee = row.get("via_wrapper") or row.get("target") or callee_id
        if row.get("call_line") and callee:
            asks[i] = (row["file"], row["call_line"], _leaf(callee))
        caller = row.get("symbol_id") or row.get("caller") or ""
        # A name too short to tell apart on a line is left unchecked, as in get_symbol.
        name = _leaf(caller) if "::" in caller else ""
        if row.get("line") and len(name) >= _MIN_GATE_NAME_LEN:
            defs[i] = (row["file"], row["line"], name)
    return asks, defs


def _by_file(keys: Iterable[tuple[str, int, str]]) -> dict[str, set[tuple[int, str]]]:
    out: dict[str, set[tuple[int, str]]] = {}
    for rel, n, name in keys:
        out.setdefault(rel, set()).add((n, name))
    return out


async def attach_call_text(
    repo_root: str | Path | None, rows: list[dict[str, Any]], callee_id: str | None = None
) -> None:
    """Add ``text``, the live source at ``call_line``, where that line still calls the callee.

    The callee is the row's ``via_wrapper``, else its ``target``, else
    *callee_id*. A line that no longer calls it (the file moved under the
    index), or a file that cannot be read, loses ``call_line`` too, so no
    stale line is served. The caller's definition ``line`` is checked the
    same way: when the live line no longer defines the caller the row loses
    ``line`` and ``call_line``. One read per distinct file, off the event loop.
    """
    asks, defs = _row_checks(rows, callee_id)
    if not asks and not defs:
        return
    texts: dict[tuple[str, int, str], str] = {}
    moved: set[tuple[str, int, str]] = set()
    if repo_root:
        texts, moved = await asyncio.to_thread(
            _call_texts,
            Path(repo_root).resolve(),
            _by_file(asks.values()),
            _by_file(defs.values()),
        )
    for i, key in defs.items():
        if key in moved:
            rows[i].pop("line", None)
            asks.pop(i, None)
            rows[i].pop("call_line", None)
    for i, key in asks.items():
        if text := texts.get(key):
            rows[i]["text"] = text
        else:
            rows[i].pop("call_line", None)


#: A runtime load: ``import(...)``, ``importlib.import_module(...)``, ``__import__(...)``.
_DYNAMIC_LOAD = re.compile(r"(?<![\w$.])(?:import|__import__)\s*\(|\bimport_module\s*\(")
#: Stems too common to name one file: the quoted path must also carry the parent.
_GENERIC_STEMS = frozenset(
    {"index", "utils", "util", "types", "main", "helpers", "constants", "config", "mod",
     "__init__"}
)


def _module_spec(module_path: str) -> re.Pattern[str]:
    """A quoted module path ending in *module_path*'s stem, or its parent and stem when generic."""
    path = PurePosixPath(module_path)
    tail = re.escape(path.stem)
    if path.stem in _GENERIC_STEMS and path.parent.name:
        tail = rf"{re.escape(path.parent.name)}[/.]{tail}"
    return re.compile(rf"""["'`](?:[^"'`\n]*[/.])?{tail}(?:\.[A-Za-z]+)?["'`]""")


def _import_sites(
    root: Path, files: list[str], module_path: str
) -> dict[str, tuple[int, str, bool]]:
    """``file -> (line, text, dynamic)`` for the quoted path naming *module_path*.

    A line that imports, exports or loads it wins over an earlier plain mention.
    """
    spec = _module_spec(module_path)
    out: dict[str, tuple[int, str, bool]] = {}
    for rel in files:
        lines = _read_lines(root, rel) or []
        mention: int | None = None
        statements: set[int] | None = None
        for n, text in enumerate(lines, 1):
            if not spec.search(text):
                continue
            if statements is None:
                statements = _import_lines(lines)
            # ``import(`` may sit alone on the line above a wrapped path.
            wrapped = n > 1 and lines[n - 2].rstrip().endswith("import(")
            if wrapped or n in statements or _DYNAMIC_LOAD.search(text):
                mention = n
                break
            mention = mention or n
        if mention is not None:
            text = lines[mention - 1]
            dynamic = bool(_DYNAMIC_LOAD.search(text)) or (
                mention > 1 and lines[mention - 2].rstrip().endswith("import(")
            )
            out[rel] = (mention, text.strip()[:MAX_TEXT_CHARS], dynamic)
    return out


async def import_sites(
    repo_root: str | Path | None, files: list[str], module_path: str
) -> dict[str, tuple[int, str, bool]]:
    """Where each of *files* names *module_path* by a quoted path, and whether it loads it at runtime.

    Unquoted imports (a dotted Python ``from`` line) are not found; those files are absent.
    """
    if not repo_root or not files:
        return {}
    return await asyncio.to_thread(_import_sites, Path(repo_root).resolve(), files, module_path)


def _scan(
    root: Path,
    files: list[str],
    name: str,
    definition: tuple[str, int, int],
    call_lines: dict[str, set[int]],
    dirty: frozenset[str] = frozenset(),
) -> tuple[
    list[dict[str, Any]], list[str], int, set[str], bool, dict[str, set[str]], dict[str, str]
]:
    """``(sites, unreadable, stale call lines, files renaming it, default-exported,
    local import aliases per Python file, package declared per file)``.

    In a *dirty* file the graph's lines predate the edit, so a call is read
    from the text (the name followed by ``(``) instead.
    """
    word = _word(name)
    # Bound under any name by its importers, so no spelling can be scanned for.
    default_export = re.compile(r"\bexport\s+default\b|\bas\s+default\b")
    def_path, def_start, def_end = definition
    sites: list[dict[str, Any]] = []
    unreadable: list[str] = []
    stale = 0
    renamed: set[str] = set()
    python_aliases: dict[str, set[str]] = {}
    packages: dict[str, str] = {}
    is_default = False
    for rel in files:
        lines = _read_lines(root, rel)
        if lines is None:
            unreadable.append(rel)
            continue
        imports = _import_lines(lines)
        python = Path(rel).suffix in _PYTHON_EXTENSIONS
        if not python and (declared := _declared_package(lines)):
            packages[rel] = declared
        aliases, local = _renames(lines, name, imports, python)
        if aliases - local or _aliases_leave(rel, lines, local, imports):
            renamed.add(rel)
        elif python and local:
            python_aliases[rel] = local
        if rel == def_path and not python:
            # A CommonJS module's exports can rename on any line, so any use counts.
            commonjs = any(_CJS_EXPORT.search(t) for t in lines)
            if commonjs or any(default_export.search(t) and word.search(t) for t in lines):
                is_default = True
        spellings = [word] + [_word(a) for a in aliases]
        live = rel in dirty
        graph_calls = set() if live else call_lines.get(rel, set())
        def_seen = rel != def_path
        for n, text in enumerate(lines, 1):
            in_graph = n in graph_calls
            hits = [m for s in spellings for m in s.finditer(text)]
            if in_graph and not hits:
                stale += 1
                continue
            if not hits:
                continue
            if live:
                in_graph = any(_CALL_AFTER.match(text, m.end()) for m in hits)
            if not def_seen and def_start <= n <= def_end:
                kind, def_seen = "definition", True
            elif n in imports:
                kind = "import"
            elif in_graph:
                kind = "call"
            else:
                kind = "reference"
            sites.append({"path": rel, "line": n, "kind": kind, "text": text.strip()[:MAX_TEXT_CHARS]})
    return sites, unreadable, stale, renamed, is_default, python_aliases, packages


def _aliases_leave(rel: str, lines: list[str], aliases: set[str], imports: set[int]) -> bool:
    """True unless every use of an import alias in *rel* is one that cannot hand it on.

    Only a call, ``new``, a JSX tag or a member access counts as local; any
    value use (argument, assignment, export, ``extends``) may pass the binding
    to another file. A plain Python module's alias is also checked against its
    importers by the caller.
    """
    if not aliases:
        return False
    if Path(rel).name == "__init__.py":
        return True
    exports = _import_lines(lines, _EXPORT_START)
    alt = "|".join(re.escape(a) for a in sorted(aliases))
    spelled = re.compile(rf"(?<![\w$])(?:{alt})(?![\w$])")
    for n, text in enumerate(lines, 1):
        if n in imports and n not in exports:
            continue
        # On an export line only a call stays local: ``b.bind(null)`` hands on a copy.
        handed = n in exports or _EXPORT_LINE.match(text) or _CJS_EXPORT.search(text)
        for m in spelled.finditer(text):
            if handed:
                if not _CALL_AFTER.match(text, m.end()):
                    return True
            elif not (
                _LOCAL_USE_AFTER.match(text, m.end())
                or _LOCAL_USE_BEFORE.search(text, 0, m.start())
            ):
                return True
    return False


def _cap_references_per_file(sites: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """*sites* with plain references past the per-file cap dropped; other kinds always stay."""
    seen: Counter[str] = Counter()
    kept: list[dict[str, Any]] = []
    for site in sites:
        if site["kind"] == "reference":
            seen[site["path"]] += 1
            if seen[site["path"]] > MAX_REFERENCES_PER_FILE:
                continue
        kept.append(site)
    return kept


def _unseen_dirty_files(root: Path, local_path: str) -> list[str] | None:
    """Uncommitted files the index has not seen; ``None`` when git cannot say."""
    dirty = _working_tree_dirty_paths(local_path)
    if dirty is None:
        return None
    candidates = [d for d in dirty if d.split("/", 1)[0] not in PRUNED_DIRS]
    out: list[str] = []
    for rel in uncommitted_targets(local_path, candidates):
        path = root / rel
        if path.is_dir():
            found = (f for f in iter_glob(path, "*") if f.is_file())
            out.extend(f.relative_to(root).as_posix() for f in islice(found, MAX_FILES + 1))
        elif path.is_file():
            out.append(rel)
    return out


def _declared_package(lines: list[str]) -> str | None:
    declared = next(filter(None, map(_PACKAGE_LINE.match, lines)), None)
    return declared.group(1) if declared else None


def _package_scope(
    root: Path, node: GraphNode, language: str
) -> tuple[list[str], str | None, list[str]]:
    """``(files, package, reasons)``: live files that may use *node* without an
    import, its file's package, and why the edit set cannot be complete."""
    if language in _IMPORT_SCOPED_LANGUAGES:
        return [], None, []
    if language not in _DIRECTORY_PACKAGE_LANGUAGES:
        return [], None, [f"{language or 'unknown language'}: same-package use is not scanned"]
    def_file = node.file_path
    def_lines = _read_lines(root, def_file) or []
    package = _declared_package(def_lines)
    if not package:
        return [], None, [f"{language}: no package declaration"]
    parent = PurePosixPath(def_file).parent
    if language == "go":
        # The package is the directory: list it live, so a file the index
        # skipped (generated, build-tagged, oversize) is scanned too.
        exts = REGISTRY.extensions_for(("go",))
        try:
            names = sorted(e.name for e in os.scandir(root / parent) if e.is_file())
        except OSError:
            return [], package, ["go: package directory unreadable"]
        return [(parent / n).as_posix() for n in names if PurePosixPath(n).suffix in exts], package, []
    files, reasons = _jvm_package_files(root, node, def_lines, package, language)
    return files, package, reasons


def _jvm_package_files(
    root: Path, node: GraphNode, def_lines: list[str], package: str, language: str
) -> tuple[list[str], list[str]]:
    """``(files, reasons)``: JVM files that declare *package* or name the symbol in it.

    A JVM package can span directories (src/main and src/test) or sit off its
    path, and another package can name the symbol fully qualified with no
    import, which the graph records no edge for: both are found by text.
    Ceilings: the needles are literal, so ``package  a.b`` with odd spacing is
    missed, and gitignored sources (build/generated) are not searched, by design.
    """
    from repowise.server.mcp_server._line_hits import _files_holding

    def_file = node.file_path
    exts = REGISTRY.extensions_for(_JVM)
    top = id_segment_name(node.node_id.split("::")[1]) if "::" in node.node_id else _bare_name(node)
    needles = [f"package {package}", f"{package}.{top}"]
    if PurePosixPath(def_file).suffix == ".kt":
        # Java reaches a top-level Kotlin declaration through the file's facade
        # class, ``UtilKt`` or its ``@file:JvmName``, never by the bare name.
        facade = next(filter(None, map(_JVM_NAME.match, def_lines)), None)
        stem = facade.group(1) if facade else f"{PurePosixPath(def_file).stem}Kt"
        needles.append(f"{package}.{stem}")
    files: set[str] = set()
    reasons: list[str] = []
    for needle in needles:
        held = _files_holding(root, needle)
        if held is None:
            reasons.append(f"{language}: could not search for {needle}")
        else:
            files.update(f for f in held if PurePosixPath(f).suffix in exts)
    return sorted(files), reasons


def _live_definition(root: Path, node: GraphNode, name: str, language: str) -> tuple[int, int]:
    """*node*'s bounds re-parsed from its edited file; ``(0, 0)`` when it is not found."""
    text = read_repo_file_text(root, node.file_path)
    row = WikiSymbol(
        file_path=node.file_path,
        symbol_id=node.node_id,
        name=name,
        language=language,
        # Holds the parser's parent name; the id match is tried first anyway.
        parent_name=node.parent_symbol_id,
        start_line=node.start_line or 0,
        end_line=node.end_line or 0,
    )
    return (relocate_symbol(row, text) if text is not None else None) or (0, 0)


def _edge_names(raw: str | None) -> list[str]:
    """The JSON name list on an edge column; empty when absent or malformed."""
    try:
        return [str(n) for n in json.loads(raw or "[]")]
    except (TypeError, ValueError):
        return []


class _EdgeFacts(NamedTuple):
    """What the edges into the symbol say about its call sites."""

    call_lines: dict[str, set[int]]
    uncertain: bool
    dynamic: bool
    unlined: bool


class _ScanResult(NamedTuple):
    """The fields :func:`_scan` returns, by name."""

    sites: list[dict[str, Any]]
    unreadable: list[str]
    stale: int
    renamed_in: set[str]
    is_default: bool
    python_aliases: dict[str, set[str]]
    packages: dict[str, str]


async def _edges_into(session: AsyncSession, repo_id: str, node: GraphNode) -> list[Any]:
    """Edges targeting *node*, plus dynamic uses of its file."""
    cols = (
        GraphEdge.source_node_id,
        GraphEdge.target_node_id,
        GraphEdge.edge_type,
        GraphEdge.imported_names_json,
        GraphEdge.call_lines_json,
        GraphEdge.confidence,
    )
    return (
        await session.execute(
            select(*cols).where(
                GraphEdge.repository_id == repo_id,
                or_(
                    GraphEdge.target_node_id == node.node_id,
                    (GraphEdge.target_node_id == node.file_path)
                    & (GraphEdge.edge_type == "dynamic_uses"),
                ),
            )
        )
    ).all()


async def _importer_files(
    session: AsyncSession, repo_id: str, def_file: str, name: str, edges: list[Any]
) -> set[str]:
    """The definition file, its importers, and files re-exporting the name onward."""
    # A package barrel's own importers are scanned too.
    files: set[str] = {def_file}
    files.update(row[0] for row in edges if row[2] == "imports")
    frontier, seen = [def_file], {def_file}
    while frontier and len(files) <= MAX_FILES:
        rows = (
            await session.execute(
                select(GraphEdge.source_node_id, GraphEdge.imported_names_json).where(
                    GraphEdge.repository_id == repo_id,
                    GraphEdge.edge_type == "imports",
                    GraphEdge.target_node_id.in_(frontier),
                )
            )
        ).all()
        frontier = []
        for src, names_json in rows:
            files.add(src)
            names = _edge_names(names_json)
            # Unknown names may carry this one onward, so they are followed too.
            if src not in seen and (not names or name in names or "*" in names):
                seen.add(src)
                frontier.append(src)
    return files


def _edge_facts(edges: list[Any], node: GraphNode, name: str, files: set[str]) -> _EdgeFacts:
    """Call lines per file from the call edges; adds each calling file to *files*."""
    call_lines: dict[str, set[int]] = {}
    uncertain = dynamic = unlined = False
    for src, tgt, etype, names_json, lines_json, conf in edges:
        if etype == "dynamic_uses":
            names = _edge_names(names_json)
            if tgt == node.node_id or not names or name in names:
                dynamic = True
            continue
        if tgt != node.node_id or etype in ("imports", "defines"):
            continue
        src_file = src.split("::", 1)[0]
        lines = {int(n) for n in _edge_names(lines_json) if n.isdigit()}
        if etype == "calls" and not lines:
            unlined = True
        if not lines:
            continue
        files.add(src_file)
        call_lines.setdefault(src_file, set()).update(lines)
        if (conf or 0) < _MIN_CALL_CONFIDENCE:
            uncertain = True
    return _EdgeFacts(call_lines, uncertain, dynamic, unlined)


async def _node_language(session: AsyncSession, repo_id: str, node: GraphNode) -> str:
    """*node*'s language, else its file node's, lower-cased."""
    language = node.language or await session.scalar(
        select(GraphNode.language).where(
            GraphNode.repository_id == repo_id, GraphNode.node_id == node.file_path
        )
    )
    return (language or "").lower()


def _capped_candidates(
    files: set[str], unseen: list[str], reasons: list[str]
) -> tuple[list[str], list[str]]:
    """Indexed and unindexed candidate files, together at most ``MAX_FILES``."""
    unindexed = sorted(set(unseen) - files)
    ordered = sorted(files)
    if len(ordered) + len(unindexed) > MAX_FILES:
        reasons.append(f"over {MAX_FILES} candidate files")
        ordered = ordered[:MAX_FILES]
        unindexed = unindexed[: max(0, MAX_FILES - len(ordered))]
    return ordered, unindexed


async def _definition_span(
    root: Path, node: GraphNode, name: str, language: str, dirty: frozenset[str]
) -> tuple[str, int, int]:
    """``(file, start, end)`` of the definition, re-parsed when its file has edits."""
    def_file = node.file_path
    if def_file in dirty:
        # Indexed bounds predate the edit; re-parse as get_symbol does.
        return (def_file, *await asyncio.to_thread(_live_definition, root, node, name, language))
    return (def_file, node.start_line or 0, node.end_line or node.start_line or 0)


async def _python_alias_exports(
    session: AsyncSession, repo_id: str, python_aliases: dict[str, set[str]], renamed_in: set[str]
) -> None:
    """Add to *renamed_in* each file whose Python alias another file imports."""
    if not python_aliases:
        return
    # Any module-level Python name is importable, so an alias another file
    # imports (or may, by unknown names or ``*``) leaves its file.
    rows = await session.execute(
        select(GraphEdge.target_node_id, GraphEdge.imported_names_json).where(
            GraphEdge.repository_id == repo_id,
            GraphEdge.edge_type == "imports",
            GraphEdge.target_node_id.in_(list(python_aliases)),
        )
    )
    for tgt, names_json in rows.all():
        names = _edge_names(names_json)
        if not names or "*" in names or python_aliases[tgt] & set(names):
            renamed_in.add(tgt)


def _evidence_reasons(scan: _ScanResult, facts: _EdgeFacts) -> list[str]:
    """Why the scan and the graph edges leave the set incomplete."""
    reasons: list[str] = []
    if scan.unreadable:
        reasons.append(f"unreadable: {', '.join(scan.unreadable[:3])}")
    if scan.stale:
        reasons.append(f"stale: {scan.stale} graph call lines no longer name it")
    if facts.unlined:
        reasons.append("call edges without line numbers")
    if facts.uncertain:
        reasons.append("low-confidence call edges")
    if facts.dynamic:
        reasons.append("dynamic use of the symbol or its file")
    return reasons


def _foreign_package_files(
    package_files: list[str],
    scanned: set[str],
    packages: dict[str, str],
    package: str | None,
    language: str,
) -> list[str]:
    """Go files in the definition's directory that declare another package."""
    # The directory is not the package then. Its external test package
    # ``<name>_test`` imports it like any other.
    return [
        f
        for f in package_files
        if language == "go" and f in scanned and packages.get(f) not in (package, f"{package}_test")
    ]


def _scope_reasons(
    node: GraphNode, scan: _ScanResult, foreign: list[str], language: str, unindexed: set[str]
) -> list[str]:
    """Why renames, default exports, members or unindexed edits leave the set incomplete."""
    reasons: list[str] = []
    if foreign:
        reasons.append(f"{language}: another package shares its directory: {', '.join(foreign[:3])}")
    if scan.renamed_in:
        reasons.append(f"renamed on import or export: {', '.join(sorted(scan.renamed_in)[:3])}")
    if scan.is_default:
        reasons.append("default export: importers may rename it")
    if node.kind in _MEMBER_KINDS or node.node_id.count("::") > 1:
        reasons.append("member: calls through untyped receivers are not scanned")
    touched = sorted({s["path"] for s in scan.sites if s["path"] in unindexed})
    if touched:
        reasons.append(f"unindexed edits name it: {', '.join(touched[:3])}")
    has_definition = any(s["kind"] == "definition" for s in scan.sites)
    if not has_definition and node.file_path not in scan.unreadable:
        reasons.append("definition not found at its indexed line")
    return reasons


def _served_sites(
    sites: list[dict[str, Any]],
    reasons: list[str],
    node: GraphNode,
    collector: OmissionCollector | None,
) -> dict[str, Any]:
    """Sort and cap *sites* into the reply, recording what the caps hid."""
    sites.sort(
        key=lambda s: (
            s["kind"] != "definition",
            is_test_related_path(s["path"]),
            s["path"],
            s["line"],
        )
    )
    kept = _cap_references_per_file(sites) if collector is not None else sites
    if len(kept) > MAX_SITES:
        reasons.append(f"over {MAX_SITES} sites")
    if len(kept) < len(sites):
        reasons.append(
            f"{len(sites) - len(kept)} references past {MAX_REFERENCES_PER_FILE} per file"
            " not shown: sites_omitted_by_file; the full list is under _meta.omitted"
        )
    out: dict[str, Any] = {"sites": kept[:MAX_SITES], "complete": not reasons}
    if collector is None:
        if len(sites) > MAX_SITES:
            out["sites_omitted"] = len(sites) - MAX_SITES
    elif len(out["sites"]) < len(sites):
        shown = {id(s) for s in out["sites"]}
        cap_collection(
            out,
            "sites",
            sites,
            MAX_SITES,
            collector,
            emitted=out["sites"],
            label=f"{node.node_id} :: references.sites not shown",
            reason="per_file_reference_cap" if len(kept) < len(sites) else "construction_cap",
        )
        out["sites_omitted_by_file"] = dict(
            Counter(s["path"] for s in sites if id(s) not in shown)
        )
    if reasons:
        out["reasons"] = reasons
    return out


async def reference_edit_set(
    session: AsyncSession,
    repo_id: str,
    repo_root: str | Path,
    node: GraphNode,
    collector: OmissionCollector | None = None,
) -> dict[str, Any]:
    """Every live site naming *node*, classified, with a completeness verdict.

    Returns ``{"sites": [{"path", "line", "kind", "text"}], "complete": bool}``
    plus ``reasons`` when incomplete and ``sites_omitted`` past the cap. Kinds:
    definition | import | call | reference. With a *collector*, plain
    references past ``MAX_REFERENCES_PER_FILE`` in one file move to it and
    ``sites_omitted_by_file`` counts every row not shown.
    """
    root = Path(repo_root).resolve()
    name = _bare_name(node)
    reasons: list[str] = []

    edges = await _edges_into(session, repo_id, node)
    files = await _importer_files(session, repo_id, node.file_path, name, edges)
    facts = _edge_facts(edges, node, name, files)

    unseen = await asyncio.to_thread(_unseen_dirty_files, root, str(repo_root))
    if unseen is None:
        reasons.append("working tree status unknown")
        unseen = []
    dirty = frozenset(unseen)

    language = await _node_language(session, repo_id, node)
    package_files, package, why = await asyncio.to_thread(_package_scope, root, node, language)
    files.update(package_files)
    reasons += why

    ordered, unindexed = _capped_candidates(files, unseen, reasons)
    definition = await _definition_span(root, node, name, language, dirty)
    scan = _ScanResult(
        *await asyncio.to_thread(
            _scan, root, ordered + unindexed, name, definition, facts.call_lines, dirty
        )
    )
    await _python_alias_exports(session, repo_id, scan.python_aliases, scan.renamed_in)

    reasons += _evidence_reasons(scan, facts)
    scanned = set(ordered) - set(scan.unreadable)
    foreign = _foreign_package_files(package_files, scanned, scan.packages, package, language)
    reasons += _scope_reasons(node, scan, foreign, language, set(unindexed))
    return _served_sites(scan.sites, reasons, node, collector)
