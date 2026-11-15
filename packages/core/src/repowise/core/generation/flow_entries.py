"""What a user runs or calls, and what the project's own docs say about it.

Flow pages walk operations a reader asks about: a CLI command, an agent tool,
an HTTP route, a library's public call. Those are declared, not inferred, so
they are read here from what declares them:

* Python: the decorators the parser keeps on each symbol (``@x.command("init")``,
  ``@mcp.tool()``, ``@app.route("/x")``), and the names a top-level package
  re-exports from its ``__init__.py``.
* Go: ``cobra.Command{Use: "x", RunE: fn}`` literals, a ``Name() string``
  method returning a literal on a type that has ``Run`` (simplecobra), and
  ``Handle("/x", fn)`` routes.

Ceiling: decorator and literal patterns per framework family. Not covered:
JS/TS commander or yargs, argparse sub-parsers, Java/C# attributes; those
repos fall back to the call-graph roots :mod:`.flow_pages` ranks.

The docs side splits the root README and a top-level ``docs`` tree into
heading sections and keeps the code a reader types (inline spans, code
blocks), so an entry can be ranked by how often the docs show it in use and a
page can quote the sections that mention it.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from repowise.core.fs_walk import walk_repo
from repowise.core.support_paths import DOC_EXTENSIONS, is_support_path
from repowise.core.test_paths import is_test_path

#: ``path -> lines`` of a repository file, empty when it cannot be read.
LineReader = Callable[[str], list[str]]

CLI = "CLI command"
TOOL = "Agent tool"
ROUTE = "HTTP route"
API = "Public API"
FUNCTION = "Function"


@dataclass(frozen=True)
class Entry:
    """Something a user or another program calls, and the function that runs it."""

    node: str
    kind: str
    public: str


# ---------------------------------------------------------------------------
# Reading files
# ---------------------------------------------------------------------------


def line_reader(repo_root: Path | str | None, source_map: Mapping[str, bytes] | None) -> LineReader:
    """A memoised ``path -> lines`` over the bytes already read, else the disk."""
    cache: dict[str, list[str]] = {}
    root = Path(repo_root) if repo_root else None
    held = source_map or {}

    def read(path: str) -> list[str]:
        if path not in cache:
            raw = held.get(path)
            if raw is None and root is not None:
                try:
                    raw = (root / path).read_bytes()
                except OSError:
                    raw = b""
            cache[path] = (raw or b"").decode("utf-8", errors="replace").splitlines()
        return cache[path]

    return read


# ---------------------------------------------------------------------------
# Declared entries
# ---------------------------------------------------------------------------

_CLI_DECO = re.compile(r"^@(?:\w+\.)*(?:command|group)\s*\(\s*(?:name\s*=\s*)?(?:[\"']([\w-]+)[\"'])?")
_TOOL_DECO = re.compile(r"^@(?:\w+\.)*tool\b")
_ROUTE_DECO = re.compile(
    r"^@\w+(?:\.\w+)*\.(?:route|get|post|put|delete|patch|websocket|api_route)\(\s*[\"']([^\"']+)"
)
_GO_NAME = re.compile(r"^func \(\w+ \*?(\w+)\) Name\(\) string \{")
_GO_RETURN = re.compile(r'\s*return "([\w-]+)"')
_GO_USE = re.compile(r"\bUse:\s*\"([\w-]+)")
_GO_RUN = re.compile(r"\bRunE?:\s*(?:\w+\.)?(\w+)\s*[,}]?\s*$")
_GO_HANDLE = re.compile(r"\bHandle(?:Func)?\(\s*\"([^\"]+)\"\s*,\s*(?:\w+\.)?(\w+)\s*\)")
# ``from .mod import a, b`` or the parenthesised multi-line form.
_REEXPORT = re.compile(r"^from \.([\w.]*) import (?:\(([^)]*)\)|([^\n(]+))$", re.M)
_re_comment = re.compile(r"#[^\n]*")
_INIT = re.compile(r"(?:src/)?\w+/__init__\.py")
_CALLABLE = ("function", "method")
_PROGRAM = re.compile(r"[\w.-]+")
# Commands no decorator declares: argparse sub-parsers and JS/TS commander.
_ARGPARSE = re.compile(r"\.add_parser\(\s*[\"']([\w-]+)[\"']")
_COMMANDER = re.compile(r"\.command\(\s*['\"]([\w-]+)")


def text_commands(paths: Iterable[str], read: LineReader, program: str = "") -> list[Entry]:
    """CLI commands declared by a call rather than a decorator, one per declaration.

    The node is ``path:line``: these are listed for readers, not walked, since
    the handler a sub-parser dispatches to is not named at the declaration.
    """
    out: list[Entry] = []
    for path in sorted(p for p in paths if _production(p)):
        rx = _ARGPARSE if path.endswith(".py") else _COMMANDER
        if not path.endswith((".py", ".js", ".mjs", ".ts")):
            continue
        for i, line in enumerate(read(path)):
            if m := rx.search(line):
                out.append(Entry(f"{path}:{i + 1}", CLI, _cli_public(program, m.group(1))))
    return out


def entry_cite(graph: Any, entry: Entry) -> str:
    """``path:line`` where *entry* is declared: its symbol's first line, or its own node."""
    attrs = graph.nodes.get(entry.node, {}) if entry.node in graph else {}
    path, line = attrs.get("file_path"), attrs.get("start_line")
    return f"{path}:{line}" if path and line else entry.node.split("::", 1)[0]


def _cli_public(program: str, command: str) -> str:
    return command if not program or command == program else f"{program} {command}"


def _decorator_entry(node: str, attrs: Mapping[str, Any], program: str) -> Entry | None:
    """The entry a Python decorator declares on *node*, if any."""
    name = attrs.get("name") or ""
    for text in attrs.get("decorators") or ():
        if m := _CLI_DECO.match(text):
            if not m.group(1) and re.search(r"\bgroup\s*\(", text):
                continue  # an unnamed top-level group is the program, not a command
            command = m.group(1) or re.sub(r"_(command|cmd)$", "", name).replace("_", "-")
            return Entry(node, CLI, _cli_public(program, command))
        if _TOOL_DECO.match(text):
            return Entry(node, TOOL, name)
        if m := _ROUTE_DECO.match(text):
            return Entry(node, ROUTE, m.group(1))
    return None


def _symbols_by_file(graph: Any) -> dict[str, list[tuple[str, str]]]:
    """``file -> [(name, node)]`` for functions and methods, in node order."""
    out: dict[str, list[tuple[str, str]]] = {}
    for node, attrs in graph.nodes(data=True):
        if attrs.get("node_type") == "symbol" and attrs.get("kind") in _CALLABLE:
            out.setdefault(attrs.get("file_path") or "", []).append((attrs.get("name") or "", node))
    return out


def _first_named(symbols: Iterable[tuple[str, str]]) -> dict[str, str]:
    """``name -> node``, the first symbol under each name."""
    out: dict[str, str] = {}
    for name, node in symbols:
        out.setdefault(name, node)
    return out


def _go_named_command(path: str, lines: list[str], i: int, graph: Any, program: str) -> Entry | None:
    """simplecobra: ``func (c *x) Name() string { return "lit" }`` on a type with ``Run``."""
    m = _GO_NAME.match(lines[i])
    lit = _GO_RETURN.match(lines[i + 1]) if m and i + 1 < len(lines) else None
    node = f"{path}::{m.group(1)}::Run" if m else ""
    return Entry(node, CLI, _cli_public(program, lit.group(1))) if lit and node in graph else None


def _go_line_entry(
    path: str, lines: list[str], i: int, ctx: tuple[Mapping[str, str], Any, str]
) -> Entry | None:
    names, graph, program = ctx
    if _GO_NAME.match(lines[i]):
        return _go_named_command(path, lines, i, graph, program)
    if m := _GO_USE.search(lines[i]):  # cobra.Command{Use: "x", RunE: fn}
        run = next((r for r in map(_GO_RUN.search, _literal_body(lines, i)) if r), None)
        if run and run.group(1) in names:
            return Entry(names[run.group(1)], CLI, _cli_public(program, m.group(1)))
        return None
    m = _GO_HANDLE.search(lines[i])
    return Entry(names[m.group(2)], ROUTE, m.group(1)) if m and m.group(2) in names else None


def _literal_body(lines: list[str], i: int) -> list[str]:
    """Lines of the command literal opened at *i*: up to the next ``Use:`` (40 at most)."""
    window = lines[i : i + 40]
    nxt = next((k for k in range(1, len(window)) if _GO_USE.search(window[k])), len(window))
    return window[:nxt]


def _go_file_entries(
    path: str, lines: list[str], names: Mapping[str, str], graph: Any, program: str
) -> Iterable[Entry]:
    for i in range(len(lines)):
        entry = _go_line_entry(path, lines, i, (names, graph, program))
        if entry is not None:
            yield entry


def _python_public_api(
    paths: Iterable[str], by_file: Mapping[str, list[tuple[str, str]]], read: LineReader
) -> Iterable[Entry]:
    """Functions and public methods a top-level package re-exports by name."""
    for init in paths:
        if not _INIT.fullmatch(init) or init.split("/")[-2].startswith("test"):
            continue
        pkg = init.rsplit("/", 1)[0]
        for module, wrapped, plain in _REEXPORT.findall("\n".join(read(init))):
            names = _re_comment.sub("", wrapped or plain)
            src = f"{pkg}/{module.replace('.', '/')}.py" if module else init
            if src not in by_file:
                src = f"{pkg}/{module.replace('.', '/')}/__init__.py"
            yield from _reexported(src, names, by_file.get(src, []))


def _reexported(src: str, names: str, symbols: list[tuple[str, str]]) -> Iterable[Entry]:
    for part in names.split(","):
        bits = [b.strip() for b in part.strip().split(" as ")]
        if not bits[0].isidentifier() or bits[-1].startswith("_"):
            continue
        name, alias = bits[0], bits[-1]
        prefix = f"{src}::{name}::"
        for method, node in symbols:
            if node == f"{src}::{name}":
                yield Entry(node, API, alias)
            elif node.startswith(prefix) and not method.startswith("_"):
                yield Entry(node, API, f"{alias}.{method}")


def _production(path: str) -> bool:
    return bool(path) and not (is_test_path(path) or is_support_path(path))


def script_for(path: str, scripts: Mapping[str, str], default: str) -> str:
    """The console script whose entry module's package holds *path*, deepest first.

    Scripts sharing that package (a main CLI and its hook launchers) resolve to
    *default*, the project-named script, else to the shortest name.
    """
    hits = [
        (-pkg.count("/"), name != default, len(name), name)
        for name, pkg in scripts.items()
        if f"/{pkg}/" in f"/{path}"
    ]
    return min(hits)[3] if hits else default


def _decorated_entries(graph: Any, program: str, scripts: Mapping[str, str]) -> Iterable[Entry]:
    for node, attrs in graph.nodes(data=True):
        if attrs.get("node_type") != "symbol" or attrs.get("kind") not in _CALLABLE:
            continue
        path = attrs.get("file_path") or ""
        entry = _decorator_entry(node, attrs, script_for(path, scripts, program))
        if entry and _production(path):
            yield entry


def _go_entries(
    by_file: Mapping[str, list[tuple[str, str]]], graph: Any, read: LineReader, program: str
) -> Iterable[Entry]:
    for path in sorted(p for p in by_file if p.endswith(".go") and _production(p)):
        yield from _go_file_entries(path, read(path), _first_named(by_file[path]), graph, program)


def declared_entries(
    graph: Any, read: LineReader, program: str = "", scripts: Mapping[str, str] | None = None
) -> list[Entry]:
    """Every declared command, tool and route, plus a Python library's public API.

    *scripts* maps each console script to its entry module's package path, so a
    command is named after the script that reaches it; *program* is the fallback.
    """
    by_file = _symbols_by_file(graph)
    inits = sorted(p for p in _file_paths(graph) if _production(p))
    out: dict[str, Entry] = {}
    for entry in (
        *_decorated_entries(graph, program, scripts or {}),
        *_go_entries(by_file, graph, read, program),
        *_python_public_api(inits, by_file, read),
    ):
        out.setdefault(entry.node, entry)
    return sorted(out.values(), key=lambda e: e.node)


def _file_paths(graph: Any) -> list[str]:
    return [n for n, a in graph.nodes(data=True) if a.get("node_type") == "file"]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _pyproject(root: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(_read_text(root / "pyproject.toml")).get("project") or {}
    except tomllib.TOMLDecodeError:
        return {}


def python_scripts(repo_root: Path | str | None) -> dict[str, str]:
    """``console script -> package path`` of its entry module (``a.cli.main:run`` -> ``a/cli``)."""
    if not repo_root:
        return {}
    scripts = _pyproject(Path(repo_root)).get("scripts") or {}
    out = {}
    for name, target in scripts.items():
        module = str(target).split(":", 1)[0].strip().split(".")
        out[name] = "/".join(module[:-1] or module)
    return out


def _python_script(root: Path) -> str:
    """The script named after the project, else the first by name."""
    scripts = sorted(_pyproject(root).get("scripts") or {})
    project = str(_pyproject(root).get("name") or "")
    return project if project in scripts else (scripts[0] if scripts else "")


def _npm_bin(root: Path) -> str:
    try:
        data = json.loads(_read_text(root / "package.json") or "{}")
    except ValueError:
        return ""
    binaries = data.get("bin") if isinstance(data, dict) else None
    names: list[str] = []
    if isinstance(binaries, dict):
        names = sorted(str(k) for k in binaries)
    elif isinstance(binaries, str) and data.get("name"):
        names = [str(data["name"]).split("/")[-1]]
    # Manifest keys are untrusted text that reaches prompts: names only.
    return next((n for n in names if _PROGRAM.fullmatch(n)), "")


def _go_program(root: Path, graph: Any) -> str:
    m = re.search(r"^module\s+(\S+)", _read_text(root / "go.mod"), re.M)
    if not m or "main.go" not in graph:
        return ""
    # A major-version suffix (``.../hugo/v2``) is not the program's name.
    segments = [s for s in m.group(1).rstrip("/").split("/") if not re.fullmatch(r"v\d+", s)]
    return segments[-1] if segments and _PROGRAM.fullmatch(segments[-1]) else ""


def program_name(repo_root: Path | str | None, graph: Any) -> str:
    """What a user types to start the program, from the root manifest.

    Read from the checkout rather than the ingestion's launcher list, which a
    graph rebuilt from the database does not carry: generation, the update
    cascade and cost estimation must name a command the same way.
    """
    if not repo_root:
        return ""
    root = Path(repo_root)
    return _python_script(root) or _npm_bin(root) or _go_program(root, graph)


# ---------------------------------------------------------------------------
# Documentation sections
# ---------------------------------------------------------------------------

_DOC_DIRS = ("docs", "doc", "documentation")
# The documentation formats whose headings the section reader below understands.
_SECTION_EXTS = tuple(sorted(DOC_EXTENSIONS & {".md", ".rst"}))
_SKIP_DOC_DIRS = frozenset({"node_modules", "tests", "test", "fixtures", "vendor"})
_MAX_DOC_FILES = 3000
_RST_UNDERLINE = re.compile(r"([=\-~^\"'`#*+])\1+")


@dataclass(frozen=True)
class DocSection:
    path: str
    heading: str
    start: int  # 1-based, inclusive
    end: int
    text: str
    spans: tuple[str, ...]  # code a reader types: inline spans and code-block lines

    @property
    def cite(self) -> str:
        return f"{self.path}:{self.start}-{self.end}"


def _is_root_doc(p: Path) -> bool:
    return p.is_file() and p.suffix.lower() in _SECTION_EXTS and p.stem.lower() in ("readme", "contributing")


def _is_doc_page(name: str) -> bool:
    low = name.lower()
    return low.endswith(_SECTION_EXTS) and "changelog" not in low


def _walk_docs(root: Path, base: Path) -> Iterable[str]:
    for dirpath, dirnames, filenames in walk_repo(base):
        dirnames[:] = sorted(
            d for d in dirnames if d.lower() not in _SKIP_DOC_DIRS and not d.startswith(".")
        )
        rel = dirpath.relative_to(root).as_posix()
        yield from (f"{rel}/{f}" for f in sorted(filenames) if _is_doc_page(f))


def doc_files(root: Path) -> list[str]:
    """The root README and CONTRIBUTING (or ``.github``'s) plus every page under a docs dir."""
    out = [p.name for p in sorted(root.iterdir()) if _is_root_doc(p)]
    github = root / ".github"
    if github.is_dir():
        out += [f".github/{p.name}" for p in sorted(github.iterdir()) if _is_root_doc(p)]
    for top in _DOC_DIRS:
        if (root / top).is_dir():
            out += _walk_docs(root, root / top)
    # Ceiling: a docs tree past this many pages is sampled in path order.
    return out[:_MAX_DOC_FILES]


def _md_heads(lines: list[str]) -> list[int]:
    heads, fenced = [], False
    for i, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced and re.match(r"#{1,4} ", line):
            heads.append(i)
    return heads


def _rst_heads(lines: list[str]) -> list[int]:
    """reStructuredText titles: a text line underlined by one repeated punctuation char."""
    heads = []
    for i in range(len(lines) - 1):
        title, under = lines[i].rstrip(), lines[i + 1].rstrip()
        stripped = title.strip()
        if stripped and not title.startswith(" ") and len(under) >= len(stripped) >= 2 and _RST_UNDERLINE.fullmatch(under):
            heads.append(i)
    return heads


def _code_spans(text: str, rst: bool) -> tuple[str, ...]:
    spans = re.findall(r"`{1,2}([^`\n]+)`{1,2}", text)
    fenced = False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif fenced or (rst and line.startswith("    ")):
            spans.append(line.strip())
    return tuple(re.sub(r"^\$\s*", "", s.strip()) for s in spans if s.strip())


def split_sections(path: str, lines: list[str]) -> list[DocSection]:
    rst = path.lower().endswith(".rst")
    heads = (_rst_heads(lines) if rst else _md_heads(lines)) or [0]
    if heads[0] != 0:
        heads = [0, *heads]
    out = []
    for a, b in zip(heads, [*heads[1:], len(lines)], strict=True):
        text = "\n".join(lines[a:b]).strip()
        if text:
            out.append(DocSection(path, lines[a].lstrip("# ").strip(), a + 1, b, text, _code_spans(text, rst)))
    return out


@lru_cache(maxsize=4)
def _sections_cached(root: str, stamp: tuple[tuple[str, int, int], ...]) -> tuple[DocSection, ...]:
    out: list[DocSection] = []
    for path, _mtime, _size in stamp:
        try:
            text = (Path(root) / path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        out += split_sections(path, text.splitlines())
    return tuple(out)


def doc_sections(repo_root: Path | str | None) -> tuple[DocSection, ...]:
    """The project's documentation split by heading; cached while no file changes."""
    if not repo_root or not Path(repo_root).is_dir():
        return ()
    root = Path(repo_root)
    stamp = []
    for path in doc_files(root):
        try:
            st = (root / path).stat()
        except OSError:
            continue
        stamp.append((path, st.st_mtime_ns, st.st_size))
    return _sections_cached(str(root), tuple(stamp))


def mention_pattern(entry: Entry) -> re.Pattern[str]:
    """How a doc's code names *entry* the way a user types or calls it."""
    public = entry.public
    if entry.kind == CLI:
        return re.compile(rf"^{re.escape(public)}(\s|$)")
    if entry.kind == ROUTE:
        # The whole path, not the tail of a longer one: a router-relative
        # ``/{id}`` must not match every ``/api/items/{id}`` in the docs.
        return re.compile(rf"(?<![\w/{{}}-]){re.escape(public)}(?![\w/{{}}-])")
    if "." in public:
        method = re.escape(public.rsplit(".", 1)[1])
        return re.compile(rf"(^|\.){method}(\(|$)|^{re.escape(public)}")
    name = re.escape(public)
    return re.compile(rf"(^|[^\w.]){name}\(|^{name}$|import .*\b{name}\b")


def doc_mentions(entry: Entry, sections: Iterable[DocSection]) -> int:
    """Doc sections whose code names the entry as typed."""
    rx = mention_pattern(entry)
    return sum(any(rx.search(s) for s in sec.spans) for sec in sections)


def quickstart_entries(entries: Iterable[Entry], sections: Iterable[DocSection]) -> set[str]:
    """Nodes of the entries the root README's first example shows in code.

    That example (a quickstart, a "using it" block) is what a newcomer runs
    first, so its entries are walked before better-mentioned ones elsewhere.
    """
    patterns = [(e.node, mention_pattern(e)) for e in entries]
    for sec in sections:
        if "/" in sec.path or PurePosixPath(sec.path).stem.lower() != "readme":
            continue
        shown = {node for node, rx in patterns if any(rx.search(s) for s in sec.spans)}
        if shown:
            return shown
    return set()


def is_ui_path(path: str) -> bool:
    """A UI component file: the render tree is not an operation a walkthrough tells."""
    p = PurePosixPath(path)
    parts = {s.lower() for s in p.parts[:-1]}
    return bool(parts & _UI_SEGMENTS) or p.suffix in (".tsx", ".jsx", ".vue", ".svelte")


_UI_SEGMENTS = frozenset(["ui", "web", "frontend", "components", "views", "templates", "static", "pages", "widgets"])
