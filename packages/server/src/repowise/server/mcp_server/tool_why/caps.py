"""Response caps for every mode, and the budget fitting path mode needs."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
    fit_to_budget,
    over_budget,
)
from repowise.server.mcp_server._episodes import bank_overflow

# --- Path-mode cap and projection -------------------------------------------
#
# Rank, project, then enforce. Over-budget is a host error, not a truncation,
# so the caps below project and the budget pass after them is the guarantee:
# no fixed cap bounds free-text fields.

#: Governing records kept, best-first. Past ~8 the tail is review-queue noise.
_MAX_PATH_DECISIONS = 8

#: Candidates inlined beside a path's decisions. Far below the decisions cap:
#: this lane signals that a review queue exists, it is not the queue.
_MAX_PATH_CANDIDATES = 3

#: Paths kept per record. A head plus a total shows how wide a decision is.
_MAX_AFFECTED_FILES = 10

#: Headroom left under the budget for ``OmissionCollector.attach``, which adds
#: ``omission_marker`` + ``_meta.omitted`` *after* the last size check.
_COLLECTOR_HEADROOM_CHARS = 600

# Status ranking uses lifecycle's ``status_rank``, so every surface orders
# records the same way.


# --- Search-mode caps -------------------------------------------------------
#
# Few records served whole rather than many served thin: a long low-relevance
# answer teaches an agent the tool is not worth calling.

#: Records kept by search mode. Past the third hit the ranking is not
#: trustworthy enough to spend an agent's context on.
_MAX_SEARCH_DECISIONS = 3

#: Records kept by workspace search. Above three because the answer can live
#: in more than one repo's store.
_MAX_WORKSPACE_DECISIONS = 5

#: Nearest pages pulled for search mode's one semantic lookup. Both lanes
#: (decisions, documentation) are partitioned out of this single window.
_SEMANTIC_WINDOW = 50

#: Records search mode ranks over. Not a cost control (ranking is a cheap
#: substring scan) and a low cut would be a silent recall ceiling; bounded only
#: so an unattended extractor cannot make one call an unbounded scan.
_DECISION_CORPUS_LIMIT = 2000

#: Items per list in the health dashboard, an orientation call. The lists are
#: ranked, so the cut keeps the part worth reading; full sizes stay in
#: ``counts`` and every cut row is recoverable through the omission collector.
_MAX_HEALTH_STALE = 5
_MAX_HEALTH_PROPOSED = 5
_MAX_HEALTH_UNGOVERNED = 8

#: Retired records and accepted records naming no file: a pointer into history.
_MAX_HEALTH_RETIRED = 5


def _cap_origin_story(
    origin_story: dict[str, Any],
    collector: OmissionCollector,
    *,
    label: str,
) -> None:
    """Bound each origin lane independently and bank its exact omitted rows."""
    for key, cap in (
        ("contributors", 5),
        ("key_commits", 5),
        ("linked_decisions", _MAX_PATH_DECISIONS),
    ):
        rows = origin_story.get(key)
        if isinstance(rows, list):
            cap_collection(
                origin_story,
                key,
                rows,
                cap,
                collector,
                label=f"{label} :: origin.{key} beyond cap={cap}",
            )
    for linked in origin_story.get("linked_decisions") or []:
        if not isinstance(linked, dict):
            continue
        commits = linked.get("evidence_commits")
        if isinstance(commits, list):
            cap_collection(
                linked,
                "evidence_commits",
                commits,
                5,
                collector,
                label=f"{label} :: origin.linked_decisions.evidence_commits beyond cap=5",
            )


def _cap_archaeology(
    archaeology: dict[str, Any], collector: OmissionCollector, *, label: str
) -> None:
    """Cap fully annotated archaeology lanes without discarding their tails."""
    for key, cap in (("file_commits", 10), ("cross_references", 10), ("git_log", 20)):
        rows = archaeology.get(key)
        if isinstance(rows, list):
            cap_collection(
                archaeology,
                key,
                rows,
                cap,
                collector,
                label=f"{label} :: archaeology.{key} beyond cap={cap}",
            )


def _cap_supporting_lanes(
    result: dict[str, Any], collector: OmissionCollector, *, label: str
) -> None:
    """Cap supporting Why lanes only after evidence annotation is complete."""
    origin = result.get("origin_story")
    if isinstance(origin, dict):
        _cap_origin_story(origin, collector, label=label)
    archaeology = result.get("git_archaeology")
    if isinstance(archaeology, dict):
        _cap_archaeology(archaeology, collector, label=label)
    rationale = result.get("code_rationale")
    if isinstance(rationale, list):
        cap_collection(
            result,
            "code_rationale",
            rationale,
            5,
            collector,
            label=f"{label} :: code_rationale beyond cap=5",
        )
    for target, context in (result.get("target_context") or {}).items():
        if not isinstance(context, dict):
            continue
        target_origin = context.get("origin")
        if isinstance(target_origin, dict):
            _cap_origin_story(target_origin, collector, label=target)
        target_archaeology = context.get("git_archaeology")
        if isinstance(target_archaeology, dict):
            _cap_archaeology(target_archaeology, collector, label=target)


def _prepare_episode_bodies(
    population: list[dict[str, Any]],
    visible_count: int,
    pending: list[tuple[dict, str, str]],
    collector: OmissionCollector,
    repo_root: Path,
) -> OmissionCollector:
    """Inline-bank visible long bodies; keep omitted episode rows byte-complete."""
    visible_ids = {id(entry) for entry in population[:visible_count]}
    visible_pending: list[tuple[dict, str, str]] = []
    for entry, label, body in pending:
        if id(entry) in visible_ids:
            visible_pending.append((entry, label, body))
        else:
            entry["recorded"] = body
    return bank_overflow(
        visible_pending,
        tool="get_why",
        repo_root=repo_root,
        collector=collector,
    ) or collector


def _serve_episodes(
    result: dict[str, Any],
    episodes: list[dict[str, Any]],
    population: list[dict[str, Any]],
    pending: list[tuple[dict, str, str]],
    collector: OmissionCollector,
    repo_root: Path,
) -> OmissionCollector:
    """Put the episode population on *result*, long visible bodies banked first.

    Returns the collector to carry on with: banking can start a new one, and
    the caller must attach that one rather than the one it passed in.
    """
    if not episodes:
        return collector
    collector = _prepare_episode_bodies(
        population, len(episodes), pending, collector, repo_root
    )
    result["episodes"] = population
    return collector


def _cap_episodes(
    result: dict[str, Any], episodes: list[dict[str, Any]], collector: OmissionCollector
) -> None:
    """Cap the served population back to the rows episode retrieval chose."""
    if episodes:
        cap_collection(
            result,
            "episodes",
            result["episodes"],
            len(episodes),
            collector,
            label="episodes beyond construction cap",
        )


def _fit_path_response(
    result_data: dict, repo_root: Any, collector: OmissionCollector | None = None
) -> dict:
    """Shrink a path response until it fits the transport budget.

    The caps bound the structured fields; this bounds the free-text ones.

    Stages, cheapest loss first:

    1. ``origin_story.linked_decisions`` (duplicates ``decisions``), then the
       history and candidate lanes: review requests, not rules.
    2. Episodes, which must only ever spend slack.
    3. Governing records from the tail, down to none if need be: an empty list
       plus a marker beats a rejected response.
    4. The ungoverned branch's ``code_rationale`` and ``git_archaeology``,
       trimmed then dropped, then ``origin_story``.

    Every drop goes to the omission store. Call after ``_meta`` is set.

    Reuse the caller's *collector*: a second one's ``attach`` would overwrite
    ``_meta.omitted``. It is also why the under-budget path still attaches.
    """

    def _over() -> bool:
        return over_budget(result_data, headroom=_COLLECTOR_HEADROOM_CHARS)

    if not _over():
        if collector is not None:
            collector.attach(result_data)
        return result_data

    if collector is None:
        collector = OmissionCollector("get_why", repo_root=repo_root)

    def _shed(*order: str) -> None:
        fit_to_budget(
            result_data,
            order,
            collector,
            headroom=_COLLECTOR_HEADROOM_CHARS,
            record_counts=True,
        )

    # Review lanes first; each note goes with its lane, since a note counting
    # rows no longer in the payload is worse than none.
    _shed(
        "origin_story.linked_decisions",
        "history[]",
        "history",
        "candidates[]",
        "candidates",
    )
    for lane_key in ("candidates", "history"):
        if not result_data.get(lane_key):
            result_data.pop(f"{lane_key}_note", None)

    # Episodes before the governing records, so a record is never evicted to
    # make room for one. Trimmed first, so mild pressure costs rows, not the lane.
    _shed("episodes[]", "episodes")

    _shed_tail_decisions(result_data, collector)

    # The ungoverned branch's answer: trim rows before dropping whole lanes.
    _shed(
        "code_rationale[]",
        "git_archaeology.file_commits[]",
        "git_archaeology.cross_references[]",
        "git_archaeology.git_log[]",
        "code_rationale",
        "git_archaeology.file_commits",
        "git_archaeology.cross_references",
        "git_archaeology.git_log",
        "origin_story",
    )

    collector.attach(result_data)
    return result_data


def _shed_tail_decisions(result_data: dict, collector: OmissionCollector) -> None:
    """Drop governing records from the tail until the response fits.

    Stage 3 of :func:`_fit_path_response`. The ``decisions_*`` counts the
    construction cap wrote are brought up to date, so a reader can tell the
    budget cut from the cap, or both, apart.
    """
    decisions: list = result_data.get("decisions") or []
    while decisions and over_budget(result_data, headroom=_COLLECTOR_HEADROOM_CHARS):
        dropped = decisions.pop()
        collector.add(f"dropped governing decision {dropped.get('title', '')}", dropped)
        result_data["truncated"] = True
        result_data.setdefault("dropped_decisions", []).append(dropped.get("id", ""))
    if "decisions_total" in result_data:
        result_data["decisions_emitted"] = len(decisions)
        if len(decisions) < result_data["decisions_total"]:
            prior = result_data.get("decisions_reduced_reason")
            result_data["decisions_reduced_reason"] = (
                "construction_cap_and_response_budget"
                if prior == "construction_cap"
                else "response_budget"
            )
            result_data["decisions_truncated"] = True
            result_data["decisions_omitted"] = result_data["decisions_total"] - len(decisions)


def _cap_target_context(
    target_context: dict[str, Any], collector: OmissionCollector
) -> None:
    """Cap evidence-enriched per-target decision lanes independently."""
    for target, entry in target_context.items():
        for lane in ("governing_decisions", "candidate_decisions"):
            decisions = entry.get(lane)
            if isinstance(decisions, list):
                cap_collection(
                    entry,
                    lane,
                    decisions,
                    _MAX_PATH_DECISIONS,
                    collector,
                    label=f"{target} :: {lane} beyond cap={_MAX_PATH_DECISIONS}",
                )


def _cap_path_decision_lanes(result_data: dict[str, Any], collector: OmissionCollector) -> None:
    """Cap path mode's rules lane and the two lanes beside it."""
    cap_collection(
        result_data,
        "decisions",
        result_data["decisions"],
        _MAX_PATH_DECISIONS,
        collector,
        label=f"path decisions beyond cap={_MAX_PATH_DECISIONS}",
    )
    for lane_key in ("candidates", "history"):
        if result_data.get(lane_key):
            cap_collection(
                result_data,
                lane_key,
                result_data[lane_key],
                _MAX_PATH_CANDIDATES,
                collector,
                label=f"path {lane_key} beyond cap={_MAX_PATH_CANDIDATES}",
            )
