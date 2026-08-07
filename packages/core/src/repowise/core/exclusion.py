"""Query-time exclusion: compile a repo's exclude rules and filter rows.

Excluded files are skipped at ingest time, but DB rows may predate an
``exclude_patterns`` / gitignore change, so read paths (MCP tools, editor-file
generation) filter again at query time. This module is the single home for
that logic; ``repowise.server.mcp_server._helpers`` delegates here.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


def _gitignore_files(root: Path) -> tuple[Path, ...]:
    """The gitignore-stack files unioned into the spec."""
    return (root / ".gitignore", root / ".git" / "info" / "exclude")


def _rule_files(root: Path) -> tuple[Path, ...]:
    """Every file whose contents define the spec, config file included.

    Read from ``repo_config`` rather than spelled out again: a stamp that
    missed the config file would cache a spec across an ``exclude_patterns``
    edit and keep serving rows the user just excluded.
    """
    from repowise.core.repo_config import CONFIG_FILENAME, get_repowise_dir

    return (get_repowise_dir(root) / CONFIG_FILENAME, *_gitignore_files(root))


def _rules_stamp(root: Path) -> tuple[int, ...]:
    """mtime of each rule source, ``0`` when absent — the cache invalidator."""
    stamp = []
    for path in _rule_files(root):
        try:
            stamp.append(path.stat().st_mtime_ns)
        except OSError:
            stamp.append(0)
    return tuple(stamp)


@lru_cache(maxsize=8)
def _compile_spec(root_key: str, _stamp: tuple[int, ...]) -> Any:
    """Compile and cache one repo's spec. Keyed by root + rule-file mtimes."""
    import pathspec

    from repowise.core.repo_config import load_repo_config

    root = Path(root_key)
    patterns = list(load_repo_config(root).get("exclude_patterns") or [])
    for ignore_file in _gitignore_files(root):
        try:
            if ignore_file.exists():
                patterns.extend(
                    ignore_file.read_text(encoding="utf-8", errors="ignore").splitlines()
                )
        except OSError:
            continue
    if not patterns:
        return None
    spec = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
    # Per-path decision memo, carried on the spec so it lives exactly as long as
    # the compiled spec does. ``match_file`` is a regex sweep over every pattern;
    # a repo-wide read filters the same few thousand paths over and over, both
    # within one call (findings outnumber files ~3:1) and across calls.
    spec._repowise_memo = {}  # type: ignore[attr-defined]
    return spec


def build_exclude_spec(repo_path: Path | str) -> Any:
    """Compile the repo's exclusion rules into a PathSpec, or ``None``.

    Unions ``.repowise/config.yaml`` ``exclude_patterns`` with the repo's
    gitignore stack (``.gitignore`` + ``.git/info/exclude``). Indexes built
    before the traverser honoured ``info/exclude`` still contain rows for
    local-only scratch dirs; filtering them at query time keeps those paths
    out of generated output without forcing a reindex.

    Cached per repo root and invalidated by the rule files' mtimes: every MCP
    tool builds this on each request, and compiling it is pure overhead when
    nothing changed.
    """
    root = Path(repo_path)
    try:
        root_key = str(root.resolve())
    except OSError:
        root_key = str(root)
    return _compile_spec(root_key, _rules_stamp(root))


def is_excluded(path: str | None, spec: Any) -> bool:
    """True if *path* matches *spec* (None spec or path -> not excluded)."""
    if spec is None or not path:
        return False
    memo = getattr(spec, "_repowise_memo", None)
    if memo is None:
        return bool(spec.match_file(path))
    cached = memo.get(path)
    if cached is None:
        cached = memo[path] = bool(spec.match_file(path))
    return cached


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
