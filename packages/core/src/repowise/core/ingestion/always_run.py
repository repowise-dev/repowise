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
subset. A walk only counts when its root is the source tree: the working
directory (``process.cwd()``), a module's ``__file__``, or the test's own
location (``__file__``, ``import.meta.url``, ``__dirname``) climbed out of the
test directory (``parents[2]``, ``".."``). The test's own directory counts only
when it is not a test directory (a test beside the code it checks). The root
may arrive directly, through a variable, or through a local function called
with one; a root naming fixtures, test data, snapshots or a temporary
directory never counts. The file read must sit in the walk's own function or
block, or in a block naming the walk's function or variable. Ceiling: the
check is textual and per file, so a walk done by a shared helper the test
calls (``list_tracked_files()``) is not seen; detecting walks in helpers and
marking their importers is the upgrade path. Python, JavaScript and
TypeScript only.
"""

from __future__ import annotations

import json
import re
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
    # A root that is the source tree wherever the test sits.
    anchor: re.Pattern[str]
    # The test's own file or directory, and what climbs out of it from the
    # file (``climb_file``) or from a name already bound to its directory.
    local: re.Pattern[str]
    climb_file: re.Pattern[str]
    climb_dir: re.Pattern[str]
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
    anchor=re.compile(r"\w\.__file__\b|\bPath\.cwd\(\)|\bos\.getcwd\(\)"),
    local=re.compile(r"(?<![\w.])__file__\b"),
    climb_file=re.compile(r"parents\[|\.parent\s*\.parent\b|[\"']\.\.[\"'/\\]|dirname\(.*dirname\("),
    climb_dir=re.compile(r"\.parent\b|parents\[|[\"']\.\.[\"'/\\]|dirname\("),
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
    anchor=re.compile(r"\bprocess\.cwd\(\)"),
    local=re.compile(r"\bimport\.meta\.(?:url|dirname|filename)\b|\b__dirname\b|\b__filename\b"),
    climb_file=re.compile(r"[\"'`]\.\.[\"'`/\\]|dirname\(.*dirname\("),
    climb_dir=re.compile(r"[\"'`]\.\.[\"'`/\\]|dirname\("),
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


def own_code(
    paths: Iterable[str],
    read: Callable[[str], str | None],
    console_scripts: Iterable[str] = (),
) -> OwnCode:
    """:class:`OwnCode` from the repository's production files and its manifests.

    *paths* leaves out tests: a test module run in a child process is still a
    test, not the project's own code. *console_scripts* are the launcher names
    the traverser already read from every ``pyproject.toml``
    (``FileTraverser.console_script_names``), so those files are not read
    again. *read* returns a file's text or ``None``; only ``package.json``
    files are read, so a caller holding the bytes passes them.
    """
    paths = list(paths)
    commands: set[str] = set(console_scripts)
    bins: set[str] = set()
    for path in paths:
        if PurePosixPath(path).name != "package.json" or "node_modules/" in path:
            continue
        if text := read(path):
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


def is_judged_test(path: str) -> bool:
    """A runnable test in a language :func:`always_run_reason` reads."""
    from ..analysis.test_selection import is_runnable_test

    return path.endswith((_PY, *_JS)) and is_runnable_test(path)


def _beside_code(path: str) -> bool:
    """Whether the test sits in a code directory, not a test directory."""
    from ..test_paths import is_test_related_path

    return not is_test_related_path(str(PurePosixPath(path).parent / "__init__.py"))


def always_run_reason(path: str, text: str, own: OwnCode) -> str | None:
    """Why the test at *path* must run with every subset, or ``None``."""
    if path.endswith(_PY):
        lang = _PY_LANG
    elif path.endswith(_JS):
        lang = _JS_LANG
    else:
        return None
    if _walks_source(text, lang, local_is_source=_beside_code(path)):
        return "it lists and reads files under a source directory"
    if command := _runs_own_code(text, lang, own, python=path.endswith(_PY)):
        return f"it runs {command} in a child process"
    return None


def _walks_source(text: str, lang: _Lang, *, local_is_source: bool = False) -> bool:
    if not (_has(text, lang.walk_tokens) and _has(text, lang.read_tokens)):
        return False
    if not (lang.walk.search(text) and lang.read.search(text)):
        return False
    lines = text.splitlines()
    bindings = _bindings(lines, lang)
    rooted = _Rooted(lang, bindings, local_is_source)
    scopes = _Scopes(lines, lang)
    for i, line in enumerate(lines):
        if not (_has(line, lang.walk_tokens) and (walk := lang.walk.search(line))):
            continue
        if any(m.start() < walk.start() < m.end() for m in _STRING.finditer(line)):
            continue  # code quoted inside a string
        func = scopes.function(i)
        anchored = rooted.holds(_expression(line, lines, i + 1)) or bool(
            func and _called_rooted(lines, func[2], rooted, lang)
        )
        if anchored and _reads_near(lines, i, func, scopes, lang):
            return True
    return False


def _reads_near(
    lines: list[str], at: int, func: tuple[int, int, str] | None, scopes: _Scopes, lang: _Lang
) -> bool:
    """A file read in the walk's own scope, or in a block naming its function or variable."""
    own = (func[0], func[1]) if func else scopes.block(at)
    if _reads_in(lines, own, scopes, lang):
        return True
    names = {func[2]} if func else set()
    if match := lang.assign.match(lines[at]):
        names.add(match.group(1))
    if not names:
        return False
    mention = _words(names)
    blocks = {scopes.block(i) for i, line in enumerate(lines) if i != at and mention.search(line)}
    return any(_reads_in(lines, block, scopes, lang) for block in blocks)


def _reads_in(lines: list[str], span: tuple[int, int], scopes: _Scopes, lang: _Lang) -> bool:
    """A read in *span*, or in a local function *span* calls (one step)."""
    text = "\n".join(lines[span[0] : span[1]])
    if lang.read.search(text):
        return True
    for name in set(_IDENT.findall(text)):
        body = scopes.body(name)
        if body and body != span and lang.read.search("\n".join(lines[body[0] : body[1]])):
            return True
    return False


class _Scopes:
    """Function spans and top-level blocks, by indentation."""

    def __init__(self, lines: list[str], lang: _Lang) -> None:
        self._funcs: list[tuple[int, int, str]] = []
        for i, line in enumerate(lines):
            if match := lang.func.match(line):
                name = next(g for g in match.groups() if g)
                self._funcs.append((i, _span_end(lines, i), name))
        self._block_of: list[int] = []
        self._starts: list[int] = []
        decorated = False  # a decorator and the definition under it are one block
        for i, line in enumerate(lines):
            top = line[:1] not in ("", " ", "\t", ")", "]", "}")
            if (top and not decorated) or not self._starts:
                self._starts.append(i)
            if top:
                decorated = line.startswith("@")
            self._block_of.append(len(self._starts) - 1)
        self._starts.append(len(lines))

    def body(self, name: str) -> tuple[int, int] | None:
        return next(((a, b) for a, b, n in self._funcs if n == name), None)

    def function(self, at: int) -> tuple[int, int, str] | None:
        """The innermost function whose span holds line *at*."""
        for k in range(len(self._funcs) - 1, -1, -1):
            start, end, name = self._funcs[k]
            if start <= at < end:
                return start, end, name
        return None

    def block(self, at: int) -> tuple[int, int]:
        b = self._block_of[at]
        return self._starts[b], self._starts[b + 1]


def _span_end(lines: list[str], start: int) -> int:
    """The line after a definition's body: the next one indented no deeper than it."""
    indent = _indent(lines[start])
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped and _indent(lines[j]) <= indent:
            # A closing bracket at the definition's own indent still belongs to it.
            return j + 1 if stripped[0] in ")]}" else j
    return len(lines)


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _has(text: str, tokens: tuple[str, ...]) -> bool:
    return any(token in text for token in tokens)


# Assignments kept per name: the last few, so a name rebound a thousand times
# in one file costs a fixed amount per lookup.
_BINDINGS_PER_NAME = 3


def _bindings(lines: list[str], lang: _Lang) -> dict[str, str]:
    """``{name: the last expressions it is assigned or looped from}``."""
    out: dict[str, list[str]] = {}
    for i, line in enumerate(lines):
        match = lang.assign.match(line) or lang.loop.match(line)
        if match and not _MODULE_LOAD.search(line):
            kept = out.setdefault(match.group(1), [])
            kept.append(_expression(match.group(2), lines, i + 1))
            del kept[:-_BINDINGS_PER_NAME]
    return {name: " ".join(exprs) for name, exprs in out.items()}


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
    """Names bound to the source tree, and names bound to the test's own directory.

    Both are found once, to a fixed point. A name of the second kind counts as
    the source tree only where the expression climbs out of it, or when the
    test sits beside the code (*local_is_source*).
    """

    def __init__(self, lang: _Lang, bindings: dict[str, str], local_is_source: bool) -> None:
        self._lang = lang
        self._local_is_source = local_is_source
        self._names: re.Pattern[str] | None = None
        self._locals: re.Pattern[str] | None = None
        rooted: set[str] = set()
        local: set[str] = set()
        pending = {n: e for n, e in bindings.items() if not _LOCAL_ROOT.search(e)}
        while True:
            fresh = {n for n, e in pending.items() if self.holds(e)}
            fresh_local = {n for n, e in pending.items() if n not in fresh and self._is_local(e)}
            if not (fresh or fresh_local):
                break
            rooted |= fresh
            local |= fresh_local
            pending = {n: e for n, e in pending.items() if n not in fresh | fresh_local}
            self._names = _words(rooted) if rooted else None
            self._locals = _words(local) if local else None

    def holds(self, expr: str) -> bool:
        if _LOCAL_ROOT.search(expr):
            return False
        lang = self._lang
        if lang.anchor.search(expr) or (self._names and self._names.search(expr)):
            return True
        if lang.local.search(expr) and (self._local_is_source or lang.climb_file.search(expr)):
            return True
        return bool(
            self._locals
            and self._locals.search(expr)
            and (self._local_is_source or lang.climb_dir.search(expr))
        )

    def _is_local(self, expr: str) -> bool:
        return bool(self._lang.local.search(expr) or (self._locals and self._locals.search(expr)))


def _words(names: Iterable[str]) -> re.Pattern[str]:
    alternation = "|".join(sorted(map(re.escape, names), key=len, reverse=True))
    return re.compile(_WORD.format(f"(?:{alternation})"))


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
    seen: set[str] = set()
    for match in lang.spawn.finditer(text):
        if lines is None:
            lines = text.splitlines()
            known = {**_function_bodies(lines, lang), **_bindings(lines, lang)}
        call = _call_region(text, match.end())
        if call in seen:  # ``run(cmd)`` a thousand times reads ``cmd`` once
            continue
        seen.add(call)
        if hit := _own_reference(_expand(call, known), own, python=python):
            return hit
    return None


# Most text one spawn call is expanded to: its arguments, then the names and
# local helpers they mention, two steps back.
_REGION_CAP = 4096


def _expand(region: str, known: Mapping[str, str]) -> str:
    """*region* plus what its names are bound to: ``run(cmd)`` with ``cmd = [...]``."""
    expanded: set[str] = set()
    for _ in range(2):
        refs = (set(_IDENT.findall(region)) & known.keys()) - expanded
        expanded |= refs
        region = " ".join([region, *(known[r] for r in sorted(refs))])[:_REGION_CAP]
    return region


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
    """The ``bin`` file *value* names by its repo path, or by a path of two or more parts.

    A bare file name only matches a ``bin`` at the repository root: ``cli.js``
    is not every package's ``bin/cli.js``.
    """
    value = value.removeprefix("./")
    if not value:
        return None
    return next(
        (b for b in own.bin_files if b == value or ("/" in value and b.endswith(f"/{value}"))),
        None,
    )


def stamp_always_run(
    graph: Any,
    parsed_files: Mapping[str, Any],
    source_map: dict[str, bytes] | None,
    console_scripts: Iterable[str] = (),
) -> int:
    """Set ``always_run_reason`` on each judged test's graph node; returns how many have one.

    A test read and found ordinary gets ``""``; ``None`` (never stamped) is
    left for a test that could not be read, which selection treats as not yet
    scanned. Reads the bytes the pipeline already holds (*source_map*),
    falling back to disk only for a file the map lacks, and only for test
    files and manifests.
    """
    from .source_text import source_text

    def read(path: str) -> str | None:
        info = getattr(parsed_files.get(path), "file_info", None)
        return source_text(path, getattr(info, "abs_path", path), source_map)

    tests = [
        p
        for p, parsed in parsed_files.items()
        if parsed.file_info.is_test and is_judged_test(p)
    ]
    if not tests:
        return 0
    production = [p for p, pf in parsed_files.items() if not pf.file_info.is_test]
    own = own_code(production, read, console_scripts)
    stamped = 0
    for path in tests:
        node = graph.nodes.get(path)
        if node is None or (text := read(path)) is None:
            continue
        node["always_run_reason"] = always_run_reason(path, text, own) or ""
        stamped += bool(node["always_run_reason"])
    return stamped
