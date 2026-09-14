"""Canonical, backward-compatible projection of an index's actual scope.

Writers persist this object in ``state.json:index_scope``.  Readers always go
through :func:`resolve_index_scope`, which also gives legacy state a stable,
conservative shape: missing evidence is ``unknown``/``None``, never inferred as
complete from a configured limit or from the absence of a finding.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

INDEX_SCOPE_VERSION = 1


def load_index_scope(repo_path: str | Path) -> dict[str, Any] | None:
    """Load a repository's scope; return ``None`` when no readable state exists."""
    repowise_dir = Path(repo_path) / ".repowise"
    try:
        state = json.loads((repowise_dir / "state.json").read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError):
        return None

    try:
        import yaml
    except ImportError:
        return resolve_index_scope(state)
    try:
        config = yaml.safe_load((repowise_dir / "config.yaml").read_text(encoding="utf-8")) or {}
    except (OSError, TypeError, ValueError, yaml.YAMLError):
        config = {}
    return resolve_index_scope(state, config)


def _choice(value: Any, choices: set[str]) -> str:
    return value if isinstance(value, str) and value in choices else "unknown"


def _number(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None


def resolve_index_scope(
    state: Mapping[str, Any] | None,
    config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the one machine-readable scope contract for every output surface."""
    state = state if isinstance(state, Mapping) else {}
    config = config if isinstance(config, Mapping) else {}
    stored = state.get("index_scope")
    scope = deepcopy(stored) if isinstance(stored, Mapping) else {}

    explicit_docs_mode = state.get("docs_mode")
    provenance = {
        "none": "none",
        "deterministic": "template",
        "llm": "model",
    }.get(explicit_docs_mode, "unknown")

    pages = scope.get("file_pages") if isinstance(scope.get("file_pages"), Mapping) else {}
    configured_cap = pages.get("configured_cap")
    if configured_cap is None and "max_file_pages" in config:
        configured_cap = _number(config.get("max_file_pages"))

    provider = scope.get("provider") if isinstance(scope.get("provider"), Mapping) else {}
    search = scope.get("search") if isinstance(scope.get("search"), Mapping) else {}
    analysis = scope.get("analysis") if isinstance(scope.get("analysis"), Mapping) else {}
    upgrade = state.get("full_upgrade")
    if not isinstance(upgrade, Mapping):
        upgrade = scope.get("upgrade") if isinstance(scope.get("upgrade"), Mapping) else {}

    run_mode = _choice(state.get("run_mode", scope.get("run_mode")), {"fast", "standard"})
    git_tier = _choice(state.get("git_tier", scope.get("git_tier")), {"essential", "full"})
    if isinstance(stored, Mapping):
        provenance = _choice(
            scope.get("content_provenance", provenance), {"none", "template", "model"}
        )

    unavailable = analysis.get("unavailable", [])
    degraded = state.get("degraded", [])
    if isinstance(unavailable, list) and isinstance(degraded, list):
        unavailable = [*unavailable, *degraded]
    skipped = analysis.get("skipped", [])
    return {
        "version": INDEX_SCOPE_VERSION,
        "run_mode": run_mode,
        "content_provenance": provenance,
        "git_tier": git_tier,
        "git_commit_cap": _number(scope.get("git_commit_cap", config.get("commit_limit"))),
        "git_history_coverage": (
            deepcopy(state.get("git_history_coverage"))
            if isinstance(state.get("git_history_coverage"), Mapping)
            else deepcopy(scope.get("git_history_coverage"))
            if isinstance(scope.get("git_history_coverage"), Mapping)
            else None
        ),
        "file_pages": {
            "configured_cap": configured_cap,
            "effective_cap": _number(pages.get("effective_cap")),
            "eligible": _number(pages.get("eligible")),
            "generated": _number(pages.get("generated")),
            "omitted": _number(pages.get("omitted")),
        },
        "analysis": {
            "unavailable": sorted({str(v) for v in unavailable})
            if isinstance(unavailable, list)
            else [],
            "skipped": sorted({str(v) for v in skipped}) if isinstance(skipped, list) else [],
        },
        "upgrade": {
            "status": _choice(
                upgrade.get("status"),
                {"pending", "running", "resumable", "failed", "complete", "not_applicable"},
            ),
            "retryable": upgrade.get("retryable")
            if isinstance(upgrade.get("retryable"), bool)
            else None,
            "completed_stages": list(upgrade.get("completed_stages", []))
            if isinstance(upgrade.get("completed_stages"), list)
            else [],
            "next_stage": _string(upgrade.get("next_stage")),
        },
        "provider": {
            "name": _string(provider.get("name", state.get("provider"))),
            "model": _string(provider.get("model", state.get("model"))),
            "embedder": _string(provider.get("embedder", config.get("embedder"))),
            "reused": provider.get("reused") if isinstance(provider.get("reused"), bool) else None,
            "model_cost_possible": provider.get("model_cost_possible")
            if isinstance(provider.get("model_cost_possible"), bool)
            else None,
        },
        "search": {
            "full_text": _choice(search.get("full_text"), {"available", "unavailable", "pending"}),
            "semantic": _choice(search.get("semantic"), {"available", "unavailable", "pending"}),
            "next_command": _string(search.get("next_command")),
        },
    }


def stamp_index_scope(
    state: dict[str, Any],
    config: Mapping[str, Any] | None = None,
    **updates: Any,
) -> dict[str, Any]:
    """Merge *updates* into and persist the canonical projection in ``state``."""
    scope = resolve_index_scope(state, config)
    for key, value in updates.items():
        if isinstance(value, Mapping) and isinstance(scope.get(key), Mapping):
            scope[key] = {**scope[key], **value}
        else:
            scope[key] = value
    state["index_scope"] = scope
    return scope


def file_page_scope(
    *, configured_cap: int | None, eligible: int, generated_pages: list[Any] | None
) -> dict[str, int | None]:
    """Build configured/effective/achieved file-page figures for one run."""
    from repowise.core.generation.selection import auto_file_page_cap

    generated = sum(1 for page in (generated_pages or []) if page.page_type == "file_page")
    effective = configured_cap
    if configured_cap is None:
        effective = auto_file_page_cap(eligible)
    elif configured_cap == 0:
        effective = None
    return {
        "configured_cap": configured_cap,
        "effective_cap": effective,
        "eligible": eligible,
        "generated": generated,
        "omitted": max(0, eligible - generated),
    }
