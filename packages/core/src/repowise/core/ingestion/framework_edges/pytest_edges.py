"""pytest conftest convention edges.

Two conventions, both invisible to a static import graph: a ``conftest.py`` is
imported by collection, not by any statement, and a test's parameter
names are fixture requests resolved at run time.

Every way a test or fixture asks for a fixture by name becomes a
``framework_binds`` edge stamped :data:`FIXTURE_HINT`: a test's parameters, a
fixture's own parameters, ``@pytest.mark.usefixtures(...)`` on a test or its
class, a module's ``pytestmark`` usefixtures, and ``request.getfixturevalue``
with a literal name. Test selection reads these edges to find every test that
uses a fixture, and trusts them only when the stamp shows all of these forms
were recorded. A request no edge can record (a computed name, a test inherited
from a base class declared elsewhere, a class-level ``pytestmark``, marks inside
``pytest.param``, a parametrize call in a hook, any other code use of a
request name such as an aliased mark, ``add_marker`` or ``fixturenames``, a
config ``usefixtures`` or ``python_functions``, a helper asking for a fixture,
a file whose text is not available) is stamped
:data:`UNRECORDED_HINT` on the conftest edges it could reach, and selection
keeps every test under those conftests.
"""

from __future__ import annotations

import io
import re
import tokenize
from collections import Counter
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ...pytest_roots import DEFAULT_PYTHON_FUNCTIONS, _words, pytest_options
from ..resolvers import ResolverContext
from ..source_text import decode_source, source_text
from ..type_names import strip_type_arguments
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
)

if TYPE_CHECKING:
    import networkx as nx

    from ...pytest_roots import PytestRoots

# Matched at the attribute tail rather than on `pytest.`, so `@my.fixture` and
# `@pytest_asyncio.fixture` are both accepted: pytest resolves the decorator by
# identity, not by module path.
_FIXTURE_DECORATOR_RE = re.compile(r"^@(?:[\w.]+\.)?fixture\b")
_PARAMETRIZE_RE = re.compile(r"^@(?:[\w.]+\.)?parametrize\b")
_QUOTED_RE = re.compile(r"""["']([^"']*)["']""")
# `@pytest.fixture(name="app")` registers `fixture_app` as `app`. Read as a
# top-level keyword only: a `name=` nested in a params list is not the
# fixture's name.
_NAME_KWARG_ARG_RE = re.compile(r"""^name\s*=\s*["']([^"']+)["']$""")
_USEFIXTURES_RE = re.compile(r"^@(?:[\w.]+\.)?usefixtures\b")
# A module-level `pytestmark = pytest.mark.usefixtures("a")` (or a list of
# marks): the statement up to the next top-level line.
_PYTESTMARK_RE = re.compile(r"^pytestmark\b[^\n]*(?:\n[ \t)\]][^\n]*)*", re.MULTILINE)
_USEFIXTURES_CALL_RE = re.compile(r"usefixtures\(([^)]*)\)")
# The name is captured only when it is a literal alone in the call.
_GETFIXTUREVALUE_RE = re.compile(r"""getfixturevalue\(\s*(?:["'](\w+)["']\s*(?=\)))?""")
# Names through which code can ask for a fixture. A use of one that is not a
# form this module records (a literal decorator, a module ``pytestmark``, a
# literal ``getfixturevalue`` call in a test or fixture) hides a request.
_REQUEST_NAMES = frozenset(
    {"usefixtures", "getfixturevalue", "add_marker", "applymarker", "fixturenames",
     "lazy_fixture", "fixture_ref"}
)
_AUTOUSE_KWARG_RE = re.compile(r"^autouse\s*=\s*(.+)$")
_INDIRECT_KWARG_RE = re.compile(r"^indirect\s*=")
_LITERAL_RE = re.compile(r"""(["'])\w+\1""")
_LITERAL_LIST_RE = re.compile(r"""[\[(]\s*(?:(["'])\w+\1\s*,?\s*)*[\])]""")
# A `pytestmark` inside a class body, and a parametrize called instead of
# applied as a decorator (`metafunc.parametrize(..., indirect=True)`).
_CLASS_PYTESTMARK_RE = re.compile(r"^[ \t]+pytestmark\b", re.MULTILINE)
_CALL_PARAMETRIZE_RE = re.compile(r"^[ \t]*[^@\s#][^\n]*\bparametrize\(", re.MULTILINE)
# Bases a test class may name without carrying fixture requests of its own.
_PLAIN_BASES = frozenset(
    {"object", "ABC", "abc.ABC", "TestCase", "unittest.TestCase", "IsolatedAsyncioTestCase",
     "unittest.IsolatedAsyncioTestCase"}
)

# Stamped on every fixture-request edge, so a reader can tell an index that
# records all request forms from one built before they were recorded.
FIXTURE_HINT = "pytest_fixture"
# Stamped instead of the conftest hint on a test -> conftest edge when the test
# (or the conftest) may request that conftest's fixtures in a way no edge records.
UNRECORDED_HINT = "pytest_conftest_unrecorded"

# pytest's own `python_files` default, and deliberately not the shared
# `is_test_path`. A fixture is injected only into a file pytest actually
# collects, so this has to answer "does pytest run this?" — a narrower question
# than "is this test-related code", which also claims `conftest.py`, a
# `tests/helpers.py` and every non-Python spec layout. Widening it would inject
# fixture parameters into files that are never collected.
#
# Used only when the traverser's pytest roots are not known; with them, the
# configured `python_files` decide.
_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$")

# pytest collects methods only from classes matching `python_classes`, so a
# `class Harness` with a `test_connection` method is never run and its
# parameters are ordinary arguments its callers pass. The setting is read
# rather than assumed: celery configures `test_*`, and assuming the default
# refused 346 of its 359 bindings.
_DEFAULT_TEST_CLASS_GLOBS = ("Test*",)


def _test_class_globs(repo_path: Path | None) -> tuple[str, ...]:
    """The ``python_classes`` globs this project collects test classes by.

    Read from the pytest table or section only (see :func:`pytest_options`):
    a ``python_classes`` line under another tool's section is that tool's, not
    pytest's. A TOML list is accepted, as pytest does in ``pyproject.toml``;
    a file that does not parse, or a pytest section without the key, falls
    through to the next file and finally to the default.
    """
    if repo_path is None:
        return _DEFAULT_TEST_CLASS_GLOBS
    for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        try:
            text = (repo_path / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        options = pytest_options(name, text)
        if not options:
            continue
        value = options.get("python_classes")
        if isinstance(value, str):
            value = value.strip().strip("\"'")
        globs = _words(value)
        if globs:
            return globs
    return _DEFAULT_TEST_CLASS_GLOBS


def _call_arguments(text: str) -> list[str]:
    """Top-level arguments of the first call in *text*, unsplit by nesting.

    A plain `split(",")` cannot do this and neither can one regex: a default
    value, a subscripted annotation and a nested `dict(...)` all contain the
    characters the split keys on. Depth counting with string awareness is the
    smallest thing that reads `def t(a, cb: Callable[[int], str], o=(1, 2))`
    correctly.
    """
    start = text.find("(")
    if start == -1:
        return []
    depth = 0
    quote: str | None = None
    escaped = False
    comment = False
    args: list[str] = []
    current: list[str] = []
    for ch in text[start:]:
        if comment:
            # A signature spanning several lines carries its comments, and an
            # apostrophe or a stray bracket in one used to swallow the rest of
            # the parameter list.
            if ch == "\n":
                comment = False
            continue
        if quote:
            current.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch == "#":
            comment = True
            continue
        if ch in "\"'":
            quote = ch
            current.append(ch)
            continue
        if ch in "([{":
            depth += 1
            if depth == 1:
                continue
        elif ch in ")]}":
            depth -= 1
            if depth == 0:
                args.append("".join(current))
                return [a.strip() for a in args if a.strip()]
        elif ch == "," and depth == 1:
            args.append("".join(current))
            current = []
            continue
        current.append(ch)
    # Unbalanced — a truncated signature. Return nothing rather than a guess.
    return []


def _has_default(param: str) -> bool:
    """Whether a single parameter carries a default value."""
    depth = 0
    quote: str | None = None
    escaped = False
    for i, ch in enumerate(param):
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
        elif ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif (
            ch == "="
            and depth == 0
            # A comparison or walrus is not a default.
            and param[i - 1 : i] not in ("=", "<", ">", "!", ":")
            and param[i + 1 : i + 2] != "="
        ):
            return True
    return False


def _fixture_scopes(parsed: Any, class_name: str | None) -> list[str | None]:
    """The class scopes a test in *class_name* can see a fixture through.

    A subclass sees its base's fixtures, which is the commonest class-scoped
    arrangement there is -- scoping the lookup to the declaring class alone
    fixes a rare wrong edge by introducing a frequent missing one. Walked from
    the file's own heritage, so a base in another module is out of reach and
    stays unclaimed.
    """
    if class_name is None:
        return [None]
    parents: dict[str, list[str]] = {}
    for rel in parsed.heritage:
        parents.setdefault(rel.child_name, []).append(
            strip_type_arguments(rel.parent_name).strip()
        )

    scopes: list[str | None] = []
    seen: set[str] = set()
    queue = [class_name]
    while queue:
        current = queue.pop(0)
        if current in seen:
            continue
        seen.add(current)
        scopes.append(current)
        queue.extend(parents.get(current, ()))
    scopes.append(None)
    return scopes


# Stamped on the test -> conftest edge. pytest loads a conftest for every test
# under its directory, so the edge is real for fixture visibility, but no
# import statement makes it: consumers that mean "imports" (the import-edge
# oracle, density metrics) must be able to tell it from one.
CONFTEST_HINT = "pytest_conftest"


def _add_conftest_edges(
    graph: nx.DiGraph, path_set: set[str], roots: PytestRoots | None = None
) -> int:
    """Collected tests and nested conftests -> each conftest.py at or above them.

    A helper beside the tests is not collected and sees no fixture, so it gets
    no edge: one would put the helper on the route of every change the conftest
    reaches. Collection is the configured ``python_files`` (*roots*, read once by
    the traverser). Without *roots*, collection is unknown and every test-tree
    file keeps its edge.
    """
    count = 0
    conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

    for conf in conftest_paths:
        conf_dir = Path(conf).parent.as_posix()
        prefix = f"{conf_dir}/" if conf_dir != "." else ""
        for p in path_set:
            if p == conf:
                continue
            if roots is not None and not (
                Path(p).name == "conftest.py" or (p.endswith(".py") and roots.may_collect_name(p))
            ):
                continue
            node = graph.nodes.get(p, {})
            if not node.get("is_test", False):
                continue
            if (p.startswith(prefix) or (prefix == "" and "/" not in p)) and _add_edge_if_new(
                graph, p, conf
            ):
                graph[p][conf]["hint_source"] = CONFTEST_HINT
                count += 1
    return count


def fixture_declaration(name: str, decorators: Iterable[str]) -> tuple[str, bool] | None:
    """``(registered name, autouse)`` when *decorators* declare a pytest fixture.

    ``name=`` and ``autouse=`` are read as top-level keywords only:
    ``@pytest.fixture(params=[dict(name="alice")])`` registers the fixture
    under its own name. An ``autouse`` that is not the literal ``False`` may be
    true, so it counts as autouse.
    """
    for dec in decorators:
        if not _FIXTURE_DECORATOR_RE.match(dec.strip()):
            continue
        registered, autouse = name, False
        for arg in _call_arguments(dec):
            if match := _NAME_KWARG_ARG_RE.match(arg):
                registered = match.group(1)
            elif match := _AUTOUSE_KWARG_RE.match(arg):
                autouse = match.group(1).strip() not in ("False", "0", "None")
        return registered, autouse
    return None


def _declared_fixtures(parsed: Any) -> dict[tuple[str | None, str], str]:
    """``{(owning class or None, fixture name): symbol id}``.

    Keyed by scope as well as by name. A fixture declared inside a test class
    serves that class only, and flattening the two makes every sibling class
    share it, which is a wrong edge whichever way the collision is resolved.
    """
    out: dict[tuple[str | None, str], str] = {}
    for sym in parsed.symbols:
        if sym.kind not in ("function", "method"):
            continue
        if declared := fixture_declaration(sym.name, sym.decorators):
            out.setdefault((sym.parent_name, declared[0]), sym.id)
    return out


@dataclass
class _Requests:
    """What a list of decorators asks for: names it supplies, names it requests."""

    supplied: set[str] = field(default_factory=set)
    requested: list[str] = field(default_factory=list)
    # A request the text does not name (a computed ``indirect`` or usefixtures argument).
    unknown: bool = False


def _argnames(spec: str) -> list[str]:
    return [p.strip() for token in _QUOTED_RE.findall(spec) for p in token.split(",") if p.strip()]


def _decorator_requests(decorators: Iterable[str]) -> _Requests:
    """Supplied argnames, and fixtures requested by ``usefixtures`` or ``indirect`` parametrize."""
    out = _Requests()
    for dec in decorators:
        dec = dec.strip()
        if _USEFIXTURES_RE.match(dec):
            _add_usefixtures(dec, out)
        elif _PARAMETRIZE_RE.match(dec):
            _add_parametrize(dec, out)
    return out


def _add_usefixtures(dec: str, out: _Requests) -> None:
    """A ``usefixtures`` decorator: each literal argument is a request."""
    for arg in _call_arguments(dec):
        if _LITERAL_RE.fullmatch(arg):
            out.requested.append(arg[1:-1])
        else:
            out.unknown = True


def _add_parametrize(dec: str, out: _Requests) -> None:
    """A ``parametrize`` decorator: its argnames are supplied, or requested via ``indirect``."""
    # Only the first argument is the argnames spec: reading the whole
    # decorator makes every parametrize value look like a supplied name.
    args = _call_arguments(dec)
    if not args:
        return
    names = _argnames(args[0])
    indirect = _indirect_argument(args)
    # `indirect` hands the value to the fixture of that name: a request.
    if indirect in (None, "False"):
        out.supplied.update(names)
    elif indirect == "True":
        out.requested.extend(names)
    elif _LITERAL_LIST_RE.fullmatch(indirect):
        via = set(_QUOTED_RE.findall(indirect))
        out.requested.extend(n for n in names if n in via)
        out.supplied.update(n for n in names if n not in via)
    else:
        out.requested.extend(names)
        out.unknown = True


def _indirect_argument(args: list[str]) -> str | None:
    """A parametrize call's ``indirect`` value: the keyword, else the third positional."""
    return next(
        (a.split("=", 1)[1].strip() for a in args[1:] if _INDIRECT_KWARG_RE.match(a)),
        args[2] if len(args) > 2 and "=" not in args[2] else None,
    )


def _requested_fixtures(sym: Any, supplied: set[str] = frozenset()) -> list[str]:
    """The parameter names *sym* asks pytest to inject, minus *supplied* argnames.

    Reads the recorded signature: a second parse of every test file is the cost
    of running inside the build, not beside it.
    """
    names = []
    for raw in _call_arguments(sym.signature or ""):
        # A defaulted parameter is never injected -- pytest skips any argument
        # whose default is not empty. The `=` has to be found at depth zero:
        # `client: Annotated[int, Field(ge=0)]` has no default and is injected.
        if raw.startswith("*") or _has_default(raw):
            continue
        name = raw.split(":")[0].strip()
        if not name or name in ("self", "cls", "/") or name in supplied:
            continue
        names.append(name)
    return names


def _module_usefixtures(text: str) -> tuple[list[str], bool]:
    """Fixture names a module-level ``pytestmark`` applies, and whether any is computed."""
    names: list[str] = []
    unknown = False
    for mark in _PYTESTMARK_RE.findall(text):
        for call in _USEFIXTURES_CALL_RE.findall(mark):
            args = _call_arguments(f"({call})")
            # `usefixtures(*names())` stops the capture early and parses to nothing.
            unknown = unknown or (bool(call.strip()) and not args)
            for arg in args:
                if _LITERAL_RE.fullmatch(arg):
                    names.append(arg[1:-1])
                else:
                    unknown = True
    return names, unknown


def _request_name_uses(text: str) -> Counter[str] | None:
    """How often code (not strings or comments) uses each of :data:`_REQUEST_NAMES`.

    ``None`` when the text does not tokenize, which hides whatever it holds.
    A substring check first, so the lexer runs only on the few files that
    mention one.
    """
    if not any(name in text for name in _REQUEST_NAMES):
        return Counter()
    try:
        tokens = tokenize.generate_tokens(io.StringIO(text).readline)
        return Counter(
            t.string for t in tokens if t.type == tokenize.NAME and t.string in _REQUEST_NAMES
        )
    except (tokenize.TokenError, SyntaxError):
        return None


def _hidden_request(text: str) -> str | None:
    """A request form the edges below cannot record, found in a test file's or conftest's text."""
    if _CLASS_PYTESTMARK_RE.search(text):
        return "a class-level pytestmark"
    if "marks=" in text and "usefixtures" in text:
        return "usefixtures inside pytest.param marks"
    if "indirect" in text and _CALL_PARAMETRIZE_RE.search(text):
        return "a parametrize call outside a decorator"
    return None


def _runtime_requests(text: str, symbols: list[Any]) -> tuple[list[tuple[Any, str]], bool]:
    """``(enclosing function, name)`` per literal ``getfixturevalue``, and whether any is computed."""
    out: list[tuple[Any, str]] = []
    unknown = False
    functions = [s for s in symbols if s.kind in ("function", "method")]
    for match in _GETFIXTUREVALUE_RE.finditer(text):
        name = match.group(1)
        line = text.count("\n", 0, match.start()) + 1
        enclosing = [s for s in functions if s.start_line <= line <= s.end_line]
        if name is None or not enclosing:
            unknown = True
            continue
        out.append((max(enclosing, key=lambda s: s.start_line), name))
    return out, unknown


def _add_fixture_injection_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    repo_path: Path | None = None,
    read: Callable[[str], str | None] | None = None,
    roots: PytestRoots | None = None,
    helper_text: Callable[[str], str | None] | None = None,
) -> int:
    """Link each test and fixture to every fixture it asks for by name.

    Scope follows pytest's own rule, innermost first: the requester's own class,
    then its module, then the nearest ``conftest.py`` at or above it. Nothing
    else is searched, so a plugin-provided fixture stays unclaimed instead of
    being bound to a same-named local one. A fixture asking for its own name
    (an override) gets the next one out. *read* returns a file's text, for the
    forms only the text shows; *roots* say which files pytest collects;
    *helper_text* returns the text of any other Python file from the bytes
    ingestion already holds (``None`` when it does not hold it).

    A request no edge can record is stamped on the requester's conftest edges
    instead (:data:`UNRECORDED_HINT`, see :func:`_stamp_unrecorded`).
    """
    # Only a conftest's module-level fixtures are visible to other files; one
    # declared inside a class there serves that class alone.
    conftests: dict[str, dict[str, str]] = {}
    for path, parsed in parsed_files.items():
        if Path(path).name != "conftest.py":
            continue
        declared = {
            name: sid for (owner, name), sid in _declared_fixtures(parsed).items()
            if owner is None
        }
        if declared:
            conftests[Path(path).parent.as_posix()] = declared

    class_globs = _test_class_globs(repo_path)
    unrecorded: set[str] = set()
    everywhere = False
    count = 0
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python":
            continue
        is_conftest = Path(path).name == "conftest.py"
        collected = roots.may_collect_name(path) if roots else bool(_TEST_FILE_RE.search(path))
        if not (is_conftest or collected):
            # Code a test calls may ask for a fixture by name, for whichever test
            # calls it. Read from the bytes ingestion holds, never from disk: a
            # file missing there is not known to be free of requests.
            text = helper_text(path) if helper_text is not None else None
            uses = None if text is None else _request_name_uses(text)
            everywhere = everywhere or uses is None or bool(uses)
            continue
        added, hidden, anywhere = _link_file(graph, path, parsed, conftests, class_globs, read)
        count += added
        everywhere = everywhere or anywhere
        if hidden:
            unrecorded.add(path)
    hidden_dirs = roots.hidden_request_dirs() if roots else []
    _stamp_unrecorded(graph, unrecorded, hidden_dirs, everywhere=everywhere)
    return count


def _stamp_unrecorded(
    graph: nx.DiGraph, files: set[str], dirs: list[str], *, everywhere: bool
) -> None:
    """Mark the conftest edges whose fixtures may be requested in a way no edge records.

    A file's requests resolve among the conftests above it, so its edges to them
    are marked; a conftest's own unrecorded requests mark the edges into it too.
    *dirs* hold a pytest config that requests fixtures for every test under it.
    """
    for conf in [n for n in graph.nodes if str(n).endswith("conftest.py")]:
        for source in list(graph.predecessors(conf)):
            data = graph[source][conf]
            if data.get("hint_source") != CONFTEST_HINT:
                continue
            under_dir = any(d == "" or str(source).startswith(f"{d}/") for d in dirs)
            if everywhere or under_dir or source in files or conf in files:
                data["hint_source"] = UNRECORDED_HINT


def _link_file(
    graph: nx.DiGraph,
    path: str,
    parsed: Any,
    conftests: dict[str, dict[str, str]],
    class_globs: tuple[str, ...],
    read: Callable[[str], str | None] | None,
) -> tuple[int, bool, bool]:
    """The fixture-request edges leaving one test module or conftest, and whether some cannot be.

    The second value is true when the file asks for a fixture in a way no edge
    records: a computed name, a test inherited from a base class this file does
    not declare, or a request form only the text shows (:func:`_hidden_request`).
    The third is true when a helper asks for one, for whichever test calls it.
    """
    own = _declared_fixtures(parsed)
    fixture_ids = set(own.values())
    linker = _FileLinker(graph, parsed, own, conftests, _conftest_chain(path, conftests))
    collected, inherited, marks, class_hidden = _test_classes(parsed, class_globs)
    # Decorator uses of `usefixtures` this file records (the computed ones are
    # flagged by `_decorator_requests` itself).
    recorded = Counter(
        "usefixtures"
        for sym in parsed.symbols
        for dec in sym.decorators
        if _USEFIXTURES_RE.match(dec.strip())
    )
    is_conftest = Path(path).name == "conftest.py"
    tests, symbol_hidden = _link_symbols(
        linker, fixture_ids, None if is_conftest else collected | inherited, marks
    )

    text = read(path) if read is not None else None
    if text is None:
        # Unread, the file may hold any request form.
        return linker.count, True, False
    text_hidden, everywhere = _link_text(linker, text, tests, fixture_ids, recorded)
    return linker.count, class_hidden or symbol_hidden or text_hidden, everywhere


def _conftest_chain(path: str, conftests: dict[str, dict[str, str]]) -> list[str]:
    """Conftest directories above *path*, nearest first.

    The deepest conftest directory that is a prefix of this file's directory
    shadows the ones above it, as pytest does.
    """
    return sorted(
        (d for d in conftests if path.startswith(f"{d}/") or d == "."),
        key=len,
        reverse=True,
    )


@dataclass
class _FileLinker:
    """One file's fixture lookup, and the count of edges it has added."""

    graph: nx.DiGraph
    parsed: Any
    own: dict[tuple[str | None, str], str]
    conftests: dict[str, dict[str, str]]
    chain: list[str]
    count: int = 0

    def link(self, sym: Any, names: Iterable[str]) -> None:
        """Edge *sym* to the fixture each of *names* resolves to: own scopes, then conftests."""
        scopes = _fixture_scopes(self.parsed, sym.parent_name)
        for name in names:
            hits = [self.own[(s, name)] for s in scopes if (s, name) in self.own]
            hits += [self.conftests[d][name] for d in self.chain if name in self.conftests[d]]
            target = next((h for h in hits if h != sym.id), None)
            if target and add_symbol_edge(self.graph, sym.id, target):
                self.graph[sym.id][target]["hint_source"] = FIXTURE_HINT
                self.count += 1


def _test_classes(
    parsed: Any, class_globs: tuple[str, ...]
) -> tuple[set[str], set[str], dict[str, _Requests], bool]:
    """``(collected, inherited, marks, hidden)`` for the file's classes.

    A collected class runs the tests of every base it names, with their marks;
    a base this file does not declare, or a computed mark, hides a request.
    """
    classes = {sym.name: sym for sym in parsed.symbols if sym.kind == "class"}
    collected = {c for c in classes if any(fnmatch(c, g) for g in class_globs)}
    inherited = {s for c in collected for s in _fixture_scopes(parsed, c) if s}
    marks = {c: _decorator_requests(classes[c].decorators) for c in inherited if c in classes}
    foreign_base = any(b not in classes and b not in _PLAIN_BASES for b in inherited)
    return collected, inherited, marks, foreign_base or any(r.unknown for r in marks.values())


def _link_symbols(
    linker: _FileLinker,
    fixture_ids: set[str],
    runnable: set[str] | None,
    marks: dict[str, _Requests],
) -> tuple[list[Any], bool]:
    """Link every fixture and collected test in the file; ``(tests, any computed request)``.

    *runnable* is the classes whose test methods pytest collects, ``None`` in a
    conftest, where nothing is collected as a test.
    """
    tests: list[Any] = []
    hidden = False
    for sym in linker.parsed.symbols:
        if sym.kind not in ("function", "method"):
            continue
        own_requests = _decorator_requests(sym.decorators)
        hidden = hidden or own_requests.unknown
        if sym.id in fixture_ids:
            linker.link(sym, _requested_fixtures(sym, own_requests.supplied) + own_requests.requested)
        elif _is_collected_test(sym, runnable):
            tests.append(sym)
            linker.link(sym, _test_requests(linker.parsed, sym, own_requests, marks))
    return tests, hidden


def _is_collected_test(sym: Any, runnable: set[str] | None) -> bool:
    """Whether pytest collects *sym* as a test: a test name, in the module or a collected class."""
    if runnable is None or not sym.name.startswith(DEFAULT_PYTHON_FUNCTIONS):
        return False
    return not sym.parent_name or sym.parent_name in runnable


def _test_requests(
    parsed: Any, sym: Any, own_requests: _Requests, marks: dict[str, _Requests]
) -> list[str]:
    """Fixtures a test asks for: its parameters and decorators, then its classes' marks."""
    scoped = [marks[s] for s in _fixture_scopes(parsed, sym.parent_name) if s in marks]
    supplied = own_requests.supplied.union(*(r.supplied for r in scoped))
    names = _requested_fixtures(sym, supplied) + own_requests.requested
    return names + [n for r in scoped for n in r.requested]


def _link_text(
    linker: _FileLinker,
    text: str,
    tests: list[Any],
    fixture_ids: set[str],
    recorded: Counter[str],
) -> tuple[bool, bool]:
    """Link the request forms only the file's text shows; ``(hidden, everywhere)``."""
    marks_unknown = _link_module_marks(linker, text, tests, recorded)
    runtime_unknown, everywhere = _link_runtime_requests(linker, text, tests, fixture_ids, recorded)
    # Any other use (an alias, a mark stored in a variable, `add_marker`,
    # `request.fixturenames`, a lazy-fixture plugin) is a request no edge records.
    uses = _request_name_uses(text)
    hidden = (
        _hidden_request(text) is not None
        or marks_unknown
        or runtime_unknown
        or uses is None
        or any(n > recorded[name] for name, n in uses.items())
    )
    return hidden, everywhere


def _link_module_marks(
    linker: _FileLinker, text: str, tests: list[Any], recorded: Counter[str]
) -> bool:
    """Link every test to a module ``pytestmark``'s fixtures; whether any is computed."""
    if "pytestmark" not in text:
        return False
    module_marks, unknown = _module_usefixtures(text)
    recorded["usefixtures"] += sum(
        len(_USEFIXTURES_CALL_RE.findall(m)) for m in _PYTESTMARK_RE.findall(text)
    )
    for sym in tests:
        linker.link(sym, module_marks)
    return unknown


def _link_runtime_requests(
    linker: _FileLinker,
    text: str,
    tests: list[Any],
    fixture_ids: set[str],
    recorded: Counter[str],
) -> tuple[bool, bool]:
    """Link literal ``getfixturevalue`` calls; ``(any computed, a helper asks)``."""
    if "getfixturevalue" not in text:
        return False, False
    runtime, unknown = _runtime_requests(text, linker.parsed.symbols)
    everywhere = False
    for sym, name in runtime:
        if sym in tests or sym.id in fixture_ids:
            linker.link(sym, [name])
            recorded["getfixturevalue"] += 1
        else:
            # A helper asking for a fixture serves whichever test calls it.
            everywhere = True
    return unknown, everywhere


def _source_reader(ctx: ResolverContext) -> Callable[[str], str | None]:
    """Text of an indexed file, from the bytes ingestion already read, else from disk."""

    def read(path: str) -> str | None:
        if ctx.repo_path is None and path not in (ctx.source_map or {}):
            return None
        return source_text(path, (ctx.repo_path or Path()) / path, ctx.source_map)

    return read


def _held_text(ctx: ResolverContext) -> Callable[[str], str | None]:
    """Text of an indexed file from the bytes ingestion already read only; ``None`` if not held."""

    def read(path: str) -> str | None:
        data = (ctx.source_map or {}).get(path)
        return None if data is None else decode_source(data)

    return read


class _FixtureInjectionHandler:
    """A test's parameter names are fixture requests pytest resolves at run time."""

    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "python" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_fixture_injection_edges(
            graph,
            parsed_files,
            ctx.repo_path,
            _source_reader(ctx),
            ctx.pytest_roots,
            _held_text(ctx),
        )


class _ConftestHandler:
    """pytest ``conftest.py`` fixtures are imported implicitly by collection."""

    def detect(self, dctx: DetectionContext) -> bool:
        return True

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_conftest_edges(graph, path_set, ctx.pytest_roots)


HANDLERS: list[FrameworkHandler] = [_ConftestHandler(), _FixtureInjectionHandler()]
