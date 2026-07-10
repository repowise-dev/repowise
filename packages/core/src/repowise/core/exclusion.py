"""Query-time exclusion: compile a repo's exclude rules and filter rows.

Excluded files are skipped at ingest time, but DB rows may predate an
``exclude_patterns`` / gitignore change, so read paths (MCP tools, editor-file
generation) filter again at query time. This module is the single home for
that logic; ``repowise.server.mcp_server._helpers`` delegates here.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def build_exclude_spec(repo_path: Path | str) -> Any:
    """Compile the repo's exclusion rules into a PathSpec, or ``None``.

    Unions ``.repowise/config.yaml`` ``exclude_patterns`` with the repo's
    gitignore stack (``.gitignore`` + ``.git/info/exclude``). Indexes built
    before the traverser honoured ``info/exclude`` still contain rows for
    local-only scratch dirs; filtering them at query time keeps those paths
    out of generated output without forcing a reindex.
    """
    import pathspec

    from repowise.core.repo_config import load_repo_config

    patterns = list(load_repo_config(repo_path).get("exclude_patterns") or [])
    root = Path(repo_path)
    for ignore_file in (root / ".gitignore", root / ".git" / "info" / "exclude"):
        try:
            if ignore_file.exists():
                patterns.extend(
                    ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                )
        except OSError:
            continue
    if not patterns:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", patterns)


def is_excluded(path: str | None, spec: Any) -> bool:
    """True if *path* matches *spec* (None spec or path -> not excluded)."""
    return bool(spec is not None and path and spec.match_file(path))


def decision_is_excluded(decision_row: Any, spec: Any) -> bool:
    """True when a DecisionRecord is anchored entirely in excluded paths.

    Decision mining can predate an exclude_patterns / info-exclude change, so
    records anchored in vendored trees (a checked-in venv's site-packages, a
    local-only scratch dir) survive in the DB and would surface as the repo's
    "top decisions". A record whose affected files are ALL excluded is noise;
    one with no affected files at all is kept (nothing to judge it by).
    Paths are normalized to forward slashes — ``affected_files_json`` stores
    OS-native separators.
    """
    if spec is None:
        return False
    try:
        affected = json.loads(getattr(decision_row, "affected_files_json", None) or "[]")
    except (ValueError, TypeError):
        return False
    paths = [p.replace("\\", "/") for p in affected if isinstance(p, str) and p]
    if not paths:
        return False
    return all(is_excluded(p, spec) for p in paths)
