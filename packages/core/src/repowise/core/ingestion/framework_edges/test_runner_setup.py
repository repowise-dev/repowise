"""JS/TS test-runner setup files named in runner configs.

A runner config (``vitest.config.ts``, ``jest.config.js``, a ``jest`` block in
``package.json``) loads the files under ``setupFiles``, ``setupFilesAfterEnv``,
``globalSetup`` and ``globalTeardown`` before the tests it runs, so a change to
one, or to anything it imports, can break each of those tests. No import
statement says so. This is the runner's counterpart of a ``conftest.py``, and
it gets the same edge: from each test the config runs to the setup file.

Configs are code (spread base configs, helper factories, env-chosen globs), so
the values are read, not evaluated: every string literal inside a setup key's
value that resolves to an indexed file counts, wherever the key sits in the
file. Which tests a config runs is not evaluated either; each config stands for
the JS/TS tests under its package (the nearest directory holding a
``package.json``). Ceiling: a config whose ``include`` globs narrow it to part
of its package still links the whole package, which over-selects but never
misses. Upgrade path: honour literal ``include`` / ``root`` values when every
entry is a literal.
"""

from __future__ import annotations

import posixpath
import re
from typing import TYPE_CHECKING, Any

from ...test_paths import is_test_path
from ..resolvers import ResolverContext
from .base import DetectionContext, FrameworkHandler, _add_edge_if_new, source_bytes

if TYPE_CHECKING:
    import networkx as nx

#: Config keys whose value names files the runner loads before the tests.
TEST_SETUP_KEYS: tuple[str, ...] = (
    "setupFiles",
    "setupFilesAfterEnv",
    "globalSetup",
    "globalTeardown",
)

# Stamped on the test -> setup file edge, as conftest edges are, so consumers
# that mean "imports" can tell the two apart.
SETUP_FILE_HINT = "test_runner_setup"

_JS_EXTS = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")
_CONFIG_PREFIXES = ("vitest.", "vite.", "jest.", "playwright.")

_KEY_RE = re.compile(
    rb"""["']?\b(?:""" + b"|".join(k.encode() for k in TEST_SETUP_KEYS) + rb""")\b["']?\s*[:=](?!=)"""
)
# A ``//`` after ``:`` is a URL (``https://``), not a comment.
_LINE_COMMENT_RE = re.compile(rb"(?<![:\w])//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(rb"/\*.*?\*/", re.DOTALL)
_OPEN, _CLOSE = b"([{", b")]}"


def is_runner_config(path: str) -> bool:
    """A file that may configure a JS/TS test runner's setup files."""
    name = path.rpartition("/")[2]
    if name == "package.json":
        return True
    return name.startswith(_CONFIG_PREFIXES) and name.endswith((*_JS_EXTS, ".json"))


def setup_specs(text: bytes) -> list[str]:
    """The string literals inside every setup key's value in a config's *text*.

    The value runs from the key to the first ``,`` / ``;`` / closing bracket or
    line end outside brackets (a line end before the value starts does not
    count), so ``setupFiles: [...base, "a.ts"].map(f)`` and
    ``setupFiles: resolve("a.ts")`` both yield ``a.ts``. A template literal with
    a hole is not a path and is skipped.
    """
    text = _LINE_COMMENT_RE.sub(b"", _BLOCK_COMMENT_RE.sub(b"", text))
    specs: list[str] = []
    for match in _KEY_RE.finditer(text):
        specs.extend(_strings_in_value(text, match.end()))
    return list(dict.fromkeys(specs))


def _strings_in_value(text: bytes, start: int) -> list[str]:
    """String literals from *start* to the end of the value expression there."""
    out: list[str] = []
    depth = 0
    started = False
    i = start
    while i < len(text):
        ch = text[i : i + 1]
        if depth == 0 and (ch in (b",", b";") or (ch == b"\n" and started)):
            break
        if ch in (b'"', b"'", b"`"):
            end = _string_end(text, i)
            body = text[i + 1 : end]
            if not (ch == b"`" and b"${" in body):
                out.append(body.decode("utf-8", errors="replace"))
            i = end + 1
            started = True
            continue
        if ch in _OPEN:
            depth += 1
        elif ch in _CLOSE:
            if depth == 0:
                break
            depth -= 1
        started = started or not ch.isspace()
        i += 1
    return out


def _string_end(text: bytes, start: int) -> int:
    """Index of the quote closing the string opened at *start* (or the text's end)."""
    quote = text[start : start + 1]
    i = start + 1
    while i < len(text):
        ch = text[i : i + 1]
        if ch == b"\\":
            i += 2
            continue
        if ch == quote:
            return i
        i += 1
    return len(text)


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


def _add_setup_edges(
    graph: nx.DiGraph, parsed_files: dict[str, Any], ctx: ResolverContext, path_set: set[str]
) -> int:
    source_map = ctx.source_map or {}
    setups: dict[str, set[str]] = {}  # package dir -> setup files its configs load
    for path in sorted(path_set):
        if not is_runner_config(path):
            continue
        text = source_bytes(path, parsed_files[path], source_map)
        if not any(k.encode() in text for k in TEST_SETUP_KEYS):
            continue
        for spec in setup_specs(text):
            target = resolve_setup_spec(spec, path, ctx)
            if target is not None:
                setups.setdefault(_package_dir(path, path_set), set()).add(target)
    if not setups:
        return 0

    count = 0
    tests = sorted(p for p in path_set if p.endswith(_JS_EXTS) and is_test_path(p))
    for pkg, targets in sorted(setups.items()):
        prefix = f"{pkg}/" if pkg else ""
        for test in tests:
            if not test.startswith(prefix):
                continue
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
