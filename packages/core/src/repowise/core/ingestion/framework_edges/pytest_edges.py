"""pytest conftest convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
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

# `@fixture`, `@pytest.fixture`, `@pytest.fixture(scope="module")`, and the
# aliased `@pytest_asyncio.fixture`. Anchored at the attribute tail so a
# user-defined `@my.fixture` is accepted too — pytest resolves the decorator by
# identity, not by module, and a name that ends in `fixture` and is used as one
# is one far more often than not.
_FIXTURE_DECORATOR_RE = re.compile(r"^@(?:[\w.]+\.)?fixture\b")
# `@pytest.fixture(name="app")` renames the fixture: the function stays
# `fixture_app` and tests ask for `app`. Matching the function name here would
# both miss the real name and claim a wrong one.
_FIXTURE_NAME_KWARG_RE = re.compile(r"""\bname\s*=\s*["']([^"']+)["']""")
# The parameter names a test declares itself. `parametrize` supplies them, so
# they are not fixture requests — and a parametrize argument that happens to
# share a fixture's name would otherwise mint a wrong edge.
_PARAMETRIZE_RE = re.compile(r"^@(?:[\w.]+\.)?parametrize\b")
_PARAM_NAMES_RE = re.compile(r"""["']([^"']+)["']""")
_SIGNATURE_PARAMS_RE = re.compile(r"\(([^)]*)\)")

_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_[^/]*|[^/]*_test)\.py$")


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


def _declared_fixtures(parsed: Any) -> dict[str, str]:
    """``{fixture name: symbol id}`` for every fixture *parsed* declares."""
    out: dict[str, str] = {}
    for sym in parsed.symbols:
        if sym.kind not in ("function", "method"):
            continue
        for dec in sym.decorators:
            if not _FIXTURE_DECORATOR_RE.match(dec.strip()):
                continue
            named = _FIXTURE_NAME_KWARG_RE.search(dec)
            out.setdefault(named.group(1) if named else sym.name, sym.id)
            break
    return out


def _requested_fixtures(sym: Any) -> list[str]:
    """The parameter names *sym* asks pytest to inject.

    Reads the recorded signature rather than re-parsing the file: the parse the
    pipeline already did is the same one, and a second `ast.parse` of every test
    file is the cost this pass exists inside a build to avoid.
    """
    match = _SIGNATURE_PARAMS_RE.search(sym.signature or "")
    if not match:
        return []
    supplied: set[str] = set()
    for dec in sym.decorators:
        if _PARAMETRIZE_RE.match(dec.strip()):
            # First string group is the argnames spec; pytest also accepts a
            # comma-joined single string.
            for token in _PARAM_NAMES_RE.findall(dec):
                supplied.update(p.strip() for p in token.split(","))
    names = []
    for raw in match.group(1).split(","):
        name = raw.split(":")[0].split("=")[0].strip()
        if not name or name.startswith("*") or name in ("self", "cls"):
            continue
        if name in supplied:
            continue
        names.append(name)
    return names


def _add_fixture_injection_edges(graph: nx.DiGraph, parsed_files: dict[str, Any]) -> int:
    """Link each test function to the fixture it asks for by name.

    The pairing is the whole point. The conftest hint above already computes
    both halves — which fixtures a conftest declares, which parameter names a
    test asks for — and then keeps only the file pair, so "this fixture is used
    by nobody" and "editing this fixture touches these tests" are both
    unanswerable today.

    Scope follows pytest's own rule: a fixture declared in the test's own module
    wins, then the nearest ``conftest.py`` at or above it. Nothing else is
    searched, so a plugin-provided fixture stays unclaimed rather than being
    bound to a same-named local one.
    """
    conftests: dict[str, dict[str, str]] = {}
    for path, parsed in parsed_files.items():
        if Path(path).name != "conftest.py":
            continue
        declared = _declared_fixtures(parsed)
        if declared:
            conftests[Path(path).parent.as_posix()] = declared

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
            if not sym.name.startswith("test_"):
                continue
            for name in _requested_fixtures(sym):
                target = own.get(name)
                if target is None:
                    target = next(
                        (conftests[d][name] for d in chain if name in conftests[d]), None
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
        return _add_fixture_injection_edges(graph, parsed_files)


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
