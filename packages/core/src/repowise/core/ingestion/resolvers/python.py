"""Python import resolution."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from ..languages.python_modules import (
    STDLIB_NAMES,
    _index_priority,
    _is_package_dir,
    build_python_module_index,
    dotted_module_for,
    source_roots,
)
from .context import ResolverContext

if TYPE_CHECKING:
    from ..models import Import

def _module_index(ctx: ResolverContext) -> dict[str, str]:
    """Source-root-aware dotted-module → path index, built once per context.

    Cached on the context (mirrors the lazy per-language index pattern used
    by the PHP / TS / Kotlin resolvers) so the index is computed a single
    time across every Python import in a build.
    """
    idx = getattr(ctx, "_python_module_index", None)
    if idx is None:
        idx = build_python_module_index(
            {path for path in ctx.path_set if _importable_path(path, ctx)}
        )
        ctx._python_module_index = idx
    return idx


def _source_roots(ctx: ResolverContext) -> set[str]:
    """The repository's Python import roots (:func:`source_roots`), built once per context."""
    roots = getattr(ctx, "_python_source_roots", None)
    if roots is None:
        roots = source_roots(ctx.path_set)
        ctx._python_source_roots = roots
    return roots


def _importable_path(path: str, ctx: ResolverContext) -> bool:
    """Inferred nested roots cannot shadow stdlib names; explicit roots can."""
    module = dotted_module_for(path, ctx.path_set)
    if not module or module.split(".")[0] not in STDLIB_NAMES:
        return True
    parts = Path(path).with_suffix("").parts
    depth = module.count(".") + 1 + (parts[-1] == "__init__")
    root = Path(*parts[:-depth])
    return root.as_posix() == "." or (
        root.name == "src" and not _is_package_dir(root.as_posix(), ctx.path_set)
    )


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
        rest = module_path.lstrip(".")
        stem = _relative_stem(module_path, importer_path)
        return _module_file(stem, ctx.path_set) if rest else None

    # Absolute import. A script (a file whose directory is not a package) has
    # its own directory first on sys.path, so its siblings shadow everything,
    # the stdlib included. A package member has no such entry: since Python 3
    # an absolute ``import types`` inside ``pkg/`` is the stdlib, never
    # ``pkg/types.py``.
    dotted = module_path.replace(".", "/")
    if not _is_package_dir(importer_dir.as_posix(), ctx.path_set):
        hit = _module_file(_under(importer_dir.as_posix(), dotted), ctx.path_set)
    else:
        hit = _resolve_in_own_root(module_path, dotted, importer_path, ctx)
    if hit:
        return hit
    for root in _enclosing_roots(module_path, importer_path, ctx):
        hit = _module_file(_under(root, dotted), ctx.path_set)
        if hit:
            return hit
    return _resolve_in_repo(module_path, dotted, ctx)


def _relative_stem(module_path: str, importer_path: str) -> str:
    """The repo path, without suffix, a relative *module_path* names."""
    dots = len(module_path) - len(module_path.lstrip("."))
    base = Path(importer_path).parent
    for _ in range(dots - 1):
        base = base.parent
    rest = module_path[dots:].replace(".", "/")
    return (base / rest).as_posix() if rest else base.as_posix()


def _enclosing_roots(module_path: str, importer_path: str, ctx: ResolverContext) -> list[str]:
    """Import roots above *importer_path* an absolute *module_path* may sit under, nearest first.

    A source root (:func:`source_roots`) above the importer is on ``sys.path``
    whenever the importer runs, so ``from middleware import jwt`` in
    ``backend/middleware/auth.py`` names ``backend/middleware/jwt.py`` when
    ``backend`` is a root, ``__init__.py`` or not (PEP 420). So is the parent of
    an enclosing directory the import names first: ``lipsync/app.py`` writing
    ``from lipsync.session import S`` is run with ``lipsync``'s parent on the
    path. A stdlib name is never looked up this way.
    """
    head = module_path.split(".")[0]
    if head in STDLIB_NAMES:
        return []
    roots = _source_roots(ctx)
    found: list[str] = []
    for parent in Path(importer_path).parents:
        here = _posix_dir(parent)
        if here in roots:
            found.append(here)
        if parent.name == head:
            found.append(_posix_dir(parent.parent))
    return list(dict.fromkeys(found))


def _posix_dir(path: Path) -> str:
    """*path* as a repo-relative POSIX directory, ``""`` for the repository root."""
    return "" if path.as_posix() == "." else path.as_posix()


def _under(root: str, rel: str) -> str:
    return rel if root in ("", ".") else f"{root}/{rel}"


def _resolve_in_own_root(
    module_path: str, dotted: str, importer_path: str, ctx: ResolverContext
) -> str | None:
    """*module_path* under the root the importer's own package is imported from."""
    # ``libs/x/pkg/mod.py`` is ``pkg.mod``, so ``libs/x`` is on sys.path
    # whenever it runs, and ``import helper`` there means ``libs/x/helper.py``.
    # A stdlib name stays external, as for any other package member.
    if module_path.split(".")[0] in STDLIB_NAMES:
        return None
    own = dotted_module_for(importer_path, ctx.path_set)
    if not own:
        return None
    parts = Path(importer_path).with_suffix("").parts
    depth = own.count(".") + 1 + (parts[-1] == "__init__")
    return _module_file(_under("/".join(parts[:-depth]), dotted), ctx.path_set)


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
    if "." not in module_path or module_path.split(".")[0] in STDLIB_NAMES:
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
        return _unresolved_targets(imp, importer_path, ctx)
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
            # Keep submodules in the package already resolved above; a merged
            # index can otherwise select another root's same-named package.
            hit = _module_file(f"{base_dir}/{name}", ctx.path_set)
            if hit is None and not imp.is_relative:
                hit = index.get(f"{imp.module_path}.{name}")
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


def _unresolved_targets(imp: Import, importer_path: str, ctx: ResolverContext) -> tuple[str, ...]:
    """Targets of an import no file defines: namespace package members, plus
    the external node unless every imported name was a member."""
    members, complete = _namespace_members(imp, importer_path, ctx)
    if members and complete:
        return members
    external = _external(imp.module_path, ctx)
    return (*members, external) if external else members


def _namespace_members(
    imp: Import, importer_path: str, ctx: ResolverContext
) -> tuple[tuple[str, ...], bool]:
    """The submodule files ``from <namespace package> import a, b`` names, and
    whether every imported name was one.

    A directory with no ``__init__.py`` is a PEP 420 namespace package: no
    file defines the package itself, and the only names it can give are its
    submodules. Each imported name that is a module file in the package
    directory becomes a target, and its binding points at that file.
    """
    names = [n for n in imp.imported_names or [] if n and n != "*" and "." not in n]
    dirs = _namespace_dirs(imp.module_path, importer_path, ctx) if names else []
    targets: list[str] = []
    complete = bool(names)
    for name in names:
        hit = _namespace_member(imp.module_path, name, dirs, ctx)
        if hit is None or hit == importer_path:
            complete = complete and hit == importer_path
            continue
        targets.append(hit)
        for binding in imp.bindings:
            if (binding.exported_name or binding.local_name) == name:
                binding.source_file = hit
                break
    return tuple(dict.fromkeys(targets)), complete


def _namespace_dirs(module_path: str, importer_path: str, ctx: ResolverContext) -> list[str]:
    """The directories a namespace package *module_path* may be, nearest first."""
    if module_path.startswith("."):
        return [_relative_stem(module_path, importer_path)] if module_path.lstrip(".") else []
    importer_dir = Path(importer_path).parent.as_posix()
    roots = [] if _is_package_dir(importer_dir, ctx.path_set) else [importer_dir]
    roots += _enclosing_roots(module_path, importer_path, ctx)
    dotted = module_path.replace(".", "/")
    return [_under(root, dotted) for root in dict.fromkeys(roots)]


def _namespace_member(
    module_path: str, name: str, dirs: list[str], ctx: ResolverContext
) -> str | None:
    """The module file *name* is in the namespace package, or None."""
    for d in dirs:
        hit = _module_file(f"{d}/{name}", ctx.path_set)
        if hit:
            return hit
    if module_path.startswith("."):
        return None
    return _module_index(ctx).get(f"{module_path}.{name}")
