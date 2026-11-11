"""Path mode: what governs one file, and the per-target cards several get."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.analysis.next_call import render_call
from repowise.core.persistence.crud.authority import decision_currencies, pinned_decision_ids
from repowise.core.persistence.crud.decision_staleness import latest_grant_times
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord
from repowise.core.precedent.currency import describe_decision_currency
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._directory import (
    _build_directory_origin,
    _indexed_under,
    _rows_under,
)
from repowise.server.mcp_server._episodes import episode_evidence
from repowise.server.mcp_server._helpers import (
    _build_origin_story,
    _compute_alignment,
    _get_exclude_spec,
    _get_repo,
    _resolve_repo_context,
    is_excluded,
)
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._meta import served_root
from repowise.server.mcp_server._why_evidence import (
    _resolved_commits,
    annotate_response_evidence_async,
)
from repowise.server.mcp_server.tool_why.archaeology import _git_archaeology_fallback
from repowise.server.mcp_server.tool_why.caps import (
    _MAX_BACKGROUND,
    _MAX_PATH_DECISIONS,
    _cap_episodes,
    _cap_path_decision_lanes,
    _cap_supporting_lanes,
    _cap_target_context,
    _fit_path_response,
    _serve_episodes,
)
from repowise.server.mcp_server.tool_why.lineage import _lineage_for_records
from repowise.server.mcp_server.tool_why.loading import (
    _all_git_metadata,
    _attach_response_decision_evidence,
    _git_metadata_for,
    _hydrate_response_decision_evidence,
    _load_corpus,
)
from repowise.server.mcp_server.tool_why.projection import (
    _authority_of,
    _governing_decision_entry,
    _path_decision_sort_key,
    bounded,
    trim_background,
)
from repowise.server.mcp_server.tool_why.ranking import _collapse_restatements
from repowise.server.repo_paths import missing_path_hint


async def _why_path(
    query: str, repo: str | None, *, history: bool = False, follow_moves: bool = True
) -> dict:
    """Mode 2: query is a path: what governs it, then (on request) its history.

    By default the answer is the constraints only: the rules in force, then at
    most ``_MAX_BACKGROUND`` mined records, labelled. *history* adds the origin
    story, alignment, withdrawn records, archaeology, rationale comments and
    episodes.
    """
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    exclude_spec = _get_exclude_spec(ctx.path)
    if is_excluded(query, exclude_spec):
        return {"query": query, "error": f"'{query}' is excluded by exclude_patterns."}
    async with get_session(ctx.session_factory) as session:
        repository = await _get_repo(session)
        res = await session.execute(
            select(DecisionRecord).where(
                DecisionRecord.repository_id == repository.id,
            )
        )
        all_decisions = res.scalars().all()

        # Load git metadata for origin story
        git_meta = await _git_metadata_for(session, repository.id, query)
        matched = _records_naming_path(all_decisions, query)
        # A typo or a stale path: nothing to excavate, so say so before the
        # archaeology fallback mines unrelated commits for it.
        known = git_meta is not None or bool(matched) or _maybe_on_disk(ctx.path, query)
        if not known and not await _indexed_under(
            session, repository.id, query, exclude_spec
        ):
            return await _path_not_found(query, repo, ctx.path, history, follow_moves)

        # A record with no acceptance row is a candidate whatever its status.
        # Over every record, since alignment reads records this path did not match.
        currencies = await decision_currencies(session, repository.id, all_decisions)

        # Rank before capping, so the survivors are the ones that govern.
        matched.sort(key=_path_decision_sort_key)
        lineage_by_id = await _lineage_for_records(session, matched, all_decisions)
        resolved = await asyncio.to_thread(_resolved_commits, None, matched, ctx.path)
        governing, candidates, retired = _split_collapsed(
            matched, lineage_by_id, currencies, resolved, collector
        )
        if governing:
            governing = _pinned_first(governing, await pinned_decision_ids(session, repository.id))
        await _stamp_still_true(governing, matched, ctx.path, session, repository.id)

        result_data: dict[str, Any] = {
            "mode": "path",
            "path": query,
            "decisions": governing,
        }
        episodes: list[dict] = []
        if history:
            lanes = _PathLanes(matched, all_decisions, currencies, governing, candidates, retired)
            episodes, collector = await _serve_history(
                result_data, session, ctx, repository, git_meta, exclude_spec, lanes, collector
            )
        else:
            _add_background(result_data, candidates)

        result_data["_meta"] = _build_meta(repository=repository)
        await _attach_response_decision_evidence(session, result_data, all_decisions)
        await annotate_response_evidence_async(
            result_data, ctx.alias, all_decisions, repo_root=ctx.path
        )
        return _finish_path(result_data, ctx.path, episodes, collector, history=history)


@dataclass(frozen=True)
class _PathLanes:
    """What a path matched, and its records split by authority."""

    matched: list
    all_decisions: list
    currencies: dict[str, Any]
    governing: list[dict]
    candidates: list[dict]
    retired: list[dict]


async def _serve_history(
    result_data: dict[str, Any],
    session: Any,
    ctx: Any,
    repository: Any,
    git_meta: Any | None,
    exclude_spec: Any,
    lanes: _PathLanes,
    collector: OmissionCollector,
) -> tuple[list[dict], OmissionCollector]:
    """Origin story, alignment, review lanes, ungoverned evidence and episodes."""
    query = result_data["path"]
    all_git_meta = await _all_git_metadata(session, repository.id)
    # Every lane: the origin story is evidence, not instruction.
    result_data["origin_story"] = _origin_for(
        query,
        git_meta,
        _rows_under(query, all_git_meta, exclude_spec),
        lanes.governing + lanes.candidates + lanes.retired,
    )
    # A coverage number, so scored over every match, not the capped head.
    result_data["alignment"] = _compute_alignment(
        query,
        [{"id": d.id, "title": d.title} for d in lanes.matched],
        lanes.all_decisions,
        lanes.currencies,
    )
    _add_review_lanes(result_data, lanes.candidates, lanes.retired)

    # --- Fallback: git archaeology when no accepted decision governs ---
    if not lanes.governing:
        await _add_ungoverned_evidence(
            result_data, query, git_meta, all_git_meta, repository, ctx.path
        )

    # Episodes are additive, not a fallback: a governed file still has history.
    episode_population: list[dict] = []
    episodes, pending = await asyncio.to_thread(
        episode_evidence,
        ctx.path,
        paths=[query],
        full_population=episode_population,
    )
    collector = _serve_episodes(
        result_data, episodes, episode_population, pending, collector, ctx.path
    )
    return episodes, collector


def _finish_path(
    result_data: dict[str, Any],
    repo_root: Any,
    episodes: list[dict],
    collector: OmissionCollector,
    *,
    history: bool,
) -> dict:
    """Cap the lanes, trim a default answer to its constraints, then fit the budget."""
    _cap_supporting_lanes(result_data, collector, label=result_data["path"])
    _cap_episodes(result_data, episodes, collector)
    if history:
        _cap_path_decision_lanes(result_data, collector)
    else:
        _cap_path_decision_lanes(result_data, collector, side_cap=_MAX_BACKGROUND)
        _drop_history_fields(result_data)
        result_data["more"] = _history_pointer([result_data["path"]])
    return _fit_path_response(result_data, repo_root, collector=collector)


async def _path_not_found(
    path: str, repo: str | None, repo_root: Any, history: bool, follow_moves: bool
) -> dict:
    """Answer for where *path* went when git says it moved, else say it is gone.

    A rename is followed to its new name; a module split into a same-named
    package is answered for that package. Either way the answer names the old
    path, so the caller can tell it was redirected.
    """
    hint = await asyncio.to_thread(missing_path_hint, str(repo_root or ""), path)
    moved = hint.get("moved_to") or []
    new_path = moved[0] if len(moved) == 1 else str(PurePosixPath(path).with_suffix(""))
    # A split is answered for its package only when every new file lives there.
    split_ok = len(moved) < 2 or all(m.startswith(f"{new_path}/") for m in moved)
    if follow_moves and moved and split_ok and new_path != path:
        result = await _why_path(new_path, repo, history=history, follow_moves=False)
        if "error" not in result:
            result["moved_from"] = path
            result["removed_by_commit"] = hint["removed_by_commit"][:12]
            return result
    out: dict[str, Any] = {
        "mode": "path",
        "path": path,
        "error": f"Path not found: '{path}' is neither in the checkout "
        "nor in the indexed git history.",
    }
    if hint.get("removed_by_commit"):
        out["error"] = f"'{path}' was removed in {hint['removed_by_commit'][:12]}."
        out["removed_by_commit"] = hint["removed_by_commit"][:12]
    return out


def _split_collapsed(
    matched: list,
    lineage_by_id: dict[str, list[dict]],
    currencies: dict[str, Any],
    resolved: dict[str, str],
    collector: OmissionCollector,
) -> tuple[list[dict], list[dict], list[dict]]:
    """``_split_by_authority`` over *matched* with restatements folded into their rows."""
    served, restates = _collapse_path_records(matched, currencies, resolved)
    governing, candidates, retired = _split_by_authority(
        served, lineage_by_id, currencies, collector
    )
    for entry in (*governing, *candidates):
        if restates.get(entry["id"]):
            entry["restates"] = restates[entry["id"]]
    return governing, candidates, retired


def _collapse_path_records(
    matched: list, currencies: dict[str, Any], resolved: dict[str, str]
) -> tuple[list, dict[str, list[str]]]:
    """*matched* with restatements folded, and ``{kept id: folded ids}``.

    Rules collapse first, so a mined record restating a rule folds into the
    rule rather than the reverse. Withdrawn records keep their own lane.
    """

    def in_force(d: Any) -> bool:
        currency = currencies.get(d.id)
        return currency is not None and is_governing(currency)

    rules = [d for d in matched if in_force(d)]
    background = [d for d in matched if currencies.get(d.id) is None]
    withdrawn = [d for d in matched if not in_force(d) and currencies.get(d.id) is not None]
    collapsed = _collapse_restatements(rules + background, resolved)
    return [d for d, _ in collapsed] + withdrawn, {d.id: f for d, f in collapsed if f}


#: Detail a rule row carries only with ``include=["history"]``.
_RULE_DETAIL_FIELDS = ("context", "alternatives", "staleness_score")


def _drop_history_fields(result_data: dict[str, Any]) -> None:
    """Trim path-mode rows to what constrains an edit, in place.

    A background row keeps only its claim, bounded, and where it came from:
    beside the rules of one file, its rationale and scope are history.
    """
    for row in result_data.get("decisions") or []:
        for key in _RULE_DETAIL_FIELDS:
            row.pop(key, None)
    background = result_data.get("candidates") or []
    trim_background(background)
    for row in background:
        for key in [k for k in row if k == "rationale" or k.startswith("affected_files")]:
            row.pop(key)
        if isinstance(row.get("decision"), str):
            row["decision"] = bounded(row["decision"])


def _history_pointer(paths: list[str]) -> str:
    call = render_call("get_why", {"targets": paths, "include": ["history"]})
    return f"Origin story, commits, episodes and contributors: {call}"


def _add_background(result_data: dict[str, Any], candidates: list[dict]) -> None:
    """Serve the mined records beside the rules, labelled as not binding."""
    if candidates:
        result_data["candidates"] = candidates
        result_data["candidates_note"] = (
            f"{len(candidates)} background record(s) mention this path. None is "
            "in force: read them as context, not rules."
        )


def _maybe_on_disk(repo_path: str | Path | None, path: str) -> bool:
    """False only when the checkout is known and *path* is not in it.

    Resolved and contained, like ``read_repo_file_text``: an absolute path,
    a ``..`` escape or a symlink out of the checkout is not in it.
    """
    if not repo_path:
        return True
    root = Path(repo_path).resolve()
    try:
        candidate = (root / path).resolve()
        candidate.relative_to(root)
    except (OSError, ValueError):
        return False
    return candidate.exists()


def _origin_for(
    path: str, git_meta: Any | None, rows_under: list, governing: list[dict]
) -> dict:
    """A file's origin story, or a directory's from the files under it."""
    if git_meta is not None:
        return _build_origin_story(path, git_meta, governing)
    return _build_directory_origin(path.rstrip("/"), rows_under, governing)


def _records_naming_path(all_decisions: list, path: str) -> list:
    """Records whose own scope names *path* as a file or a module."""
    # A record whose file list is just its source commit's footprint does not
    # govern those files; it stays in search.
    return [
        d
        for d in all_decisions
        if binds_to_paths(d.scope_basis)
        and (
            path in json.loads(d.affected_files_json)
            or path in json.loads(d.affected_modules_json)
        )
    ]


def _split_by_authority(
    matched: list,
    lineage_by_id: dict[str, list[dict]],
    currencies: dict[str, Any],
    collector: OmissionCollector,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Project *matched* into ``(governing, candidates, retired)`` rows, in order."""
    governing: list[dict] = []
    candidates: list[dict] = []
    retired: list[dict] = []
    for d in matched:
        # The supersedes/refines chain, so the answer is a lineage, not a list.
        lineage = lineage_by_id.get(d.id, [])
        entry = _governing_decision_entry(
            d, json.loads(d.affected_files_json), lineage, collector
        )
        currency = currencies.get(d.id)
        if currency is None:
            entry["authority"] = "candidate"
            # Never accepted: its own lane, not the rules for this file.
            entry["review_state"] = "open"
            candidates.append(entry)
            continue
        entry["currency"] = currency
        if is_governing(currency):
            entry["authority"] = "accepted"
            governing.append(entry)
        else:
            # Accepted once, withdrawn since: neither rule nor review request,
            # so a third lane rather than dropped.
            entry["authority"] = "withdrawn"
            retired.append(entry)
    return governing, candidates, retired


def _pinned_first(governing: list[dict], pinned: set[str]) -> list[dict]:
    """*governing* with pinned rows marked and first, the rest in their order.

    The mark is set only where true, so a store with nothing pinned answers in
    the same bytes as before.
    """
    for entry in governing:
        if entry["id"] in pinned:
            entry["pinned"] = True
    return sorted(governing, key=lambda e: not e.get("pinned"))


async def _stamp_still_true(
    governing: list[dict], matched: list, repo_path: str | Path, session: Any, repository_id: str
) -> None:
    """Ask git whether the top governing record still holds, and say so on it."""
    # Only the top record: the git query is affordable once per call, and the
    # first-ranked record is the one a reader acts on. Candidates never get it.
    if not governing:
        return
    top = next(d for d in matched if d.id == governing[0]["id"])
    # Counted from the latest review, as the stored staleness score is.
    grants = await latest_grant_times(session, repository_id, [top.id])
    sentence = await asyncio.to_thread(
        describe_decision_currency,
        repo_path,
        created_at=top.created_at,
        nodes=json.loads(top.affected_files_json or "[]"),
        evidence_commits=json.loads(top.evidence_commits_json or "[]"),
        accepted_at=grants.get(top.id),
    )
    if sentence:
        governing[0]["still_true"] = sentence


def _add_review_lanes(
    result_data: dict[str, Any], candidates: list[dict], retired: list[dict]
) -> None:
    """Serve candidates and retired records beside the rules, each labelled."""
    if candidates:
        # Separated from the rules, so background never reads as one.
        result_data["candidates"] = candidates
        result_data["candidates_note"] = (
            f"{len(candidates)} background record(s) mention this path. None is "
            "in force: read them as context, not rules. One that still holds can "
            "be put in force with "
            "`repowise decision confirm <id> --agent <your slug> --scope <path>`."
        )

    if retired:
        # History, never a rule.
        result_data["history"] = retired
        result_data["history_note"] = (
            f"{len(retired)} decision(s) governing this path were accepted "
            "and have since been superseded or withdrawn. They are history, "
            "not rules."
        )


async def _add_ungoverned_evidence(
    result_data: dict[str, Any],
    path: str,
    git_meta: Any | None,
    all_git_meta: list,
    repository: Any,
    repo_path: str | Path,
) -> None:
    """Git archaeology and mined rationale for a file no accepted decision governs."""
    result_data["git_archaeology"] = await _git_archaeology_fallback(
        path,
        git_meta,
        all_git_meta,
        served_root(repository, repo_path),
    )
    # The "why" may live in a code comment.
    rationale = _mine_rationale(
        repo_path, [path], None, max_results=1000, for_why=True
    )
    if rationale:
        result_data["code_rationale"] = rationale


async def _build_target_context(
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    targets: list[str],
    accepted: set[str] | None = None,
    *,
    history: bool = True,
) -> dict[str, Any]:
    """Per-target governing decisions + origin story, with archaeology fallback.

    Without *history* a card is its rules and background records only.

    ``governing_decisions`` holds only accepted records; the rest go to
    ``candidate_decisions``. The archaeology fallback keys on the accepted lane:
    a file with only candidates is ungoverned, and its commits are stronger
    evidence than an unconfirmed candidate.
    """
    async with get_session(ctx.session_factory) as session2:
        # Load all git metadata for cross-file search
        all_git_meta_list = await _all_git_metadata(session2, repository.id)

        root = served_root(repository, ctx.path)
        exclude_spec = _get_exclude_spec(ctx.path)
        target_context: dict[str, Any] = {}
        for t in targets:
            target_context[t] = await _target_card(
                t,
                all_decisions,
                target_git.get(t),
                all_git_meta_list,
                root,
                accepted or set(),
                _rows_under(t, all_git_meta_list, exclude_spec),
                history=history,
            )
        return target_context


def _records_governing_target(all_decisions: list, target: str) -> list:
    """Records naming *target*, best-first, by the same rule as ``_why_path``.

    A record's modules are mostly its files' folders, so a file merely under
    one shares a folder with the decision, not its subject. Path mode and the
    decision graph match a module only as itself, and so does this.
    """
    governing_records = _records_naming_path(all_decisions, target)
    governing_records.sort(key=_path_decision_sort_key)
    return governing_records


async def _target_card(
    t: str,
    all_decisions: list,
    git_m: Any | None,
    all_git_meta_list: list,
    repo_root: str | None,
    accepted_ids: set[str],
    rows_under: list,
    *,
    history: bool = True,
) -> dict[str, Any]:
    """One target's card: its rules, its candidates, its origin, and archaeology."""
    rows = [
        {
            "id": d.id,
            "title": d.title,
            "status": d.status,
            "source": d.source,
            "authority": _authority_of(d.id, accepted_ids),
        }
        for d in _records_governing_target(all_decisions, t)
    ]
    t_governing = [r for r in rows if r["authority"] == "accepted"]
    t_candidates = [r for r in rows if r["authority"] != "accepted"]
    ctx_entry: dict[str, Any] = {"governing_decisions": t_governing}
    if t_candidates:
        ctx_entry["candidate_decisions"] = t_candidates
    if history:
        ctx_entry.update(
            await _card_history(t, git_m, rows_under, t_governing, all_git_meta_list, repo_root)
        )
    return ctx_entry


async def _card_history(
    t: str,
    git_m: Any | None,
    rows_under: list,
    t_governing: list[dict],
    all_git_meta_list: list,
    repo_root: str | None,
) -> dict[str, Any]:
    """A card's origin, plus git archaeology when no *accepted* decision governs."""
    out: dict[str, Any] = {"origin": _origin_for(t, git_m, rows_under, t_governing)}
    if not t_governing:
        out["git_archaeology"] = await _git_archaeology_fallback(
            t,
            git_m,
            all_git_meta_list,
            repo_root,
        )
    return out


async def _why_targets(targets: list[str], repo: str | None, *, history: bool = False) -> dict:
    """Mode 2b: targets and no query — the paths themselves are the question.

    One target is path mode outright, the fullest answer about a file. Several
    get the per-target card instead: path mode per target would cost a corpus
    scan and a git subprocess each.
    """
    if len(targets) == 1:
        return await _why_path(targets[0], repo, history=history)

    ctx, repository, all_decisions, target_git, accepted = await _load_corpus(
        repo, targets
    )
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    result_data = {
        "mode": "path",
        "paths": targets,
        "target_context": await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted, history=history
        ),
        "_meta": _build_meta(repository=repository, targets=targets),
        **({} if history else {"more": _history_pointer(targets)}),
    }
    await _hydrate_response_decision_evidence(ctx, result_data, all_decisions)
    result_data = await annotate_response_evidence_async(
        result_data, ctx.alias, all_decisions, repo_root=ctx.path
    )
    _cap_supporting_lanes(result_data, collector, label="targets")
    _cap_target_context(
        result_data["target_context"],
        collector,
        candidate_cap=_MAX_PATH_DECISIONS if history else _MAX_BACKGROUND,
    )
    collector.attach(result_data)
    return result_data
