"""Decision lanes a run without a model skipped, and the later run that fills them.

PRs, git history and code comments produce decisions only through a model, so a
keyless or ``--no-prose`` index records them as pending in ``state.json``. The
first run that has a model (``generate``, ``update --full``) runs exactly those
lanes through the index's own extraction entry point and clears them.
"""

from __future__ import annotations

import types
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: ``state.json`` key holding the lanes still waiting on a model.
PENDING_DECISION_LANES_KEY = "decision_lanes_pending"

#: Words for each model-only lane, in the order they are named to a user.
_LANE_WORDS: tuple[tuple[str, str], ...] = (
    ("pr", "PRs"),
    ("git_archaeology", "history"),
    ("comment", "comments"),
)

#: Pseudo page type the cost estimator prices one decision-extraction call as.
DECISION_CALL_TYPE = "decision_call"


def lanes_needing_model(repo_path: Path) -> tuple[str, ...]:
    """Enabled index sources that produce nothing without a model."""
    from repowise.core.analysis.decisions.policy import INDEX_SOURCE_KEYS, resolve_policy
    from repowise.core.repo_config import load_repo_config

    policy = resolve_policy(load_repo_config(repo_path)).policy
    return tuple(
        rt.key
        for rt in policy.runtime(provider_available=False)
        if rt.key in INDEX_SOURCE_KEYS and rt.status == "skipped_no_provider"
    )


def set_pending_decision_lanes(state: dict[str, Any], lanes: tuple[str, ...] | list[str]) -> None:
    """Store *lanes* as pending; an empty set removes the key."""
    if lanes:
        state[PENDING_DECISION_LANES_KEY] = list(lanes)
    else:
        state.pop(PENDING_DECISION_LANES_KEY, None)


def record_decision_lanes(state: dict[str, Any], repo_path: Path, *, ran_with_model: bool) -> None:
    """After a full extraction: pending is every model-only lane, or none with a model."""
    set_pending_decision_lanes(state, () if ran_with_model else lanes_needing_model(repo_path))


def pending_decision_lanes(state: Mapping[str, Any], repo_path: Path) -> tuple[str, ...]:
    """Pending lanes the repo's current decision policy still wants run."""
    stored = state.get(PENDING_DECISION_LANES_KEY)
    if not isinstance(stored, list) or not stored:
        return ()
    wanted = set(lanes_needing_model(repo_path))
    return tuple(key for key, _ in _LANE_WORDS if key in stored and key in wanted)


def lanes_label(lanes: tuple[str, ...] | list[str]) -> str:
    """``PRs, history and comments`` for the named lanes."""
    words = [word for key, word in _LANE_WORDS if key in lanes]
    if len(words) <= 1:
        return "".join(words)
    return f"{', '.join(words[:-1])} and {words[-1]}"


@dataclass(frozen=True)
class DecisionLanePlan:
    """The lanes a run will fill and the model calls they will make."""

    lanes: tuple[str, ...]
    calls: int

    def cost_plan(self) -> Any:
        from repowise.core.cost_estimator import PageTypePlan

        return PageTypePlan(page_type=DECISION_CALL_TYPE, count=self.calls, level=0)


def _extractor(repo_path: Path, inputs: Any, provider: Any = None) -> Any:
    from repowise.core.analysis.decision_extractor import DecisionExtractor
    from repowise.core.analysis.decisions.policy import resolve_policy
    from repowise.core.repo_config import load_repo_config

    return DecisionExtractor(
        repo_path=repo_path,
        provider=provider,
        graph=inputs.graph_builder.graph(),
        git_meta_map=inputs.git_meta_map,
        parsed_files=inputs.parsed_files,
        source_map=inputs.source_map,
        policy=resolve_policy(load_repo_config(repo_path)).policy,
    )


def plan_decision_lanes(
    state: Mapping[str, Any], repo_path: Path, inputs: Any
) -> DecisionLanePlan | None:
    """The pending lanes and their call count, or ``None`` when nothing is pending.

    *inputs* carries ``graph_builder``, ``git_meta_map``, ``parsed_files`` and
    ``source_map`` (a rehydrated repo).
    """
    lanes = pending_decision_lanes(state, repo_path)
    if not lanes:
        return None
    return DecisionLanePlan(lanes, _extractor(repo_path, inputs).planned_model_calls(lanes))


@dataclass(frozen=True)
class DecisionLaneOutcome:
    """What a lane run found, and the lanes still pending after it."""

    found: int
    still_pending: tuple[str, ...]


async def run_decision_lanes(
    session_factory: Any,
    repo_id: str,
    repo_path: Path,
    plan: DecisionLanePlan,
    *,
    provider: Any,
    inputs: Any,
    vector_store: Any = None,
) -> DecisionLaneOutcome:
    """Run the planned lanes with *provider*, persist what they find.

    A lane that failed stays pending so the next model run retries it; one that
    ran and found nothing is done.
    """
    from repowise.core.persistence import get_session
    from repowise.core.pipeline.persist import persist_analysis
    from repowise.core.pipeline.phases.analysis import _run_decision_extraction

    report = await _run_decision_extraction(
        repo_path,
        llm_client=provider,
        graph_builder=inputs.graph_builder,
        git_meta_map=inputs.git_meta_map,
        parsed_files=inputs.parsed_files,
        source_map=inputs.source_map,
        progress=None,
        only_sources=plan.lanes,
    )
    if report is None:
        return DecisionLaneOutcome(found=0, still_pending=plan.lanes)
    failures = getattr(report, "failures", None) or {}
    # Only the decision fields: the other analyses were not rerun.
    result = types.SimpleNamespace(
        decision_report=report,
        dead_code_report=None,
        doc_drift_report=None,
        health_report=None,
        vector_store=vector_store,
        git_metadata_list=list(inputs.git_meta_map.values()),
        repo_path=repo_path,
    )
    async with get_session(session_factory) as session:
        await persist_analysis(result, session, repo_id)
    return DecisionLaneOutcome(
        found=len(report.decisions),
        still_pending=tuple(lane for lane in plan.lanes if lane in failures),
    )
