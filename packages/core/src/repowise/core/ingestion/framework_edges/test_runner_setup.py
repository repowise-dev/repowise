"""JS/TS test-runner setup files named in runner configs.

A runner config (``vitest.config.ts``, ``jest.config.js``, a ``jest`` block in
``package.json``) loads the files under its setup keys
(:data:`~..languages.js_config_values.TEST_SETUP_KEYS`) before the tests it
runs, so a change to one, or to anything it imports, can break each of those
tests. No import statement says so. This is the runner's counterpart of a
``conftest.py``, and it gets the same edge: from each test the config runs to
the setup file.

Configs are read, not evaluated (:mod:`~..languages.js_config_values`): every
string literal in a setup key's value that resolves to an indexed file counts.
Which tests a config runs is not evaluated either; a config stands for the
JS/TS tests under its package (the nearest directory holding a
``package.json``), or under the whole repository when a ``root`` / ``dir`` /
``testDir`` / ``rootDir`` / ``roots`` value climbs out of the config's
directory with ``..``. Ceiling: ``include`` globs that narrow a config to part
of its package are not honoured, which over-selects but never misses. Upgrade
path: honour literal ``include`` / ``root`` values when every entry is a
literal.

Configs are found by name over the indexed path set, not by the TS workspace
scan: that walk collects only ``vitest.config.*`` / ``vite.config.*``, and the
configs that name setup files are often shared or scoped modules
(``vitest.shared.config.ts``, ``jest.config.js``, ``package.json``).
"""

from __future__ import annotations

import posixpath
from typing import TYPE_CHECKING, Any

from ...test_paths import is_test_path
from ..languages.js_config_values import TEST_SETUP_KEYS, string_literals, value_spans
from ..resolvers import ResolverContext
from ..source_text import source_bytes
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new

if TYPE_CHECKING:
    import networkx as nx

# Stamped on the test -> setup file edge, as conftest edges are, so consumers
# that mean "imports" can tell the two apart.
SETUP_FILE_HINT = "test_runner_setup"

_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_CONFIG_PREFIXES = ("vitest.", "vite.", "jest.", "playwright.")
# Keys that move where a runner looks for tests.
_TEST_ROOT_KEYS = ("root", "dir", "testDir", "rootDir", "roots")
_SETUP_KEY_BYTES = tuple(k.encode() for k in TEST_SETUP_KEYS)


def is_runner_config(path: str) -> bool:
    """A file that may configure a JS/TS test runner's setup files."""
    name = path.rpartition("/")[2]
    if name == "package.json":
        return True
    return name.startswith(_CONFIG_PREFIXES) and name.endswith((*_JS_EXTS, ".json"))


def setup_specs(text: bytes) -> list[str]:
    """The string literals inside every setup key's value in a config's *text*."""
    specs = [s for span in value_spans(text, TEST_SETUP_KEYS) for s in string_literals(span)]
    return list(dict.fromkeys(specs))


def leaves_its_directory(text: bytes) -> bool:
    """Whether the config points its test root above its own directory."""
    return any(
        ".." in s for span in value_spans(text, _TEST_ROOT_KEYS) for s in string_literals(span)
    )


def _package_dir(config: str, path_set: set[str]) -> str:
    """The nearest directory at or above *config* holding a ``package.json``; ``""`` for none."""
    d = posixpath.dirname(config)
    while d:
        if f"{d}/package.json" in path_set:
            return d
        d = posixpath.dirname(d)
    return ""


def resolve_setup_spec(spec: str, config: str, ctx: ResolverContext) -> str | None:
    """The indexed file *spec*, written in *config*, names, or ``None``.

    A runner resolves a relative setup path against its root, which may be the
    config's directory or any directory above it (a config under
    ``test/vitest/`` with ``root`` set to the repository), so each is tried,
    nearest first. ``<rootDir>/`` is the package directory.
    """
    from ..resolvers.typescript import resolve_ts_js_import

    if spec.startswith("<rootDir>/"):
        bases = [_package_dir(config, ctx.path_set)]
        spec = spec[len("<rootDir>/") :]
    else:
        bases = []
        d = posixpath.dirname(config)
        while True:
            bases.append(d)
            if not d:
                break
            d = posixpath.dirname(d)
    if not spec or spec.startswith("/") or "\n" in spec:
        return None
    for base in bases:
        joined = posixpath.normpath(posixpath.join(base, spec))
        if joined.startswith(".."):
            continue
        resolved = resolve_ts_js_import(f"./{joined}", "_", ctx)
        if resolved and not resolved.startswith("external:") and resolved != config:
            return resolved
    return None


def _setups_by_scope(
    parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> dict[str, set[str]]:
    """``{directory whose tests run them: setup files}`` over every runner config."""
    setups: dict[str, set[str]] = {}
    for path in sorted(path_set):
        if not is_runner_config(path):
            continue
        text = source_bytes(path, parsed_files[path].file_info.abs_path, ctx.source_map)
        if not any(k in text for k in _SETUP_KEY_BYTES):
            continue
        targets = {t for s in setup_specs(text) if (t := resolve_setup_spec(s, path, ctx))}
        if targets:
            scope = "" if leaves_its_directory(text) else _package_dir(path, path_set)
            setups.setdefault(scope, set()).update(targets)
    return setups


def _add_setup_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    setups = _setups_by_scope(parsed_files, ctx, path_set)
    if not setups:
        return 0
    # Each test once: the setup files of every scope at or above its directory.
    count = 0
    # A runnable test is named like one; a helper under a test directory is not.
    tests = (p for p in path_set if p.endswith(_JS_EXTS) and is_test_path(posixpath.basename(p)))
    for test in sorted(tests):
        targets: set[str] = set()
        d = posixpath.dirname(test)
        while True:
            targets |= setups.get(d, set())
            if not d:
                break
            d = posixpath.dirname(d)
        for target in sorted(targets):
            if _add_edge_if_new(graph, test, target):
                graph[test][target]["hint_source"] = SETUP_FILE_HINT
                count += 1
    return count


class _SetupFilesHandler:
    """Runner configs load their setup files before every test they run."""

    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.endswith(_JS_EXTS) for p in dctx.path_set)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_setup_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_SetupFilesHandler()]
