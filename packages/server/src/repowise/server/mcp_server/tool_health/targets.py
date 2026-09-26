"""Target resolution for get_health: expanding ``module:`` targets, naming misses."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def _expand_module_targets(
    metrics: list[Any], module_targets: list[str], file_targets: list[str]
) -> tuple[list[str], set[str]]:
    """Add every file of each named module to the targets; report which matched.

    The path list is returned untouched (not re-sorted) when no module was
    named, so a plain file-target call keeps the caller's order.
    """
    matched_modules: set[str] = set()
    if not module_targets:
        return file_targets, matched_modules
    expanded = list(file_targets)
    module_set = set(module_targets)
    for m in metrics:
        if m.module in module_set:
            matched_modules.add(m.module)
            expanded.append(m.file_path)
    return sorted(set(expanded)), matched_modules


def _unresolved_targets(
    *,
    file_targets: list[str],
    module_targets: list[str],
    matched_modules: set[str],
    resolved_paths: set[str],
    excluded_paths: set[str],
    unscored_paths: set[str],
    repo_root: Any,
) -> list[dict[str, str]]:
    """Name every requested target that produced no rows, with a reason.

    Otherwise an empty ``findings`` list reads as "this file is healthy". The
    reason is actionable: ``not_indexed`` (run ``repowise update``),
    ``no_such_path`` (a typo), ``excluded`` (repo config), ``not_measured``
    (indexed, but no stored split for the ``counts`` reading; passed in so it
    is not misreported as ``not_indexed``).
    """
    out = [
        {"target": t, "reason": _miss_reason(t, excluded_paths, unscored_paths, repo_root)}
        for t in file_targets
        if t not in resolved_paths
    ]
    out.extend(
        {"target": f"module:{name}", "reason": "no_such_module"}
        for name in module_targets
        if name not in matched_modules
    )
    return out


def _miss_reason(
    target: str, excluded_paths: set[str], unscored_paths: set[str], repo_root: Any
) -> str:
    """Why one file target produced no row."""
    if target in excluded_paths:
        return "excluded"
    if target in unscored_paths:
        return "not_measured"
    try:
        on_disk = (Path(repo_root) / target).exists()
    except (OSError, ValueError):
        on_disk = False
    return "not_indexed" if on_disk else "no_such_path"
