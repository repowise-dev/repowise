"""Path mode: what governs one file, and the per-target cards several get."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.persistence.crud.authority import decision_currencies
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import DecisionRecord
from repowise.core.precedent.currency import describe_decision_currency
from repowise.server.mcp_server._budget import OmissionCollector
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
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
from repowise.server.mcp_server._why_evidence import annotate_response_evidence_async
from repowise.server.mcp_server.tool_why.archaeology import _git_archaeology_fallback
from repowise.server.mcp_server.tool_why.caps import (
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
)


async def _why_path(query: str, repo: str | None) -> dict:
    """Mode 2: query is a path — governing decisions, origin story, alignment."""
    ctx = await _resolve_repo_context(repo)
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    if is_excluded(query, _get_exclude_spec(ctx.path)):
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
        if git_meta is None and not matched and not _maybe_on_disk(ctx.path, query):
            return {
                "mode": "path",
                "path": query,
                "error": f"Path not found: '{query}' is neither in the checkout "
                "nor in the indexed git history.",
            }

        # Pre-load all git metadata for cross-file search (used by fallback)
        all_git_meta = await _all_git_metadata(session, repository.id)

        # A record with no acceptance row is a candidate whatever its status.
        # Over every record, since alignment reads records this path did not match.
        currencies = await decision_currencies(session, repository.id, all_decisions)

        # Rank before capping, so the survivors are the ones that govern.
        matched.sort(key=_path_decision_sort_key)
        lineage_by_id = await _lineage_for_records(session, matched, all_decisions)
        governing, candidates, retired = _split_by_authority(
            matched, lineage_by_id, currencies, collector
        )
        await _stamp_still_true(governing, matched, ctx.path)

        # Every lane: the origin story is evidence, not instruction.
        origin_story = _build_origin_story(
            query, git_meta, governing + candidates + retired
        )

        result_data: dict[str, Any] = {
            "mode": "path",
            "path": query,
            "decisions": governing,
            "origin_story": origin_story,
            # A coverage number, so scored over every match, not the capped head.
            "alignment": _compute_alignment(
                query,
                [{"id": d.id, "title": d.title} for d in matched],
                all_decisions,
                currencies,
            ),
        }
        _add_review_lanes(result_data, candidates, retired)

        # --- Fallback: git archaeology when no accepted decision governs ---
        if not governing:
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

        result_data["_meta"] = _build_meta(repository=repository)
        await _attach_response_decision_evidence(session, result_data, all_decisions)
        await annotate_response_evidence_async(
            result_data, ctx.alias, all_decisions, repo_root=ctx.path
        )
        _cap_supporting_lanes(result_data, collector, label=query)
        _cap_episodes(result_data, episodes, collector)
        _cap_path_decision_lanes(result_data, collector)
        return _fit_path_response(result_data, ctx.path, collector=collector)


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


async def _stamp_still_true(
    governing: list[dict], matched: list, repo_path: str | Path
) -> None:
    """Ask git whether the top governing record still holds, and say so on it."""
    # Only the top record: the git query is affordable once per call, and the
    # first-ranked record is the one a reader acts on. Candidates never get it.
    if not governing:
        return
    top = next(d for d in matched if d.id == governing[0]["id"])
    sentence = await asyncio.to_thread(
        describe_decision_currency,
        repo_path,
        created_at=top.created_at,
        nodes=json.loads(top.affected_files_json or "[]"),
    )
    if sentence:
        governing[0]["still_true"] = sentence


def _add_review_lanes(
    result_data: dict[str, Any], candidates: list[dict], retired: list[dict]
) -> None:
    """Serve candidates and retired records beside the rules, each labelled."""
    if candidates:
        # Separated from the rules, so a review request never reads as one.
        result_data["candidates"] = candidates
        result_data["candidates_note"] = (
            f"{len(candidates)} candidate(s) mention this path and none of them "
            "govern it. Nobody has accepted them, so they are a review "
            "request, not a rule. A person accepts one with "
            "`repowise decision confirm <id> --scope <path>`. An agent "
            "cannot, unless this repository has allowed it; where it has, "
            "pass `--agent <your slug>` so the acceptance is not recorded "
            "under a person's name."
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
        repo_path, [path], None, max_results=1000, truncate_blocks=False
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
) -> dict[str, Any]:
    """Per-target governing decisions + origin story, with archaeology fallback.

    ``governing_decisions`` holds only accepted records; the rest go to
    ``candidate_decisions``. The archaeology fallback keys on the accepted lane:
    a file with only candidates is ungoverned, and its commits are stronger
    evidence than an unconfirmed candidate.
    """
    async with get_session(ctx.session_factory) as session2:
        # Load all git metadata for cross-file search
        all_git_meta_list = await _all_git_metadata(session2, repository.id)

        root = served_root(repository, ctx.path)
        target_context: dict[str, Any] = {}
        for t in targets:
            target_context[t] = await _target_card(
                t,
                all_decisions,
                target_git.get(t),
                all_git_meta_list,
                root,
                accepted or set(),
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
    origin = (
        _build_origin_story(t, git_m, t_governing)
        if git_m
        else {
            "available": False,
            "summary": f"No git history for {t}.",
        }
    )
    ctx_entry: dict[str, Any] = {
        "governing_decisions": t_governing,
        "origin": origin,
    }
    if t_candidates:
        ctx_entry["candidate_decisions"] = t_candidates
    # Git archaeology fallback when no *accepted* decision governs
    if not t_governing:
        ctx_entry["git_archaeology"] = await _git_archaeology_fallback(
            t,
            git_m,
            all_git_meta_list,
            repo_root,
        )
    return ctx_entry


async def _why_targets(targets: list[str], repo: str | None) -> dict:
    """Mode 2b: targets and no query — the paths themselves are the question.

    One target is path mode outright, the fullest answer about a file. Several
    get the per-target card instead: path mode per target would cost a corpus
    scan and a git subprocess each.
    """
    if len(targets) == 1:
        return await _why_path(targets[0], repo)

    ctx, repository, all_decisions, target_git, accepted = await _load_corpus(
        repo, targets
    )
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    result_data = {
        "mode": "path",
        "paths": targets,
        "target_context": await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted
        ),
        "_meta": _build_meta(repository=repository, targets=targets),
    }
    await _hydrate_response_decision_evidence(ctx, result_data, all_decisions)
    result_data = await annotate_response_evidence_async(
        result_data, ctx.alias, all_decisions, repo_root=ctx.path
    )
    _cap_supporting_lanes(result_data, collector, label="targets")
    _cap_target_context(result_data["target_context"], collector)
    collector.attach(result_data)
    return result_data
