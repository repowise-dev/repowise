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
    changed: bool
    traversal_changed: bool
    git_changed: bool
    health_changed: bool
    generation_changed: bool
    rebuild_required: bool


def classify_config_changes(repo_path: Path, state: dict, cfg: dict[str, Any]) -> ConfigChanges:
    """Compare the stored config fingerprints with the live ones (missing = unchanged)."""
    from repowise.cli.helpers import config_fingerprint
    from repowise.core.repo_config import (
        changed_config_dependencies,
        config_dependency_fingerprints,
    )

    prev_config_fp = state.get("config_fingerprint")
    curr_config_fp = config_fingerprint(repo_path)
    config_changed = prev_config_fp is not None and prev_config_fp != curr_config_fp
    curr_dependency_fps = config_dependency_fingerprints(repo_path, config=cfg)
    changed_dependencies = (
        changed_config_dependencies(
            state.get("config_dependency_fingerprints"), curr_dependency_fps
        )
        if config_changed
        else set()
    )
    legacy_config_change = config_changed and changed_dependencies is None
    dependency_changes = changed_dependencies or set()
    # Each area is invalidated by its own dependencies and by every area it
    # reads from: git reads the traversal, health and generation read both.
    traversal_config_changed = legacy_config_change or bool(
        {"traversal", "other"} & dependency_changes
    )
    git_config_changed = traversal_config_changed or "git_history" in dependency_changes
    health_config_changed = git_config_changed or bool({"health", "other"} & dependency_changes)
    generation_config_changed = git_config_changed or "generation" in dependency_changes
    config_rebuild_required = config_changed and bool(
        legacy_config_change
        or dependency_changes - {"state_only"}
    )
    return ConfigChanges(
        prev_fingerprint=prev_config_fp,
        curr_fingerprint=curr_config_fp,
        curr_dependency_fps=curr_dependency_fps,
        changed=config_changed,
        traversal_changed=traversal_config_changed,
        git_changed=git_config_changed,
        health_changed=health_config_changed,
        generation_changed=generation_config_changed,
        rebuild_required=config_rebuild_required,
    )
