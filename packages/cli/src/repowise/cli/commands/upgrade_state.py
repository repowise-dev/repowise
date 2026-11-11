"""state.json bookkeeping for ``repowise update --full``.

The resumable transition (which stages finished, what runs next) and the final
flip to a full index. Kept apart from :mod:`upgrade_flow`, which does the work.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.cli.helpers import (
    load_state,
    resolve_max_file_pages,
    save_state,
    stamp_offered_slots,
)
from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
from repowise.core.decision_lanes import set_pending_decision_lanes
from repowise.core.docs_mode import docs_mode_state_fields
from repowise.core.index_scope import stamp_index_scope
from repowise.core.ingestion.git_indexer import GIT_HISTORY_VERSION, GIT_HISTORY_VERSION_KEY
from repowise.core.ingestion.git_indexer._constants import _DEFAULT_COMMIT_LIMIT

#: The resumable stages of a full upgrade, in run order.
_UPGRADE_STAGES = ["git_backfill", "generation", "search", "health"]


def _completed_stages(state: dict[str, Any]) -> list[str]:
    return list(
        state.get("full_upgrade", {}).get("completed_stages", [])
        if isinstance(state.get("full_upgrade"), dict)
        else []
    )


def _next_stage(stages: list[str]) -> str:
    return next((item for item in _UPGRADE_STAGES if item not in stages), "finalize")


def _running(stages: list[str], provider_block: dict[str, Any]) -> dict[str, Any]:
    """The transition record while the upgrade runs."""
    return {
        "status": "running",
        "retryable": True,
        "completed_stages": stages,
        "next_stage": _next_stage(stages),
        "provider": provider_block.get("name"),
        "model": provider_block.get("model"),
        "embedder": provider_block.get("embedder"),
    }


def _start_transition(
    repo_path: Path,
    state: dict[str, Any],
    cfg: dict[str, Any],
    provider_block: dict[str, Any],
) -> None:
    """Record the running transition before any backfill or model work.

    A killed process then leaves an honest resumable transition while the prior
    index_scope keeps describing the usable index.
    """
    state["full_upgrade"] = _running(_completed_stages(state), provider_block)
    stamp_index_scope(state, cfg, upgrade=state["full_upgrade"], provider=dict(provider_block))
    save_state(repo_path, state)


def _upgrade_checkpoint(repo_path: Path, cfg: dict[str, Any], provider_block: dict[str, Any]) -> Any:
    """A callback that records each completed stage in state.json."""

    def _checkpoint(stage: str, **fields: Any) -> None:
        current = load_state(repo_path)
        stages = _completed_stages(current)
        if stage not in stages:
            stages.append(stage)
        current["full_upgrade"] = _running(stages, provider_block)
        if fields.get("git_history_coverage") is not None:
            current["git_history_coverage"] = fields["git_history_coverage"]
        stamp_index_scope(current, cfg, upgrade=current["full_upgrade"])
        save_state(repo_path, current)

    return _checkpoint


def _mark_transition(repo_path: Path, cfg: dict[str, Any], **updates: Any) -> None:
    """Update the recorded transition after the upgrade stopped early."""
    current = load_state(repo_path)
    transition = current.get("full_upgrade", {})
    transition.update(**updates)
    current["full_upgrade"] = transition
    stamp_index_scope(current, cfg, upgrade=transition)
    save_state(repo_path, current)


def _unpack_upgrade_result(
    upgrade_result: tuple[Any, ...], cfg: dict[str, Any]
) -> tuple[list[Any], int, dict[str, Any]]:
    """Pages, page count and outcome; a 2-tuple from a wrapper gets an unknown outcome."""
    if len(upgrade_result) != 2:
        generated_pages, total_pages, outcome = upgrade_result
        return generated_pages, total_pages, outcome
    generated_pages, total_pages = upgrade_result
    outcome = {
        "git_history_coverage": None,
        "file_pages": {
            "configured_cap": resolve_max_file_pages(config=cfg),
            "effective_cap": None,
            "eligible": None,
            "generated": None,
            "omitted": None,
        },
        "search": {
            "full_text": "unknown",
            "semantic": "unknown",
            "next_command": None,
        },
        "embedding_error": None,
    }
    return generated_pages, total_pages, outcome


def _finish_upgrade_state(
    state: dict[str, Any],
    cfg: dict[str, Any],
    *,
    head: str | None,
    config: Any,
    commit_limit: Any,
    total_pages: int,
    outcome: dict[str, Any],
    provider_block: dict[str, Any],
) -> None:
    """Flip the persisted state to full.

    A model run records ``llm`` so later updates take the model path. A keyless
    run records documentation built from code and no provider, as a keyless
    ``init`` does.
    """
    written = bool(provider_block.get("name"))
    state["last_sync_commit"] = head
    state.update(docs_mode_state_fields("llm" if written else "deterministic"))
    state["git_tier"] = "full"
    state["run_mode"] = "standard"
    if outcome["git_history_coverage"] is not None:
        state["git_history_coverage"] = outcome["git_history_coverage"]
    state["total_pages"] = total_pages
    if written:
        # Record who wrote the pages, or `status` reports them as unknown.
        state["provider"] = provider_block["name"]
        state["model"] = provider_block["model"]
    else:
        # No model wrote these pages, whatever an earlier run recorded.
        state.pop("provider", None)
        state.pop("model", None)
    # Every onboarding slot was evaluated against whole-repo signals, so the
    # missing-slot notice has nothing left to report.
    stamp_offered_slots(state, enabled=config.enable_onboarding)
    if outcome.get("decision_lanes_pending") is not None:
        set_pending_decision_lanes(state, outcome["decision_lanes_pending"])
    # Health and the git backfill ran over the whole repo under the current
    # analyzer and history rules, so the next update need not redo them.
    state["health_analyzer_version"] = HEALTH_ANALYZER_VERSION
    state[GIT_HISTORY_VERSION_KEY] = GIT_HISTORY_VERSION
    state["full_upgrade"] = {
        "status": "complete",
        "retryable": False,
        "completed_stages": [*_UPGRADE_STAGES, "finalize"],
        "next_stage": None,
    }
    stamp_index_scope(
        state,
        cfg,
        run_mode="standard",
        content_provenance="model" if written else "template",
        git_tier="full",
        git_commit_cap=(commit_limit if isinstance(commit_limit, int) else _DEFAULT_COMMIT_LIMIT),
        git_history_coverage=outcome["git_history_coverage"],
        file_pages=outcome["file_pages"],
        test_run=False,
        analysis={"unavailable": [], "skipped": []},
        upgrade=state["full_upgrade"],
        provider=dict(provider_block),
        search=outcome["search"],
    )
