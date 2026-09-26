"""Target resolution for get_health: naming every requested target that matched nothing."""

from __future__ import annotations

from pathlib import Path
from typing import Any


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

    A dropped target is otherwise indistinguishable from a clean file: an
    empty ``findings`` list reads as "this file is healthy", which is the most
    damaging default this tool can have. The reason is the actionable part —
    ``not_indexed`` means run ``repowise update``, ``no_such_path`` means the
    target was a typo, ``excluded`` means the repo config drops it on purpose,
    and ``not_measured`` means the row is indexed but carries no stored split
    for the reading ``counts`` asked for. That last one is why the projection
    is passed in rather than inferred: an indexed file it could not answer for
    would otherwise read as ``not_indexed`` and send the caller to run an
    update that changes nothing.
    """
    out: list[dict[str, str]] = []
    for t in file_targets:
        if t in resolved_paths:
            continue
        if t in excluded_paths:
            reason = "excluded"
        elif t in unscored_paths:
            reason = "not_measured"
        else:
            try:
                on_disk = (Path(repo_root) / t).exists()
            except (OSError, ValueError):
                on_disk = False
            reason = "not_indexed" if on_disk else "no_such_path"
        out.append({"target": t, "reason": reason})
    out.extend(
        {"target": f"module:{name}", "reason": "no_such_module"}
        for name in module_targets
        if name not in matched_modules
    )
    return out
