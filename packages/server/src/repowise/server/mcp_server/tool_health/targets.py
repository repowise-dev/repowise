"""Target resolution for get_health: expanding ``module:`` targets, naming misses."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.core.analysis.health.complexity.languages import NO_DIALECT_STATUS
from repowise.server.repo_paths import missing_path_hint


def _expand_module_targets(
    metrics: list[Any], module_targets: list[str], file_targets: list[str]
) -> tuple[list[str], set[str]]:
    """Add every file of each named module to the targets; report which matched.

    A name matches the files of a top-level module and the files under any
    directory it names, so ``module:httpx/_transports`` reaches the files of
    that subsystem and not just the whole ``httpx`` module. The directory
    boundary is part of the match, so ``httpx/_trans`` does not pick up
    ``httpx/_transports``.

    The path list is returned untouched (not re-sorted) when no module was
    named, so a plain file-target call keeps the caller's order.
    """
    matched_modules: set[str] = set()
    if not module_targets:
        return file_targets, matched_modules
    expanded = list(file_targets)
    normalized = {raw: _normalize_module_name(raw) for raw in module_targets}
    for m in metrics:
        module = _normalize_module_name(m.module)
        matched_names = [
            raw
            for raw, name in normalized.items()
            if name and (module == name or _is_under(m.file_path, name))
        ]
        if matched_names:
            matched_modules.update(matched_names)
            expanded.append(m.file_path)
    return sorted(set(expanded)), matched_modules


def _normalize_module_name(name: Any) -> str:
    """Strip the ``./`` prefix and the separators a caller may wrap a name in."""
    if not isinstance(name, str):
        return ""
    normalized = name.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.strip("/")


def _is_under(file_path: str, module_name: str) -> bool:
    """Whether ``file_path`` sits in the directory ``module_name`` names."""
    return file_path.replace("\\", "/").startswith(module_name + "/")


def _unresolved_targets(
    *,
    file_targets: list[str],
    module_targets: list[str],
    matched_modules: set[str],
    resolved_paths: set[str],
    excluded_paths: set[str],
    unscored_paths: set[str],
    repo_root: Any,
    unanalysed_paths: set[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Name every requested target that produced no rows, with a reason.

    Otherwise an empty ``findings`` list reads as "this file is healthy". The
    reason is actionable: ``not_indexed`` (run ``repowise update``),
    ``no_such_path`` (a typo), ``excluded`` (repo config), ``not_measured``
    (indexed, but no stored split for the ``counts`` reading; passed in so it
    is not misreported as ``not_indexed``), ``language_not_supported`` (health
    has no dialect for the file's language, so it carries no score).
    """
    out = [
        {
            "target": t,
            **(
                {"reason": NO_DIALECT_STATUS}
                if t in unanalysed_paths
                else _miss_reason(t, excluded_paths, unscored_paths, repo_root)
            ),
        }
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
) -> dict[str, Any]:
    """Why one file target produced no row, plus a redirect when one is known."""
    if target in excluded_paths:
        return {"reason": "excluded"}
    if target in unscored_paths:
        return {"reason": "not_measured"}
    try:
        on_disk = (Path(repo_root) / target).exists()
    except (OSError, ValueError):
        on_disk = False
    if on_disk:
        return {"reason": "not_indexed"}
    root = str(repo_root) if repo_root else None
    return {"reason": "no_such_path", **missing_path_hint(root, target)}
