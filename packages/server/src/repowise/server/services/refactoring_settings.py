"""The refactoring code-generation switch, kept in the repo's ``.repowise/config.yaml``.

The provider and model are read-only: they come from the same resolver chat
uses, so the user configures a model once. Never carries a key.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def refactoring_settings(config: dict[str, Any], repo_id: str, repo_path: Path) -> dict[str, Any]:
    """The switch from *config* plus the model chat would build, or none."""
    from repowise.core.analysis.health.refactoring.llm import llm_enrichment_enabled
    from repowise.server.provider_config import get_configured_active_provider

    provider, model = get_configured_active_provider(repo_id=repo_id, repo_path=repo_path)
    return {"enabled": llm_enrichment_enabled(config), "provider": provider, "model": model}


def save_llm_enabled(repo_path: Path, enabled: bool) -> dict[str, Any]:
    """Write ``refactoring.llm.enabled`` and return the saved config.

    Round-trips through the loaded config so unrelated keys are preserved.
    """
    from repowise.core.repo_config import load_repo_config, save_repo_config

    config = load_repo_config(repo_path)
    refactoring = config.get("refactoring")
    if not isinstance(refactoring, dict):
        refactoring = {}
        config["refactoring"] = refactoring
    llm = refactoring.get("llm")
    if not isinstance(llm, dict):
        llm = {}
        refactoring["llm"] = llm
    llm["enabled"] = enabled

    save_repo_config(repo_path, config)
    return config


__all__ = ["refactoring_settings", "save_llm_enabled"]
