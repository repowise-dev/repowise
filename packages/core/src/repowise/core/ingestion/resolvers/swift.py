"""Swift import resolution."""

from __future__ import annotations

from pathlib import PurePosixPath

from .context import ResolverContext
from .swift_spm import resolve_via_swift_targets


def resolve_swift_import(module_path: str, importer_path: str, ctx: ResolverContext) -> str | None:
    """Resolve a Swift import to a repo-relative file path."""
    parts = module_path.split(".")
    local = parts[-1]

    # Package.swift target → directory mapping (SPM is the dominant way Swift
    # apps are structured today; consult before stem matching).
    spm_match = resolve_via_swift_targets(module_path, ctx)
    if spm_match is not None:
        return spm_match

    # Try stem lookup
    result = ctx.stem_lookup(local.lower())
    if result and result.endswith(".swift"):
        return result

    # Look for directory matching module name
    for p in ctx.path_set:
        if p.endswith(".swift"):
            parent_name = PurePosixPath(p).parent.name.lower()
            if parent_name == module_path.lower():
                return p

    return ctx.add_external_node(module_path)
