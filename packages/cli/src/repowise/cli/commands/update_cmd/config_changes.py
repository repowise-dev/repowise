"""Which parts of an update a config.yaml / health-rules.json edit invalidates."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ConfigChanges:
    """The stored config fingerprints against the live ones, split by area."""

    prev_fingerprint: str | None
    curr_fingerprint: str
    curr_dependency_fps: dict
    changed_dependencies: set[str]
    changed: bool
    traversal_changed: bool
    git_changed: bool
    health_changed: bool
    generation_changed: bool
    rebuild_required: bool


def classify_config_changes(
    repo_path: Path,
    state: dict,
    cfg: dict[str, Any],
    *,
    defer_generation: bool = False,
) -> ConfigChanges:
    """Compare the stored config fingerprints with the live ones (missing = unchanged)."""
    from repowise.cli.helpers import config_fingerprint
    from repowise.core.repo_config import (
        changed_config_dependencies,
        config_dependency_fingerprints,
    )

    prev_config_fp = state.get("config_fingerprint")
    curr_config_fp = config_fingerprint(repo_path)
    fingerprint_changed = prev_config_fp is not None and prev_config_fp != curr_config_fp
    curr_dependency_fps = config_dependency_fingerprints(repo_path, config=cfg)
    changed_dependencies = (
        changed_config_dependencies(
            state.get("config_dependency_fingerprints"), curr_dependency_fps
        )
        if isinstance(state.get("config_dependency_fingerprints"), dict)
        else None
    )
    dependency_changes = changed_dependencies or set()
    # Deferred docs runs may have already applied the index-facing config and
    # stamped its broad fingerprint while intentionally leaving the generation
    # bucket for a later docs run. That residual bucket must not trigger another
    # graph rebuild while docs are still deferred.
    deferred_generation_only = (
        defer_generation and not fingerprint_changed and dependency_changes == {"generation"}
    )
    config_changed = (fingerprint_changed or bool(dependency_changes)) and not deferred_generation_only
    legacy_config_change = fingerprint_changed and changed_dependencies is None
    # Each area is invalidated by its own dependencies and by every area it
    # reads from: git reads the traversal, health and generation read both.
    traversal_config_changed = legacy_config_change or bool(
        {"traversal", "other"} & dependency_changes
    )
    git_config_changed = traversal_config_changed or "git_history" in dependency_changes
    health_config_changed = git_config_changed or bool({"health", "other"} & dependency_changes)
    generation_config_changed = git_config_changed or "generation" in dependency_changes
    if deferred_generation_only:
        generation_config_changed = False
    config_rebuild_required = config_changed and bool(
        legacy_config_change
        or dependency_changes - {"state_only"}
    )
    return ConfigChanges(
        prev_fingerprint=prev_config_fp,
        curr_fingerprint=curr_config_fp,
        curr_dependency_fps=curr_dependency_fps,
        changed_dependencies=dependency_changes,
        changed=config_changed,
        traversal_changed=traversal_config_changed,
        git_changed=git_config_changed,
        health_changed=health_config_changed,
        generation_changed=generation_config_changed,
        rebuild_required=config_rebuild_required,
    )
