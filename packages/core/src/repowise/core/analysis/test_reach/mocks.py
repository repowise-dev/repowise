"""Which repository modules a test replaces outright, read from the test's own text.

A test that swaps a module object for a double runs the double, not the module,
so its route into that module is no evidence that a test exercises it. This
module reads Python (:mod:`.mocks_js` reads JavaScript); the patch forms come
from the mock lexicon
(:attr:`~repowise.core.analysis.health.mocks.lexicon.MockDialect.binding_patches`).

A patch replaces one binding. Only a binding that *is* a module masks it:
``patch("pkg.helpers")``, ``patch.object(pkg, "helpers")``, a ``sys.modules``
entry. ``patch("pkg.mod.func")`` replaces ``func`` inside ``pkg.mod``, and the
test still runs the rest of ``pkg.mod`` (patching where a name is used is how
a module is tested in isolation), so it masks nothing. Ceiling: a binding that
names a module through a consumer's own import (``patch("app.cli.helpers")``
after ``from app import helpers``) is not traced to that module; resolving it
through the consumer's import edges is the upgrade path.

The answer is per test file, not per test function: a file that also imports
the module it replaces keeps its reach, since another test in it may run the
real one. Ceiling: a file mixing both without importing the module still masks
it; reading each test function's own patches is the upgrade path.

Abstain-first: a target built at run time, an alias the file binds more than
once, a relative import, a ``patch`` not imported from the mock library, or a
patch carrying a pass-through keyword (``wraps``, ``side_effect``,
``autospec``) masks nothing, so the test keeps its reach.
"""

from __future__ import annotations

import ast
from collections.abc import Collection, Mapping

from repowise.core.analysis.health.mocks.lexicon import MockDialect, mock_dialect
from repowise.core.ingestion.languages.python_modules import build_python_module_index
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.typescript import resolve_ts_js_import

from .mocks_java import replaced_java_files
from .mocks_js import JS_SUFFIXES, replaced_specifiers


def replaced_modules(path: str, text: str | bytes) -> frozenset[str]:
    """Dotted names of the module bindings the test at *path* replaces outright.

    Every name is a candidate: whether it is a module is
    :func:`replaced_files`'s question, answered against the repository's files.
    """
    dialect = mock_dialect("python")
    if dialect is None or not path.endswith(".py"):
        return frozenset()
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    if not any(marker in text for marker in dialect.file_markers):
        return frozenset()
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        return frozenset()
    aliases = _import_aliases(tree)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            found |= _patched(node, dialect, aliases)
    return frozenset(found - _imported_modules(tree))


# Test files :func:`replaced_files` reads; a caller need not read any other.
MASKABLE_SUFFIXES = (".py", ".java", *JS_SUFFIXES)


def replaced_files(
    texts: Mapping[str, str | bytes], files: Collection[str]
) -> dict[str, frozenset[str]]:
    """``{test: the repository files it replaces outright}``, for the tests in *texts*.

    *files* are the repository's paths. A Python name masks a file only when it
    is exactly that file's dotted module name; a JavaScript or TypeScript
    specifier (:func:`~.mocks_js.replaced_specifiers`) only when it is relative
    and resolves to one of *files*; a Java test's mocks
    (:func:`~.mocks_java.replaced_java_files`) when resolved to a repository Java
    file. Ceiling: a bare or aliased specifier (``@/lib/x``, a workspace package)
    needs the repository's tsconfig and workspace map, so it abstains; resolving
    it with the indexer's full resolver context is the upgrade path. Tests
    replacing none are absent.
    """
    index = build_python_module_index({f for f in files if f.endswith(".py")})
    ctx = ResolverContext(path_set=set(files), stem_map={}, graph=None)
    java_files = {f for f in files if f.endswith(".java")}
    out: dict[str, frozenset[str]] = {}
    for test, text in texts.items():
        if test.endswith(".py"):
            hits = {index[name] for name in replaced_modules(test, text) if name in index}
        elif test.endswith(".java"):
            hits = set(replaced_java_files(test, text, java_files))
        else:
            specs = replaced_specifiers(test, text)
            hits = {
                hit
                for spec in specs
                if spec.startswith(".")
                and (hit := resolve_ts_js_import(spec, test, ctx)) in ctx.path_set
            }
        hits.discard(test)
        if hits:
            out[test] = frozenset(hits)
    return out


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """``{local name: dotted module}`` for names the file binds once, by an absolute import."""
    seen: dict[str, set[str | None]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.asname:
                    seen.setdefault(a.asname, set()).add(a.name)
                else:
                    root = a.name.split(".", 1)[0]
                    seen.setdefault(root, set()).add(root)
        elif isinstance(node, ast.ImportFrom):
            for a in node.names:
                local = a.asname or a.name
                dotted = f"{node.module}.{a.name}" if node.module and not node.level else None
                seen.setdefault(local, set()).add(dotted)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            seen.setdefault(node.id, set()).add(None)
        elif isinstance(node, ast.arg):
            seen.setdefault(node.arg, set()).add(None)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) or (
            isinstance(node, ast.ExceptHandler) and node.name
        ):
            seen.setdefault(node.name, set()).add(None)
    return {name: next(iter(v)) for name, v in seen.items() if len(v) == 1 and None not in v}


def _imported_modules(tree: ast.AST) -> set[str]:
    """Every dotted name the file imports: modules, and ``from`` names under them."""
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            out.add(node.module)
            out.update(f"{node.module}.{a.name}" for a in node.names)
    return out


def _dotted(node: ast.expr, aliases: Mapping[str, str] | None = None) -> str | None:
    """``a.b.c`` for a plain name chain; with *aliases*, its root resolved through them."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    root = node.id if aliases is None else aliases.get(node.id)
    if root is None:
        return None
    return ".".join([root, *reversed(parts)])


def _callee(call: ast.Call, dialect: MockDialect, aliases: Mapping[str, str]) -> str | None:
    """The callee as :attr:`MockDialect.binding_patches` spells it, guard roots dropped.

    A callee whose root the receiver table does not name must be imported from
    a guard root (``from unittest.mock import patch``, ``import mock``).
    """
    name = _dotted(call.func)
    if name is None:
        return None
    root = name.split(".", 1)[0]
    if root not in dialect.receiver_methods:
        origin = aliases.get(root)
        if origin is None or origin.split(".", 1)[0] not in dialect.guard_roots:
            return None
        name = _dotted(call.func, aliases) or name
    parts = name.split(".")
    while len(parts) > 1 and parts[0] in dialect.guard_roots:
        parts.pop(0)
    return ".".join(parts)


def _literal(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _patched(call: ast.Call, dialect: MockDialect, aliases: Mapping[str, str]) -> set[str]:
    """The dotted bindings one call replaces, or nothing when any part is unproven."""
    form = dialect.binding_patches.get(_callee(call, dialect, aliases) or "")
    if form is None or any(k.arg in dialect.passthrough_keywords for k in call.keywords):
        return set()
    args = call.args
    if form == "dotted_or_object":
        form = "dotted" if args and _literal(args[0]) is not None else "object"
    if form == "dotted":
        target = _literal(args[0]) if args else None
        return {target} if target else set()
    if form == "object":
        owner = _dotted(args[0], aliases) if len(args) >= 2 else None
        attr = _literal(args[1]) if len(args) >= 2 else None
        return {f"{owner}.{attr}"} if owner and attr else set()
    # ``modules``: entries of ``sys.modules``, by key.
    if not args or not (_literal(args[0]) == "sys.modules" or _dotted(args[0]) == "sys.modules"):
        return set()
    if len(args) >= 2 and isinstance(args[1], ast.Dict):
        keys = [_literal(k) for k in args[1].keys]
        return set() if None in keys else {k for k in keys if k}
    key = _literal(args[1]) if len(args) >= 2 else None
    return {key} if key else set()
