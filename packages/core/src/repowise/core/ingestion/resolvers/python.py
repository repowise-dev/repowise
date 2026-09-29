"""Python import resolution."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ..languages.python_modules import (
    _index_priority,
    _is_package_dir,
    build_python_module_index,
)
from .context import ResolverContext

if TYPE_CHECKING:
    from ..models import Import

# Stdlib top-level names, independent of the indexer's own Python: the running
# interpreter's set drops modules removed in 3.12/3.13 that older code imports.
_STDLIB_NAMES = sys.stdlib_module_names | {
    "asynchat", "asyncore", "distutils", "imp", "smtpd",  # removed in 3.12
    "aifc", "audioop", "cgi", "cgitb", "chunk", "crypt", "imghdr", "mailcap",
    "msilib", "nis", "nntplib", "ossaudiodev", "pipes", "sndhdr", "spwd",
    "sunau", "telnetlib", "uu", "xdrlib", "lib2to3",  # removed in 3.13
}  # fmt: skip


def _module_index(ctx: ResolverContext) -> dict[str, str]:
    """Source-root-aware dotted-module → path index, built once per context.

    Cached on the context (mirrors the lazy per-language index pattern used
    by the PHP / TS / Kotlin resolvers) so the index is computed a single
    time across every Python import in a build.
    """
    idx = getattr(ctx, "_python_module_index", None)
    if idx is None:
        idx = build_python_module_index(ctx.path_set)
        ctx._python_module_index = idx
    return idx


def _module_file(stem: str, path_set: frozenset[str] | set[str]) -> str | None:
    """``<stem>.py`` or ``<stem>/__init__.py``, whichever the repo has."""
    for c in (f"{stem}.py", f"{stem}/__init__.py"):
        if c in path_set:
            return c
    return None


def resolve_python_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Python import to a repo-relative file path."""
    hit = _resolve_file(module_path, importer_path, ctx)
    if hit:
        # A module naming itself is no dependency.
        return None if hit == importer_path else hit
    return _external(module_path, ctx)


def _external(module_path: str, ctx: ResolverContext) -> str | None:
    """The ``external:`` node for an absolute import no repo file defines."""
    if module_path.startswith("."):
        return None
    # Nothing in the repo defines this module, so register it the way every
    # other language resolver does: the packages tab and the import counts can
    # only see a third-party or stdlib dependency if the miss becomes a node.
    # Stdlib is included on purpose, as Go and TypeScript already do, and
    # io_kind.py seeds stdlib names for exactly this consumer.
    return ctx.add_external_node(module_path)


def _resolve_file(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """The repo file *module_path* names from *importer_path*, or None."""
    importer_dir = Path(importer_path).parent

    # Relative import: ".sibling" or "..parent.module"
    if module_path.startswith("."):
        dots = len(module_path) - len(module_path.lstrip("."))
        rest = module_path[dots:].replace(".", "/")
        base = importer_dir
        for _ in range(dots - 1):
            base = base.parent
        return _module_file((base / rest).as_posix(), ctx.path_set) if rest else None

    # Absolute import. A script (a file whose directory is not a package) has
    # its own directory first on sys.path, so its siblings shadow everything,
    # the stdlib included. A package member has no such entry: since Python 3
    # an absolute ``import types`` inside ``pkg/`` is the stdlib, never
    # ``pkg/types.py``.
    dotted = module_path.replace(".", "/")
    if not _is_package_dir(importer_dir.as_posix(), ctx.path_set):
        base = "" if importer_dir.as_posix() == "." else f"{importer_dir.as_posix()}/"
        hit = _module_file(f"{base}{dotted}", ctx.path_set)
        if hit:
            return hit
    return _resolve_in_repo(module_path, dotted, ctx)


def _resolve_in_repo(module_path: str, dotted: str, ctx: ResolverContext) -> str | None:
    """The repo file importable as *module_path* from a repo import root, or None."""
    # The source-root-aware module index maps the fully-qualified dotted name
    # to its defining file however deeply the source root is nested (``src/``,
    # ``packages/*/src/``, …).
    hit = _module_index(ctx).get(module_path)
    if hit:
        return hit

    # The repo root and a single ``src`` are import roots even for a loose
    # module the index cannot name.
    return (
        _module_file(dotted, ctx.path_set)
        or _module_file(f"src/{dotted}", ctx.path_set)
        or _suffix_match(module_path, dotted, ctx)
    )


def _suffix_match(module_path: str, dotted: str, ctx: ResolverContext) -> str | None:
    """The best file whose path ends with the whole dotted path, or None."""
    # A dotted path the index cannot name, typically a PEP 420 namespace
    # package (``ns/pkg/mod.py`` with no ``ns/__init__.py``) under a nested
    # root: accept a file whose path *ends* with the whole dotted path, below a
    # directory that is not itself a package (so it can be a sys.path entry).
    # A bare top-level name is never matched this way: ``import pydantic`` is
    # not ``utils/pydantic.py``, and matching one component is a guess. Nor is
    # a stdlib name, which precedes every root but a script's own directory:
    # ``os.path`` is not ``compat/os/path.py``.
    if "." not in module_path or module_path.split(".")[0] in _STDLIB_NAMES:
        return None
    matches = []
    for c in ctx.stem_map.get(module_path.rsplit(".", 1)[-1].lower(), ()):
        for suffix in (f"/{dotted}.py", f"/{dotted}/__init__.py"):
            if c.endswith(suffix) and not _is_package_dir(c[: -len(suffix)], ctx.path_set):
                matches.append(c)
    return min(matches, key=_index_priority) if matches else None


def resolve_python_import_all(
    imp: Import, importer_path: str, ctx: ResolverContext
) -> tuple[str, ...]:
    """Resolve a Python import, fanning edges out to submodule files.

    ``from pkg import a, b`` resolves to ``pkg/__init__.py`` only, so when an
    imported name is itself a submodule file the submodule never gains an
    inbound edge and the dead-code analyzer reports it unreachable (#666) —
    FastAPI apps wiring routers through their package are the canonical hit.
    Probe every imported name against the package directory and emit the
    submodule targets, plus the package itself unless every name was a
    submodule. The bare-relative form
    (``from . import a, b``) is already split upstream by
    ``expand_bare_relative_imports``; this covers the named-package forms,
    both absolute and relative.
    """
    # A package ``__init__.py`` importing its own submodules (``from pkg import
    # a`` inside ``pkg/__init__.py``) still fans out; only the self edge goes.
    base = _resolve_file(imp.module_path, importer_path, ctx)
    if base is None:
        external = _external(imp.module_path, ctx)
        return (external,) if external else ()
    targets = [base]
    names = imp.imported_names or []
    # Whether some imported name is not a submodule, so it is read from the
    # package ``__init__.py`` itself.
    needs_base = True
    if base.endswith("__init__.py") and names and names != ["*"]:
        needs_base = False
        index = _module_index(ctx)
        base_dir = Path(base).parent.as_posix()
        for name in names:
            if not name or name == "*" or "." in name:
                needs_base = True
                continue
            # Source-root-aware index first (absolute imports), then direct
            # sibling probes, which also cover the relative form.
            hit = None
            if not imp.is_relative:
                hit = index.get(f"{imp.module_path}.{name}")
            if hit is None:
                hit = _module_file(f"{base_dir}/{name}", ctx.path_set)
            if hit is None or hit == base:
                needs_base = True
            else:
                targets.append(hit)
                # Point the binding for this name at the submodule file, not
                # the package ``__init__.py``. ``from pkg import submodule``
                # binds ``submodule`` to ``pkg/submodule.py``, so a later
                # ``submodule.symbol()`` call must resolve against that file
                # (#1193). Without this the binding's source_file stays the
                # package init, which declares nothing, and the call is missed.
                for binding in imp.bindings:
                    if (binding.exported_name or binding.local_name) == name:
                        binding.source_file = hit
                        break
    # ``from pkg import a, b`` where both are submodules reads nothing from
    # ``pkg/__init__.py``; an edge there would claim a dependency no name
    # carries, just as ``from pkg.a import x`` draws none to the package.
    if not needs_base and len(targets) > 1:
        targets.remove(base)
    return tuple(t for t in dict.fromkeys(targets) if t != importer_path)
