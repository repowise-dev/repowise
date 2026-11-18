"""Go modules: every ``go.mod`` the resolver's :class:`GoPackageIndex` was built from.

A unit is a module, not a package directory. Deps are the ``require``
entries naming another module of the repo.
"""

from __future__ import annotations

from repowise.core.build_units.adapters import Found
from repowise.core.build_units.model import BuildUnit
from repowise.core.ingestion.external_systems.go import scan_go_mod
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.go import _read_module_directive
from repowise.core.ingestion.resolvers.go_workspace import get_or_build_go_index


def units(ctx: ResolverContext) -> Found:
    if ctx.repo_path is None or not any(path.endswith(".go") for path in ctx.path_set):
        return Found([])
    index = get_or_build_go_index(ctx)

    # ``index.modules`` also carries local ``replace`` targets, keyed by the
    # replaced import path; a module is a directory holding a ``go.mod``,
    # named by its own ``module`` directive.
    modules: list[tuple[str, str, list[str]]] = []
    for module_dir in sorted({module_dir for module_dir, _path in index.modules}):
        go_mod = ctx.repo_path / module_dir / "go.mod"
        module_path = _read_module_directive(go_mod) if go_mod.is_file() else None
        if not module_path:
            continue
        try:
            text = go_mod.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        requires, _replaced = scan_go_mod(text)
        modules.append((module_dir, module_path, [name for name, _v, _i in requires]))

    paths = {module_path for _dir, module_path, _req in modules}
    out: list[tuple[BuildUnit, tuple[str, ...]]] = []
    for module_dir, module_path, requires in modules:
        unit = BuildUnit(
            id=f"go:{module_path}",
            ecosystem="go",
            name=module_path,
            root=module_dir,
            manifest=f"{module_dir}/go.mod" if module_dir else "go.mod",
            source="go-mod",
            deps=tuple(f"go:{name}" for name in requires if name in paths),
        )
        out.append((unit, ()))
    return Found(out)
