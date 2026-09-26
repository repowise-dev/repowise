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
# Path mode used to return every governing record whole: on ``persist.py`` that
# was 15 records inlining 241 file paths between them, plus an origin story
# carrying full commit bodies — 81 854 chars, which the MCP host rejects
# outright (see ``_budget.budgeter``: over the cap is an isError, not a
# truncation). The mode that matters most, "what governs this file right before
# I edit it", hard-failed on exactly the bug-magnet files it exists for.
#
# So: rank, project, then enforce. The caps below are the projection; the
# budget pass after them is the guarantee, since no fixed cap can bound a
# response whose fields are free text.

#: Governing records kept, best-first. Past ~8 the tail is review-queue noise.
_MAX_PATH_DECISIONS = 8

#: Candidates inlined beside a path's decisions. Deliberately far below the
#: decisions cap: this lane exists so a reader knows a queue is there and can
#: go and work it, not so they can work it from inside a get_why response. A
#: live index carries 380 candidates, and a hot path can be named by dozens.
_MAX_PATH_CANDIDATES = 3

#: Paths kept per record. The array answers "how wide is this decision", which
#: a head plus a total answers as well as 241 paths do.
_MAX_AFFECTED_FILES = 10

#: Headroom left under the budget for ``OmissionCollector.attach``, which adds
#: ``omission_marker`` + ``_meta.omitted`` *after* the last size check.
_COLLECTOR_HEADROOM_CHARS = 600

# Ranking uses ``status_rank`` from lifecycle. The local table this file kept
# had drifted from it: it ranked ``deprecated`` ahead of ``superseded`` while
# the list endpoint ranked them the other way, so a record moved position
# depending on which surface was asked.


# --- Search-mode caps -------------------------------------------------------
#
# Search mode had no caps at all: it served eight whole records with their file
# arrays inlined, measuring 27 640 - 34 917 chars over five probe questions
# against a 32 000 budget, so two of the five went over the ceiling outright.
# The cost was not only transport. On a question the store does answer ("why
# ruff check and not ruff format", held by two active records) it returned eight
# records, none of them those, three restating one unrelated decision. A long
# low-relevance answer teaches an agent that the tool is not worth calling,
# which is more expensive than a miss.
#
# So this mode gets few records served whole rather than many served thin: a
# padded answer is the failure mode, and thinning every record to keep eight of
# them is padding with extra steps.

#: Records kept by search mode. Three whole beats eight thinned: past the third
#: hit the ranking is not trustworthy enough to spend an agent's context on.
_MAX_SEARCH_DECISIONS = 3

#: Records kept by workspace search. Above the single-repo three because the
#: answer can genuinely live in more than one store and the repo is part of it;
#: nowhere near the fifteen whole records this path served before it was ranked.
_MAX_WORKSPACE_DECISIONS = 5

#: Nearest pages pulled for the *one* semantic lookup search mode makes. Both
#: lanes (decisions, documentation) are partitioned out of this single window,
#: so the depth is the old decision lane's rather than the sum of the two.
_SEMANTIC_WINDOW = 50

#: Records search mode ranks over. It used to be 200, against a store holding
#: 614: ``list_decisions`` sorts confirmed-then-confident, so the 414 records
#: below the cut were unreachable by any question, and the cap was a silent
#: recall ceiling rather than a cost control. It is not a cost control either —
#: ranking is a substring scan over short fields, measured at 2 ms for 182
#: records here, so the whole store costs single-digit milliseconds. Kept
#: bounded only so an unattended extractor cannot turn one call into an
#: unbounded scan.
_DECISION_CORPUS_LIMIT = 2000

#: Items per list in the health dashboard. This mode is an orientation call:
#: asked once, skimmed, acted on twice. It served 45 items to be read as a
#: verdict. Halved now that ``get_decision_health_summary`` ranks what it
#: returns: cutting an unranked list only makes a list nobody reads shorter,
#: cutting a ranked one keeps the part that is worth reading. The full sizes stay
#: legible in ``counts`` and in the summary line, and every row cut here is
#: recoverable through the omission collector, so nothing is silently dropped.
#:
#: That clause needs a list to be true of: for a count-only lane there was
#: nothing to recover, which is why the two below exist.
_MAX_HEALTH_STALE = 5
_MAX_HEALTH_PROPOSED = 5
_MAX_HEALTH_UNGOVERNED = 8

#: Retired records and accepted records naming no file. Same 5 as its peers:
#: a pointer into history, not a place to read it from.
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

    The projection above bounds the structured fields; this bounds the free
    text ones, which no fixed cap can — a single record's ``rationale`` is
    unbounded, and the ungoverned-file branch returns git archaeology instead
    of decisions and so is not capped by any of them.

    Stages, cheapest loss first:

    1. Drop ``origin_story.linked_decisions``, which re-inlines each record's
       title, rationale and matched commits — all of it already in
       ``decisions``.
    2. Drop the candidate lane whole. A candidate is a review request, and a
       response that cannot afford the rules it must not spend on the queue.
    3. Drop governing records from the tail, all the way to none if it comes
       to that. They are sorted best-first, so the tail is review-queue noise,
       and an empty list plus a marker beats a rejected response.
    4. Trim the fallback blocks the ungoverned branch adds (``code_rationale``,
       then ``git_archaeology``) to a tail, dropping them whole only if that is
       not enough, then ``origin_story``. What survives — mode, path,
       alignment, ``_meta`` — is bounded.

    Every drop goes to the omission store, so the agent gets a
    ``[repowise#<ref>]`` marker it can expand rather than a silently shortened
    response. Call after ``_meta`` is set: the collector writes into it.

    *collector* is the one a caller already started — the episode block caps
    long bodies and banks the overflow before this runs. It must be reused
    rather than joined by a second, because ``attach`` overwrites
    ``_meta.omitted`` with its own refs and the loser's markers would then
    point at content the response no longer advertises. It is also why the
    under-budget path still attaches: a response that fits can still carry a
    capped body whose remainder needs advertising.
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

    # Episodes go before the governing records, not after: they are the newest
    # evidence kind here and must only ever spend slack. Dropping them later in
    # the sequence meant the decisions loop ran with the episode block still
    # inflating the response, and a governing record was evicted to make room
    # for an episode that then survived — measured, not theorised.
    # ``episodes[]`` floors at one row, so the whole-block entry behind it is
    # what still empties the lane before the decisions loop below. Trimming
    # first is why mild pressure costs rows: this served 0 of 20.
    # Candidates first of everything: they are the one lane whose whole
    # purpose is to be reviewed later, so under pressure they are the cheapest
    # thing in the response to lose. Their note goes with them, because a
    # sentence counting candidates that are no longer in the payload is worse
    # than no sentence.
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

    _shed("episodes[]", "episodes")

    _shed_tail_decisions(result_data, collector)

    # The ungoverned branch's whole answer, and it served 0 of 58 mined
    # rationale comments on a file with no governing record at all.
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
