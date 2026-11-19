"""Go modules: every ``go.mod`` the resolver's :class:`GoPackageIndex` was built from.

A unit is a module, not a package directory. Deps are the ``require``
entries naming another module of the repo.
"""

from __future__ import annotations

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.external_systems.go import scan_go_mod
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go import (
    _read_local_replacements,
    _read_module_directive,
)
from repowise.core.ingestion.resolvers.go_workspace import get_or_build_go_index


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

    paths = {module_path for _dir, module_path, _req, _rep, _lrep in modules}
    dir_to_module = {
        module_dir: module_path for module_dir, module_path, _req, _rep, _lrep in modules
    }

    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for module_dir, module_path, requires, replaced, local_replacements in modules:
        alias_to_module = {
            imp_path: dir_to_module[target_dir]
            for target_dir, imp_path in local_replacements
            if target_dir in dir_to_module
        }

        resolved_deps: set[str] = set()
        for name in (*requires, *replaced):
            if name in alias_to_module:
                target = alias_to_module[name]
            elif name not in replaced and name in paths:
                target = name
            else:
                continue
            if target != module_path:
                resolved_deps.add(f"go:{target}")

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
