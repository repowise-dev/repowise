"""Tests the dependency graph cannot see into, found from their source text.

A test's import edges say which files it exercises, so test selection can skip
it when none of those files changed. Two shapes of test break that promise
while still importing something the graph resolves:

* a **tree-walking** test lists a source directory (``rglob``, ``os.walk``,
  ``readdirSync``, a glob, ``git ls-files``) and reads what it finds, so it
  depends on every file under that root, including files added later;
* a **self-running** test starts the project's own command or module in a
  child process (a console script, ``sys.executable -m <own module>``, code
  passed to ``-c`` that imports the project, the package's ``bin``), so the
  code it exercises is reached through a process, not an import.

:func:`always_run_reason` names which, or returns ``None``. It reads only
the text it is given, so the indexer runs it once per test file while the
bytes are in memory and stores the answer on the file's graph node; selection
reads that stored answer and runs such tests with every subset.

Precision matters as much as recall here: every detected test joins every
subset. A walk only counts when its root is anchored at the test's own
location or the working directory (``__file__``, ``import.meta.url``,
``process.cwd()``), directly, through a variable assigned from such an
anchor, or through a local function called with one; a root naming fixtures,
test data, snapshots or a temporary directory does not count. Ceiling: the
check is textual and per file, so a walk done by a shared helper the test
calls (``list_tracked_files()``) is not seen; detecting walks in helpers and
marking their importers is the upgrade path. Python, JavaScript and
TypeScript only.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

_PY = ".py"
_JS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")


@dataclass(frozen=True)
class _Lang:
    # Plain substrings every match of the pattern below contains: a test
    # holding none of them is passed over without running a regex.
    walk_tokens: tuple[str, ...]
    read_tokens: tuple[str, ...]
    spawn_tokens: tuple[str, ...]
    walk: re.Pattern[str]
    read: re.Pattern[str]
    anchor: re.Pattern[str]
    spawn: re.Pattern[str]
    assign: re.Pattern[str]
    loop: re.Pattern[str]
    func: re.Pattern[str]


_PY_LANG = _Lang(
    walk_tokens=("glob(", "walk(", "listdir(", "scandir(", "iterdir(", "ls-files"),
    read_tokens=("read_text(", "read_bytes(", "open(", "parse(", "tokenize."),
    spawn_tokens=("subprocess", "os.system(", "os.exec", "pexpect"),
    walk=re.compile(
        r"\.rglob\(|\bos\.walk\(|\.glob\(|\bglob\.i?glob\(|\bos\.listdir\(|\bos\.scandir\("
        r"|\.iterdir\(|ls-files"
    ),
    read=re.compile(r"read_text\(|read_bytes\(|\bopen\(|\bast\.parse\(|\btokenize\."),
    anchor=re.compile(r"__file__|\bPath\.cwd\(\)|\bos\.getcwd\(\)"),
    spawn=re.compile(
        r"\bsubprocess\.(?:run|Popen|call|check_call|check_output)\b|create_subprocess_(?:exec|shell)"
        r"|\bos\.system\(|\bos\.exec[lv]p?e?\(|\bpexpect\.spawn"
        r"|from subprocess import"
    ),
    assign=re.compile(r"^\s*([A-Za-z_]\w*)\s*(?::[^=\n]+)?=(?!=)\s*(.*)$"),
    loop=re.compile(r"^\s*for\s+([A-Za-z_]\w*)\s+in\s+(.*)$"),
    func=re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\("),
)

_JS_LANG = _Lang(
    walk_tokens=("readdir", "opendir", "glob", "fg(", "fg.sync(", "ls-files"),
    read_tokens=("readFile", "createReadStream"),
    spawn_tokens=("spawn", "exec", "fork", "Bun.spawn"),
    walk=re.compile(
        r"\breaddir(?:Sync)?\s*\(|\bopendir(?:Sync)?\s*\(|\bglob(?:Sync)?\s*\(|\bfg(?:\.sync)?\s*\("
        r"|\bfs\.glob\b|ls-files"
    ),
    read=re.compile(r"\breadFile(?:Sync)?\s*\(|\bcreateReadStream\s*\("),
    anchor=re.compile(r"\bimport\.meta\.(?:url|dirname|filename)\b|\b__dirname\b|\b__filename\b"
                      r"|\bprocess\.cwd\(\)"),
    spawn=re.compile(
        r"\b(?:spawn|spawnSync|exec|execSync|execFile|execFileSync|fork)\s*\("
        r"|\bexeca(?:Sync|Node)?\s*\(|\bBun\.spawn"
    ),
    assign=re.compile(
        r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=\n]+)?=(?!=)\s*(.*)$"
    ),
    loop=re.compile(r"^\s*for\s*\(\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s+of\s+(.*)$"),
    func=re.compile(
        r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)\s*\("
        r"|^\s*(?:export\s+)?(?:const|let)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?"
        r"(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*(?::[^=]+)?=>"
    ),
)

# A JS spawn call only counts where a process API is imported: a bare
# ``exec(`` is also ``RegExp.prototype.exec``.
_JS_PROCESS_TOKENS = ("child_process", "execa", "Bun.spawn")
_IDENT = re.compile(r"[A-Za-z_$][\w$]*")

# A root naming any of these is a test's own material, not the source tree.
_LOCAL_ROOT = re.compile(
    r"fixture|testdata|test_data|__snapshots__|snapshot|golden|mkdtemp|tmp_path|tmpdir"
    r"|tmp_dir|tempdir|temp_dir|\btmp\b|tmpRoot|tempRoot|os\.tmpdir|gettempdir",
    re.IGNORECASE,
)

# An anchor used to load a module (``createRequire(import.meta.url)``) roots
# an import, not a directory.
_MODULE_LOAD = re.compile(r"\bcreateRequire\b|\brequire\(|\bimport\(|\bimportlib\b")

# String literals on one line. An escape and a plain character never match the
# same backslash, so an unclosed quote cannot backtrack exponentially.
_STRING = re.compile(r"""(?P<q>["'`])((?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)""")
# A string opening a call or a list, where a command name sits; a command name
# elsewhere (``tmp_path / "repowise"``) is a path or a message.
_LEADING_STRING = re.compile(r"""[\[(]\s*(?P<q>["'`])((?:\\.|(?!(?P=q))[^\\\n])*)(?P=q)""")
# ``import a.b`` / ``from a.b import c`` inside a code string handed to ``-c``.
_CODE_IMPORT = re.compile(r"(?:^|[;\n\s])(?:from|import)\s+([A-Za-z_][\w.]*)")
_DOTTED = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)+")
_WORD = r"(?<![\w$.]){}(?![\w$])"


@dataclass(frozen=True)
class OwnCode:
    """What the repository installs: launcher names, ``bin`` targets, Python modules.

    *commands* are console scripts (``[project.scripts]``) and package ``bin``
    names; *bin_files* the files those ``bin`` entries point at; *modules* every
    dotted name a Python file can be imported under, whatever its source root
    (``a/b/c.py`` gives ``c``, ``b.c`` and ``a.b.c``).
    """

    commands: frozenset[str] = frozenset()
    bin_files: frozenset[str] = frozenset()
    modules: frozenset[str] = frozenset()


def own_code(paths: Iterable[str], read: Callable[[str], str | None]) -> OwnCode:
    """:class:`OwnCode` from the repository's production files and its manifests.

    *paths* leaves out tests: a test module run in a child process is still a
    test, not the project's own code. *read* returns a file's text or ``None``;
    only ``pyproject.toml`` and ``package.json`` files are read, so a caller
    holding the bytes passes them.
    """
    from .traverser import _add_script_targets

    paths = list(paths)
    commands: set[str] = set()
    bins: set[str] = set()
    for path in paths:
        name = PurePosixPath(path).name
        if name not in ("pyproject.toml", "package.json") or "node_modules/" in path:
            continue
        text = read(path)
        if not text:
            continue
        if name == "pyproject.toml":
            project = _toml_project(text)
            if project is not None:
                _add_script_targets(project, commands, set())
        else:
            _add_package_bins(path, text, commands, bins)
    return OwnCode(frozenset(commands), frozenset(bins), _module_names(paths))


def _module_names(paths: list[str]) -> frozenset[str]:
    names: set[str] = set()
    for path in paths:
        if not path.endswith(_PY):
            continue
        parts = path[: -len(_PY)].split("/")
        if parts[-1] in ("__init__", "__main__"):
            parts.pop()
        names.update(".".join(parts[i:]) for i in range(len(parts)))
    names.discard("")
    return frozenset(names)


def _toml_project(text: str) -> dict | None:
    try:
        project = tomllib.loads(text).get("project")
    except (tomllib.TOMLDecodeError, ValueError):
        return None
    return project if isinstance(project, dict) else None


def _add_package_bins(path: str, text: str, commands: set[str], bins: set[str]) -> None:
    """One ``package.json``'s ``bin`` names and the repo paths they point at."""
    try:
        manifest = json.loads(text)
    except ValueError:
        return
    if not isinstance(manifest, dict):
        return
    entries = manifest.get("bin")
    if isinstance(entries, str) and isinstance(manifest.get("name"), str):
        entries = {manifest["name"].rsplit("/", 1)[-1]: entries}
    if not isinstance(entries, dict):
        return
    root = PurePosixPath(path).parent
    for command, target in entries.items():
        if isinstance(command, str) and isinstance(target, str):
            commands.add(command)
            bins.add(str(root / target.removeprefix("./")).removeprefix("./"))


def always_run_reason(path: str, text: str, own: OwnCode) -> str | None:
    """Why the test at *path* must run with every subset, or ``None``."""
    if path.endswith(_PY):
        lang = _PY_LANG
    elif path.endswith(_JS):
        lang = _JS_LANG
    else:
        return None
    if _walks_source(text, lang):
        return "it lists and reads files under a source directory"
    if command := _runs_own_code(text, lang, own, python=path.endswith(_PY)):
        return f"it runs {command} in a child process"
    return None


def _walks_source(text: str, lang: _Lang) -> bool:
    if not (_has(text, lang.walk_tokens) and _has(text, lang.read_tokens)):
        return False
    if not (lang.walk.search(text) and lang.read.search(text)):
        return False
    lines = text.splitlines()
    rooted = _Rooted(lang, _bindings(lines, lang))
    funcs: set[str] = set()
    for i, line in enumerate(lines):
        if not (_has(line, lang.walk_tokens) and lang.walk.search(line)):
            continue
        if "ls-files" in line or rooted.holds(_expression(line, lines, i + 1)):
            return True
        if func := _enclosing_function(lines, i, lang):
            funcs.add(func)
    return any(_called_rooted(lines, func, rooted, lang) for func in funcs)


def _has(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


def _bindings(lines: list[str], lang: _Lang) -> dict[str, str]:
    """``{name: the expression it is assigned or looped from}`` (with two more lines)."""
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        match = lang.assign.match(line) or lang.loop.match(line)
        if match and not _MODULE_LOAD.search(line):
            expr = _expression(match.group(2), lines, i + 1)
            out[match.group(1)] = f"{out[match.group(1)]} {expr}" if match.group(1) in out else expr
    return out


def _expression(head: str, lines: list[str], nxt: int, cap: int = 6) -> str:
    """*head* plus the lines that close its brackets or continue it (``? a : b``)."""
    parts = [head]
    depth = _depth(head)
    while (
        nxt < len(lines)
        and len(parts) <= cap
        and (depth > 0 or lines[nxt].lstrip().startswith(_CONTINUATION))
    ):
        parts.append(lines[nxt])
        depth += _depth(lines[nxt])
        nxt += 1
    return " ".join(parts)


_CONTINUATION = ("?", ":", ".", "|", "&", "+")


def _depth(text: str) -> int:
    return sum(text.count(c) for c in "([{") - sum(text.count(c) for c in ")]}")


class _Rooted:
    """Names bound to an anchored expression, found once to a fixed point."""

    def __init__(self, lang: _Lang, bindings: dict[str, str]) -> None:
        self._anchor = lang.anchor
        self._names: re.Pattern[str] | None = None
        names: set[str] = set()
        pending = {n: e for n, e in bindings.items() if not _LOCAL_ROOT.search(e)}
        while fresh := {n for n, e in pending.items() if self.holds(e)}:
            names |= fresh
            pending = {n: e for n, e in pending.items() if n not in fresh}
            self._names = _words(names)

    def holds(self, expr: str) -> bool:
        if _LOCAL_ROOT.search(expr):
            return False
        return bool(self._anchor.search(expr) or (self._names and self._names.search(expr)))


def _words(names: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    return re.compile(_WORD.format(f"(?:{alternation})"))


def _enclosing_function(lines: list[str], at: int, lang: _Lang) -> str | None:
    for line in reversed(lines[: at + 1]):
        if match := lang.func.match(line):
            return next(g for g in match.groups() if g)
    return None


def _called_rooted(lines: list[str], func: str, rooted: _Rooted, lang: _Lang) -> bool:
    call = re.compile(_WORD.format(re.escape(func)) + r"\s*\(")
    return any(
        call.search(line) and not lang.func.match(line) and rooted.holds(_expression(line, lines, i + 1))
        for i, line in enumerate(lines)
    )


def _runs_own_code(text: str, lang: _Lang, own: OwnCode, *, python: bool) -> str | None:
    """The own command, module or file a child process in *text* is started with."""
    if not _has(text, lang.spawn_tokens if python else _JS_PROCESS_TOKENS):
        return None
    lines: list[str] | None = None
    for match in lang.spawn.finditer(text):
        if lines is None:
            lines = text.splitlines()
            bindings, bodies = _bindings(lines, lang), _function_bodies(lines, lang)
        region = _call_region(text, match.end())
        # One step back through names and local helpers: ``run(cmd)`` with
        # ``cmd = [sys.executable, "-m", ...]`` or ``cmd = _command()``.
        for _ in range(2):
            refs = set(_IDENT.findall(region))
            region += " ".join(
                [*(bindings[r] for r in refs & bindings.keys()),
                 *(bodies[r] for r in refs & bodies.keys())]
            )
        if hit := _own_reference(region, own, python=python):
            return hit
    return None


def _call_region(text: str, start: int, cap: int = 600) -> str:
    """The text of a call's arguments: up to its closing bracket, at most *cap* characters."""
    depth = 0
    end = min(len(text), start + cap)
    for i in range(start, end):
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
            if depth <= 0:
                return text[start : i + 1]
    return text[start:end]


def _function_bodies(lines: list[str], lang: _Lang, cap: int = 30) -> dict[str, str]:
    out: dict[str, str] = {}
    for i, line in enumerate(lines):
        if match := lang.func.match(line):
            out[next(g for g in match.groups() if g)] = " ".join(lines[i : i + cap])
    return out


def _own_reference(region: str, own: OwnCode, *, python: bool) -> str | None:
    """The first own command, ``bin`` file or module the strings in *region* name."""
    if own.commands:
        for match in _LEADING_STRING.finditer(region):
            if (head := match.group(2).strip().split(" ", 1)[0]) in own.commands:
                return f"the project's own command `{head}`"
    values = [m.group(2).strip() for m in _STRING.finditer(region)]
    dash_m, dash_c = "-m" in values, "-c" in values
    for value in values:
        head = value.split(" ", 1)[0]
        if not python:
            if (bin_file := _bin_file(head, own)) is not None:
                return f"the project's own bin `{bin_file}`"
            continue
        if dash_m and _DOTTED.fullmatch(value) and value in own.modules:
            return f"`python -m {value}`"
        if dash_c:
            for module in _CODE_IMPORT.findall(value):
                if "." in module and module in own.modules:
                    return f"code importing `{module}`"
    return None


def _bin_file(value: str, own: OwnCode) -> str | None:
    value = value.removeprefix("./")
    if not value or ("/" not in value and "." not in value):
        return None
    return next((b for b in own.bin_files if b == value or b.endswith(f"/{value}")), None)


def stamp_always_run(
    graph: Any, parsed_files: Mapping[str, Any], source_map: dict[str, bytes] | None
) -> int:
    """Set ``always_run_reason`` on each runnable test file's graph node; returns how many.

    Reads the bytes the pipeline already holds (*source_map*), falling back to
    disk only for a file the map lacks, and only for test files and manifests.
    """
    from ..analysis.test_selection import is_runnable_test
    from .source_text import source_text

    def read(path: str) -> str | None:
        info = getattr(parsed_files.get(path), "file_info", None)
        return source_text(path, getattr(info, "abs_path", path), source_map)

    tests = [
        p
        for p, parsed in parsed_files.items()
        if parsed.file_info.is_test and p.endswith((_PY, *_JS)) and is_runnable_test(p)
    ]
    if not tests:
        return 0
    own = own_code([p for p, pf in parsed_files.items() if not pf.file_info.is_test], read)
    stamped = 0
    for path in tests:
        node = graph.nodes.get(path)
        text = read(path)
        reason = always_run_reason(path, text, own) if node is not None and text else None
        if node is not None:
            node["always_run_reason"] = reason
            stamped += reason is not None
    return stamped
