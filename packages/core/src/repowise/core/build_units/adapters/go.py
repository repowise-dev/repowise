"""Go modules: every ``go.mod`` the resolver's :class:`GoPackageIndex` was built from.

A unit is a module, not a package directory. Deps are the ``require``
entries naming another module of the repo. A module path taken twice keeps
the first directory in sorted order; the later directory is left to the
manifest fallback. A ``replace`` that points at that later directory links
its ``manifest:`` unit rather than the module that kept the path. A
``replace`` outside the repo links nothing.
"""

from __future__ import annotations

import structlog

from repowise.core.build_units.adapters import Found, linked_unit_id
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.external_systems.go import scan_go_mod
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go import (
    _read_local_replacements,
    _read_module_directive,
)
from repowise.core.ingestion.resolvers.go_workspace import get_or_build_go_index

log = structlog.get_logger(__name__)


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(path.endswith(".go") for path in ctx.path_set):
        return Found([])
    index = get_or_build_go_index(ctx)

    # ``index.modules`` also carries local ``replace`` targets, keyed by the
    # replaced import path; a module is a directory holding a ``go.mod``,
    # named by its own ``module`` directive.
    modules: list[tuple[str, str, list[str], set[str], list[tuple[str, str]]]] = []
    for module_dir in sorted({module_dir for module_dir, _path in index.modules}):
        go_mod = ctx.repo_path / module_dir / "go.mod"
        module_path = _read_module_directive(go_mod) if go_mod.is_file() else None
        if not module_path:
            continue
        try:
            text = go_mod.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        requires, replaced = scan_go_mod(text)
        local_replacements = _read_local_replacements(go_mod, ctx.repo_path)
        modules.append(
            (
                module_dir,
                module_path,
                [name for name, _v, _i in requires],
                replaced,
                local_replacements,
            )
        )

    # A module path taken twice keeps its first directory. The later one is
    # not claimed, so the fallback gives it a manifest unit and a replace
    # pointing at it does not resolve to the module that kept the path.
    by_root: dict[str, str] = {}
    dropped: set[str] = set()
    taken: set[str] = set()
    kept: list[tuple[str, str, list[str], set[str], list[tuple[str, str]]]] = []
    for module in modules:
        module_dir, module_path = module[0], module[1]
        uid = f"go:{module_path}"
        if uid in taken:
            log.warning("build_units_duplicate_id", unit_id=uid, root=module_dir)
            dropped.add(module_dir)
            continue
        taken.add(uid)
        by_root[module_dir] = uid
        kept.append(module)

    paths = {module_path for _dir, module_path, _req, _rep, _lrep in kept}

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for module_dir, module_path, requires, replaced, local_replacements in kept:
        alias_to_module = {
            imp_path: linked
            for target_dir, imp_path in local_replacements
            if (linked := linked_unit_id(target_dir, by_root, dropped))
        }

        resolved_deps: set[str] = set()
        own = f"go:{module_path}"
        for name in (*requires, *replaced):
            if name in alias_to_module:
                target = alias_to_module[name]
            elif name not in replaced and name in paths:
                target = f"go:{name}"
            else:
                continue
            if target != own:
                resolved_deps.add(target)

        unit = BuildUnit(
            id=f"go:{module_path}",
            ecosystem="go",
            name=module_path,
            root=module_dir,
            manifest=f"{module_dir}/go.mod" if module_dir else "go.mod",
            source="go-mod",
            deps=tuple(sorted(resolved_deps)),
        )
        out.append((unit, ()))
    return Found(out)
