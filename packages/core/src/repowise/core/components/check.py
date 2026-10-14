"""Validate catalog files against the repository they describe.

Parse problems come from :func:`.catalog.read_catalog` (unknown kinds, wrong
apiVersion, unknown ``repowise.dev/*`` annotations, missing required spec
fields). This adds what needs the file list: globs that do not compile or
match no file, and files two declared components both claim (an error, or a
warning when one component's files nest wholly inside the other's).
"""

from __future__ import annotations

from collections.abc import Iterable

from repowise.core.components.catalog import (
    PATHS_ANNOTATION,
    Catalog,
    CatalogIssue,
    owner_group_name,
)
from repowise.core.components.resolve import compile_globs


def check_catalog(catalog: Catalog, file_paths: Iterable[str]) -> list[CatalogIssue]:
    """Every issue in *catalog*, parse-time ones first."""
    files = sorted(set(file_paths))
    issues = list(catalog.issues)
    matched_by: dict[str, set[str]] = {}
    for comp in catalog.components:
        label = f"Component:{comp.name}"
        if not comp.paths:
            issues.append(
                CatalogIssue(
                    "warning",
                    f"no {PATHS_ANNOTATION}: covers files only if a derived component "
                    "has the same name",
                    comp.declared_in,
                    label,
                )
            )
            continue
        _spec, bad = compile_globs(comp.paths)
        for glob in bad:
            issues.append(
                CatalogIssue("error", f"glob {glob!r} is not valid", comp.declared_in, label)
            )
        claimed = matched_by.setdefault(comp.name, set())
        for glob in comp.paths:
            if glob in bad:
                continue
            single, _ = compile_globs([glob])
            matched = {f for f in files if single.match_file(f)}
            if not matched:
                issues.append(
                    CatalogIssue(
                        "error", f"glob {glob!r} matches no files", comp.declared_in, label
                    )
                )
            claimed |= matched

    # Declaration order decides a shared file. A component nested wholly inside
    # another is a deliberate carve-out (warning); a partial overlap is an error.
    names = list(matched_by)
    for i, first in enumerate(names):
        for second in names[i + 1 :]:
            a, b = matched_by[first], matched_by[second]
            shared = a & b
            if not shared:
                continue
            nested = a < b or b < a
            issues.append(
                CatalogIssue(
                    "warning" if nested else "error",
                    f"{len(shared)} file(s) claimed by both {first!r} and {second!r}"
                    f"{' (one nests inside the other)' if nested else ''}; "
                    f"{first!r} is declared first and wins",
                    None,
                    f"Component:{second}",
                )
            )

    systems = {s.name for s in catalog.systems}
    groups = {g.name for g in catalog.groups}
    for comp in catalog.components:
        if comp.system and systems and comp.system not in systems:
            issues.append(
                CatalogIssue(
                    "warning",
                    f"spec.system {comp.system!r} is not a declared System",
                    comp.declared_in,
                    f"Component:{comp.name}",
                )
            )
        group = owner_group_name(comp.owner)
        if group and groups and group not in groups:
            issues.append(
                CatalogIssue(
                    "warning",
                    f"spec.owner {comp.owner!r} is not a declared Group",
                    comp.declared_in,
                    f"Component:{comp.name}",
                )
            )
    return issues
