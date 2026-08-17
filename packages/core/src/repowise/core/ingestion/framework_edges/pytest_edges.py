"""pytest conftest convention edges.

Two conventions, both invisible to a static import graph: a ``conftest.py`` is
imported by collection rather than by any statement, and a test's parameter
names are fixture requests resolved at run time.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    add_symbol_edge,
)

if TYPE_CHECKING:
    import networkx as nx

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

_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$")

# pytest collects methods only from classes matching `python_classes`, so a
# `class Harness` with a `test_connection` method is never run and its
# parameters are ordinary arguments its callers pass. The setting is read
# rather than assumed: celery configures `test_*`, and assuming the default
# refused 346 of its 359 bindings.
_DEFAULT_TEST_CLASS_GLOBS = ("Test*",)
_PYTHON_CLASSES_RE = re.compile(
    r"^\s*python_classes\s*=\s*(.+?)\s*$", re.MULTILINE
)


def _test_class_globs(repo_path: Path | None) -> tuple[str, ...]:
    """The ``python_classes`` globs this project collects test classes by."""
    if repo_path is None:
        return _DEFAULT_TEST_CLASS_GLOBS
    for name in ("pyproject.toml", "pytest.ini", "tox.ini", "setup.cfg"):
        try:
            text = (repo_path / name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        match = _PYTHON_CLASSES_RE.search(text)
        if not match:
            continue
        globs = tuple(match.group(1).strip().strip("\"'").split())
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
    args: list[str] = []
    current: list[str] = []
    for ch in text[start:]:
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
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


def _add_conftest_edges(graph: nx.DiGraph, path_set: set[str]) -> int:
    """conftest.py -> test files in the same or child directories."""
    count = 0
    conftest_paths = [p for p in path_set if Path(p).name == "conftest.py"]

    for conf in conftest_paths:
        conf_dir = Path(conf).parent.as_posix()
        prefix = f"{conf_dir}/" if conf_dir != "." else ""
        for p in path_set:
            if p == conf:
                continue
            node = graph.nodes.get(p, {})
            if not node.get("is_test", False):
                continue
            if (p.startswith(prefix) or (prefix == "" and "/" not in p)) and _add_edge_if_new(
                graph, p, conf
            ):
                count += 1
    return count


def _registered_name(sym: Any, decorator: str) -> str:
    """The name pytest registers the fixture under.

    ``name=`` must be read as a top-level keyword, not found anywhere in the
    text: ``@pytest.fixture(params=[dict(name="alice")])`` otherwise registers
    the fixture as ``alice``, which both loses the real request and mints a
    fabricated one.
    """
    for arg in _call_arguments(decorator):
        match = _NAME_KWARG_ARG_RE.match(arg)
        if match:
            return match.group(1)
    return sym.name


def _declared_fixtures(parsed: Any) -> dict[tuple[str | None, str], str]:
    """``{(owning class or None, fixture name): symbol id}``.

    Keyed by scope rather than by name alone. A fixture declared inside a test
    class serves that class only, and flattening the two makes every sibling
    class share it — which is a wrong edge whichever way the collision is
    resolved.
    """
    out: dict[tuple[str | None, str], str] = {}
    for sym in parsed.symbols:
        if sym.kind not in ("function", "method"):
            continue
        for dec in sym.decorators:
            if not _FIXTURE_DECORATOR_RE.match(dec.strip()):
                continue
            out.setdefault((sym.parent_name, _registered_name(sym, dec)), sym.id)
            break
    return out


def _requested_fixtures(sym: Any) -> list[str]:
    """The parameter names *sym* asks pytest to inject.

    Reads the recorded signature rather than re-parsing: a second pass over
    every test file is the cost of running inside the build rather than beside
    it.
    """
    supplied: set[str] = set()
    for dec in sym.decorators:
        if not _PARAMETRIZE_RE.match(dec.strip()):
            continue
        # Only the first argument is the argnames spec. Reading the whole
        # decorator instead makes every parametrize *value* look like a name
        # the test supplies, so a real fixture request whose name is also a
        # common literal is silently refused.
        args = _call_arguments(dec)
        if args:
            for token in _QUOTED_RE.findall(args[0]):
                supplied.update(p.strip() for p in token.split(","))

    names = []
    for raw in _call_arguments(sym.signature or ""):
        # A defaulted parameter is never injected: pytest skips any argument
        # whose default is not empty. The arguments are already split at top
        # level, so an `=` here is a default and not part of an annotation.
        if "=" in raw or raw.startswith("*"):
            continue
        name = raw.split(":")[0].strip()
        if not name or name in ("self", "cls", "/"):
            continue
        if name in supplied:
            continue
        names.append(name)
    return names


def _add_fixture_injection_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], repo_path: Path | None = None
) -> int:
    """Link each test function to the fixture it asks for by name.

    Scope follows pytest's own rule, innermost first: the test's own class, then
    its module, then the nearest ``conftest.py`` at or above it. Nothing else is
    searched, so a plugin-provided fixture stays unclaimed rather than being
    bound to a same-named local one.
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

    count = 0
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "python" or not _TEST_FILE_RE.search(path):
            continue

        own = _declared_fixtures(parsed)
        # Nearest-first: the deepest conftest directory that is a prefix of this
        # file's directory shadows the ones above it, as pytest does.
        chain = sorted(
            (d for d in conftests if path.startswith(f"{d}/") or d == "."),
            key=len,
            reverse=True,
        )

        for sym in parsed.symbols:
            if sym.kind not in ("function", "method") or not sym.name.startswith("test_"):
                continue
            if sym.parent_name and not any(
                fnmatch(sym.parent_name, g) for g in class_globs
            ):
                continue
            for name in _requested_fixtures(sym):
                target = (
                    own.get((sym.parent_name, name))
                    or own.get((None, name))
                    or next(
                        (conftests[d][name] for d in chain if name in conftests[d]), None
                    )
                )
                if target and add_symbol_edge(graph, sym.id, target):
                    count += 1
    return count


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
        return _add_fixture_injection_edges(graph, parsed_files, ctx.repo_path)


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
        return _add_conftest_edges(graph, path_set)


HANDLERS: list[FrameworkHandler] = [_ConftestHandler(), _FixtureInjectionHandler()]
