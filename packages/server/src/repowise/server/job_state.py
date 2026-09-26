"""The ``state.json`` / ``config.yaml`` baseline a server job leaves for the CLI.

Later ``repowise update`` runs and server jobs read these files, so every job
kind records the commit it synced and, for a first index, the full baseline
``repowise init`` would have written.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog

from repowise.core.docs_mode import DocsMode, docs_mode_state_fields, resolve_docs_mode

logger = structlog.get_logger(__name__)


def _read_head_sha(repo_path: Path) -> str | None:
    import subprocess as _sp

    try:
        result = _sp.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception:
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _load_state(repo_path: Path) -> dict:
    state_path = repo_path / ".repowise" / "state.json"
    if state_path.is_file():
        return json.loads(state_path.read_text(encoding="utf-8"))
    return {}


def _save_state(repo_path: Path, state: dict) -> None:
    state_path = repo_path / ".repowise" / "state.json"
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def _stamp_last_sync_commit(repo_path: Path) -> None:
    """Record the synced HEAD so CLI incremental updates know the baseline."""
    head = _read_head_sha(repo_path)
    if not head:
        return
    state = _load_state(repo_path)
    state["last_sync_commit"] = head
    _save_state(repo_path, state)


def _persist_initial_index_state(
    repo_path: Path,
    *,
    llm_client: Any,
    docs_mode: DocsMode,
    docs_skip_reason: str | None,
    total_pages: int,
    wiki_style: str,
    exclude_patterns: list[str],
) -> None:
    """Write the ``repowise init`` baseline after a first API-driven index.

    Mirrors what the CLI persists at the end of ``init``: a complete
    ``state.json`` (sync baseline, docs flags, run mode, store-format stamp,
    config fingerprint) and a ``config.yaml`` recording the provider/model
    and style the run used, so later CLI ``update`` runs and server jobs
    resolve the same configuration.
    """
    from repowise.core.repo_config import config_fingerprint

    # config.yaml is written first: the fingerprint below covers it.
    _write_initial_repo_config(
        repo_path,
        llm_client=llm_client,
        wiki_style=wiki_style,
        exclude_patterns=exclude_patterns,
    )

    # ---- state.json ----
    state = _load_state(repo_path)
    head = _read_head_sha(repo_path)
    if head:
        state["last_sync_commit"] = head
    state.update(docs_mode_state_fields(docs_mode))
    if docs_skip_reason and docs_mode == "none":
        state["docs_skip_reason"] = docs_skip_reason
    state["run_mode"] = "standard"
    state["git_tier"] = "full"
    state["include_submodules"] = False
    state["total_pages"] = total_pages
    if llm_client is not None:
        state["provider"] = getattr(llm_client, "provider_name", "")
        state["model"] = getattr(llm_client, "model_name", "")
    _stamp_store_format(state, repo_path)
    state["config_fingerprint"] = config_fingerprint(repo_path)
    _save_state(repo_path, state)


def _write_initial_repo_config(
    repo_path: Path, *, llm_client: Any, wiki_style: str, exclude_patterns: list[str]
) -> None:
    """Record the provider/model, style and excludes the first index used.

    Values the repo's config.yaml already sets are kept; a write failure is
    logged, not fatal.
    """
    from repowise.core.generation.styles import DEFAULT_STYLE
    from repowise.core.repo_config import load_repo_config, save_repo_config

    config = load_repo_config(repo_path)
    if llm_client is not None:
        config["provider"] = getattr(llm_client, "provider_name", "") or config.get("provider")
        config["model"] = getattr(llm_client, "model_name", "") or config.get("model")
    if wiki_style and wiki_style != DEFAULT_STYLE and not config.get("wiki_style"):
        config["wiki_style"] = wiki_style
    if exclude_patterns and not config.get("exclude_patterns"):
        config["exclude_patterns"] = exclude_patterns
    try:
        save_repo_config(repo_path, config)
    except Exception:
        logger.debug("config_yaml_write_failed", repo_path=str(repo_path), exc_info=True)


def _stamp_store_format(state: dict, repo_path: Path) -> None:
    """Stamp the store-format version onto ``state``; best effort."""
    try:
        from importlib.metadata import version as _dist_version

        from repowise.core.upgrade import stamp as _stamp_store_version

        try:
            _pkg_version: str | None = _dist_version("repowise")
        except Exception:
            _pkg_version = None
        # This is the full-index persist (concept tree included), so stamp the
        # terminal store-format version rather than clamping at the reindex gate.
        _stamp_store_version(state, package_version=_pkg_version, full_index=True)
    except Exception:
        logger.debug("store_version_stamp_failed", repo_path=str(repo_path), exc_info=True)

def _persist_generate_job_state(
    repo_path: Path,
    *,
    total_pages: int,
    remaining_templates: int,
    pages_generated: int,
) -> None:
    """Persist a scoped generation's Git baseline and page metadata."""
    head = _read_head_sha(repo_path)
    state = _load_state(repo_path)
    if head:
        state["last_sync_commit"] = head
    state["total_pages"] = total_pages
    if remaining_templates == 0 and pages_generated and resolve_docs_mode(state) != "llm":
        state.update(docs_mode_state_fields("llm"))
    _save_state(repo_path, state)
