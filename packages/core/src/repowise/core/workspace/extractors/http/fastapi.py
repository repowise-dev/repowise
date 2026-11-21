"""FastAPI / Python HTTP provider dialect — ``@app.get('/path')``.

Resolves the real route path by stitching three segments where present:
a cross-file ``include_router(prefix=...)`` mount, the in-file
``APIRouter(prefix=...)`` binding, and the decorator path itself.
"""

from __future__ import annotations

import ast
import posixpath
import re
import textwrap
from typing import TYPE_CHECKING

from ..base import line_at
from ..calls import code_matches
from ..langs import PYTHON
from .dialect import METHODS, build_provider_contract
from .flask import flask_file
from .mounts import (
    add_mounts,
    balanced_args,
    compose_prefix,
    declare_module_mount,
    module_prefix,
    router_prefixes,
)

if TYPE_CHECKING:
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

# @<var>.get('/path') / @<var>.post('/path'). The variable is captured so its
# router prefix can be resolved; only known routers (those bound to APIRouter /
# FastAPI in-file, plus the conventional ``app`` / ``router`` names) are kept, so
# an unrelated ``@cache.get(...)`` decorator is not mistaken for a route.
# The path is ``*`` not ``+``: ``@router.post("")`` on a prefixed router is the
# idiomatic way to serve the collection root, and requiring one character made
# those routes invisible entirely. The empty path contributes nothing on its own,
# so ``build_provider_contract`` still drops it when no prefix stitches on.
_FASTAPI_RE = re.compile(
    rf"""@(\w+)\.({METHODS})\s*\(\s*['"]([^'"]*)['"]""",
    re.IGNORECASE,
)

# <receiver>.include_router(...): a router mounted on an app or a parent router.
_INCLUDE_ROUTER_RE = re.compile(r"""(?:(\w+)\s*\.\s*)?include_router\s*\(""")
_FIRST_ARG_RE = re.compile(r"""\s*([\w.]+)""")
_PREFIX_KW_RE = re.compile(r"""prefix\s*=\s*['"]([^'"]+)['"]""")

_DEFAULT_ROUTER_NAMES = frozenset({"app", "router"})

# Mount keys are ``fastapi:<file>:<router variable>``.
_NAMESPACE = "fastapi"


class FastApiDialect:
    name = "fastapi"
    extensions = PYTHON
    # A route's receiver is `app`, `router` or a name bound to one of the two
    # classes; a mount is an `include_router`.
    requires = ("@app.", "@router.", "APIRouter", "FastAPI", "include_router")

    def collect_mounts(self, ctx: ScanContext) -> dict[str, str]:
        """Find ``include_router(target, prefix=...)`` mounts declared in the file.

        Keyed by the mounted router's identity, the file that defines it and
        its variable (``routers/users.py:router``), never by the name alone:
        most FastAPI modules call their router ``router``, so a name key put
        one module's mount prefix on all of them. A router mounted on another
        router is recorded under that parent, so nested prefixes compose. A
        target whose defining file cannot be found is left unmounted.
        """
        if flask_file(ctx.content):
            return {}
        own = router_prefixes(ctx.content, "APIRouter")
        imports: dict[str, tuple[str, str | None]] | None = None
        out: dict[str, str] = {}
        for m in code_matches(ctx, _INCLUDE_ROUTER_RE):
            args = balanced_args(ctx.content, m.end() - 1)  # m.end()-1 is the '('
            var_m = _FIRST_ARG_RE.match(args)
            if not var_m:
                continue
            if imports is None:
                imports = _python_imports(ctx.content)
            target = _router_identity(ctx, var_m.group(1), imports)
            if target is None:
                continue
            pm = _PREFIX_KW_RE.search(args)
            prefix = pm.group(1) if pm else ""
            receiver = m.group(1)
            parent = ""
            if receiver in own:
                # Mounted on an in-file APIRouter: that router's own prefix
                # comes first, then wherever the router itself is mounted.
                prefix = compose_prefix(own[receiver], prefix)
                parent = _router_key(ctx.rel_path, receiver)
            elif receiver and receiver in imports:
                # Ceiling: an imported parent router's own APIRouter(prefix=)
                # lives in its file and is not composed here.
                parent = _router_identity(ctx, receiver, imports) or ""
            add_mounts(out, declare_module_mount(_NAMESPACE, target, prefix, parent))
        return out

    def extract(self, ctx: ScanContext) -> list[Contract]:
        # `@app.get` is spelled the same in both frameworks and `app` is a
        # conventional name in both, so a Flask file read here would publish
        # every one of its routes a second time, labelled FastAPI.
        if flask_file(ctx.content):
            return []
        prefixes = router_prefixes(ctx.content, "APIRouter|FastAPI")
        known = set(prefixes) | _DEFAULT_ROUTER_NAMES

        out: list[Contract] = []
        for m in code_matches(ctx, _FASTAPI_RE):
            var, method, path_raw = m.group(1), m.group(2), m.group(3)
            if var not in known:
                continue
            # In-file APIRouter(prefix=...) then any cross-file mount of this var.
            path = compose_prefix(prefixes.get(var, ""), path_raw)
            path = compose_prefix(mount_prefix(ctx, var), path)
            c = build_provider_contract(
                ctx,
                method=method.upper(),
                path_raw=path,
                framework="fastapi",
                line=line_at(ctx.content, m.start()),
            )
            if c is not None:
                out.append(c)
        return out


def _router_key(rel_path: str, var: str) -> str:
    return f"{rel_path}:{var}"


def mount_prefix(ctx: ScanContext, var: str) -> str:
    """Where the router *var* defined in *ctx*'s file is mounted, "" if nowhere.

    A router mounted at two different prefixes reads as unmounted, as an
    ambiguous mount always has: its routes keep their in-file path.
    """
    _mounted, prefix = module_prefix(ctx.mounts, _NAMESPACE, _router_key(ctx.rel_path, var))
    return prefix or ""


def _python_imports(content: str) -> dict[str, tuple[str, str | None]]:
    """``{local name: (module, imported name or None)}`` for the file's imports.

    A relative module keeps its leading dots. Module-level imports win,
    including those inside ``try`` and ``if`` blocks; an import inside a
    function or class binds a name only when the module does not. A file
    Python cannot parse yields no imports. The text is dedented first so an
    indented snippet reads as the module it would be.

    Ceiling: one table per file, not per scope, so a function that rebinds a
    module-level name to another module reads as the module-level one.
    """
    try:
        tree = ast.parse(textwrap.dedent(content))
    except (SyntaxError, ValueError):
        return {}
    nested: list[ast.AST] = []
    out: dict[str, tuple[str, str | None]] = {}
    # Depth first in source order, so a later module-level import rebinds an
    # earlier one as it does at run time.
    pending: list[ast.AST] = list(reversed(tree.body))
    while pending:
        node = pending.pop()
        # A function's imports, and an `except` fallback the `try` import
        # normally wins over, bind only names the module leaves free.
        if isinstance(
            node,
            (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda, ast.ExceptHandler),
        ):
            nested.append(node)
            continue
        _bind_import(node, out, overwrite=True)
        pending.extend(reversed(list(ast.iter_child_nodes(node))))
    for scope in nested:
        for node in ast.walk(scope):
            _bind_import(node, out, overwrite=False)
    return out


def _bind_import(node: ast.AST, out: dict[str, tuple[str, str | None]], *, overwrite: bool) -> None:
    bound: list[tuple[str, tuple[str, str | None]]] = []
    if isinstance(node, ast.Import):
        for a in node.names:
            # `import a.b` binds `a`; `import a.b as c` binds `c` to `a.b`.
            name = a.asname or a.name.split(".", 1)[0]
            bound.append((name, (a.name if a.asname else name, None)))
    elif isinstance(node, ast.ImportFrom):
        module = "." * node.level + (node.module or "")
        bound.extend((a.asname or a.name, (module, a.name)) for a in node.names if a.name != "*")
    for name, value in bound:
        if overwrite or name not in out:
            out[name] = value


def _router_identity(
    ctx: ScanContext, expr: str, imports: dict[str, tuple[str, str | None]]
) -> str | None:
    """The ``file:variable`` key of the router *expr* names in *ctx*'s file.

    ``users.router`` after ``from routers import users`` is
    ``<dir>/routers/users.py:router``; a bare name bound by
    ``from .users import router as users_router`` is ``<dir>/users.py:router``;
    a bare name the file does not import is the file's own variable.
    """
    head, *rest = expr.split(".")
    if head not in imports:
        return None if rest else _router_key(ctx.rel_path, head)
    root = ctx.repo_root
    if root is None:
        return None
    module, name = imports[head]
    if not rest:
        if name is None:
            return None
        target, var = module, name
    else:
        parts = [*([name] if name else []), *rest[:-1]]
        joiner = "" if module.endswith(".") else "."
        target, var = (module + joiner + ".".join(parts) if parts else module), rest[-1]
    rel = _resolve_module(root, ctx.rel_path, target)
    return _router_key(*_defined_at(root, rel, var)) if rel else None


def _defined_at(root: Path, rel: str, var: str) -> tuple[str, str]:
    """Where *var*, as module *rel* exposes it, is defined.

    A package ``__init__.py`` that re-exports a router (``from .users import
    router``) is followed one hop to the defining file. Ceiling: one hop; a
    longer re-export chain keys the router at the second file.
    """
    try:
        content = (root / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rel, var
    if re.search(rf"^[ \t]*{re.escape(var)}\s*(?::[^=\n]+)?=(?!=)", content, re.MULTILINE):
        return rel, var
    module, name = _python_imports(content).get(var, ("", None))
    if name is None:
        return rel, var
    source = _resolve_module(root, rel, module)
    return (source, name) if source else (rel, var)


def _resolve_module(root: Path, from_path: str, dotted: str) -> str | None:
    """The repo-relative file a Python module name resolves to from *from_path*.

    A relative name resolves against that file's package. An absolute name is
    tried under each directory from the file's own up to the repo root, since
    the import root is often a subdirectory (``backend/``) and not the repo root.
    """
    level = len(dotted) - len(dotted.lstrip("."))
    parts = [p for p in dotted[level:].split(".") if p]
    here = posixpath.dirname(from_path)
    if level:
        base = here
        for _ in range(level - 1):
            base = posixpath.dirname(base)
        bases = [base]
    else:
        bases = [here]
        while bases[-1]:
            bases.append(posixpath.dirname(bases[-1]))
    for base in bases:
        stem = posixpath.join(base, *parts)
        if not stem:
            continue
        for candidate in (f"{stem}.py", f"{stem}/__init__.py"):
            if (root / candidate).is_file():
                return candidate
    return None
