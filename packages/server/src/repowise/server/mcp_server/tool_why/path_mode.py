"""Path mode: what governs one file, and the per-target cards several get."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.persistence.crud.authority import (
    decision_currencies,
)
from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import (
    DecisionRecord,
    GitMetadata,
)
from repowise.core.precedent.currency import describe_decision_currency
from repowise.server.mcp_server._budget import (
    OmissionCollector,
    cap_collection,
)
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
from repowise.server.mcp_server._why_evidence import (
    annotate_response_evidence_async,
)
from repowise.server.mcp_server.tool_why.archaeology import _git_archaeology_fallback
from repowise.server.mcp_server.tool_why.caps import (
    _MAX_PATH_CANDIDATES,
    _MAX_PATH_DECISIONS,
    _cap_supporting_lanes,
    _cap_target_context,
    _fit_path_response,
    _prepare_episode_bodies,
)
from repowise.server.mcp_server.tool_why.loading import (
    _attach_response_decision_evidence,
    _hydrate_response_decision_evidence,
    _lineage_for_records,
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
        git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
                GitMetadata.file_path == query,
            )
        )
        git_meta = git_res.scalar_one_or_none()

        # Pre-load all git metadata for cross-file search (used by fallback)
        all_git_res = await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git_meta = all_git_res.scalars().all()

        # A record whose file list is the footprint of the commit it was
        # mined from does not answer "what governs this file". It keeps its
        # files and its place in search; it stops claiming to be specific.
        matched = [
            d
            for d in all_decisions
            if binds_to_paths(d.scope_basis)
            and (
                query in json.loads(d.affected_files_json)
                or query in json.loads(d.affected_modules_json)
            )
        ]
        # The authority split, and the only test that makes it: a record with
        # no acceptance row is a candidate whatever its status column says.
        # Computed over every record in the repository, because the sibling
        # coverage inside alignment reads records this path did not match.
        currencies = await decision_currencies(session, repository.id, all_decisions)

        # Rank before capping, so the 8 that survive are the 8 that govern —
        # not whichever 8 the table scan happened to yield first.
        matched.sort(key=_path_decision_sort_key)
        lineage_by_id = await _lineage_for_records(session, matched, all_decisions)
        governing: list[dict] = []
        candidates: list[dict] = []
        retired: list[dict] = []
        for d in matched:
            # Walk supersedes/refines back to roots so the answer is a
            # lineage chain (sessions → JWT → OAuth2), not a flat list.
            lineage = lineage_by_id.get(d.id, [])
            entry = _governing_decision_entry(
                d, json.loads(d.affected_files_json), lineage, collector
            )
            currency = currencies.get(d.id)
            if currency is None:
                entry["authority"] = "candidate"
                # Never accepted. It goes in its own lane, labelled, rather
                # than into the list an agent reads as the rules for this file.
                entry["review_state"] = "open"
                candidates.append(entry)
                continue
            entry["currency"] = currency
            if is_governing(currency):
                entry["authority"] = "accepted"
                governing.append(entry)
            else:
                # Accepted once, withdrawn since: not a ruling any more, so it
                # must not read as one to ``_stamp_answer_basis`` either.
                entry["authority"] = "withdrawn"
                # Accepted once and withdrawn since. Not a rule and not a
                # review request, so it gets a third lane rather than being
                # dropped: on a file whose only record was superseded, dropping
                # it left the answer with nothing to say about the one thing
                # anybody had ever decided there.
                retired.append(entry)

        # Ask git whether the top governing record still holds — and only the
        # top one. The query is ~60 ms, which is affordable once inside an MCP
        # call and is not affordable eight times; the record ranked first is
        # the one a reader acts on. Everything below it keeps the stored
        # proportion, which needed no subprocess to compute. A candidate never
        # gets the check: it is not something anyone should be acting on.
        if governing:
            top = next(d for d in matched if d.id == governing[0]["id"])
            sentence = await asyncio.to_thread(
                describe_decision_currency,
                ctx.path,
                created_at=top.created_at,
                nodes=json.loads(top.affected_files_json or "[]"),
            )
            if sentence:
                governing[0]["still_true"] = sentence

        # Every lane: the origin story is history, and a commit that matches a
        # candidate's or a retired record's title is still the commit that
        # explains the file. It is evidence, not instruction, so nothing here
        # needs the authority test.
        origin_story = _build_origin_story(
            query, git_meta, governing + candidates + retired
        )

        result_data: dict[str, Any] = {
            "mode": "path",
            "path": query,
            "decisions": governing,
            "origin_story": origin_story,
            # Alignment is scored over every matching record, not just the
            # ones that survived the cap — it is a coverage number, and
            # capping its input would make a well-governed hotspot look thin.
            # It reads the id, the title and the acceptance map, so the cheap
            # projection is the whole of what it needs.
            "alignment": _compute_alignment(
                query,
                [{"id": d.id, "title": d.title} for d in matched],
                all_decisions,
                currencies,
            ),
        }
        if candidates:
            # Named, counted and separated from the rules. An agent reading
            # this must be able to tell a request to review something from an
            # instruction to follow it, without parsing a status string.
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
            # Named as history, never as a rule. A reader asking why a file
            # looks the way it does is owed "this was decided and then
            # replaced", which is the sentence a dropped record cannot say.
            result_data["history"] = retired
            result_data["history_note"] = (
                f"{len(retired)} decision(s) governing this path were accepted "
                "and have since been superseded or withdrawn. They are history, "
                "not rules."
            )

        # --- Fallback: git archaeology when no accepted decision governs ---
        if not governing:
            result_data["git_archaeology"] = await _git_archaeology_fallback(
                query,
                git_meta,
                all_git_meta,
                repository,
                collector,
            )
            # Decisions and git history both silent → the "why" may live in a
            # code comment. Mine this file's rationale comments directly.
            rationale = _mine_rationale(
                ctx.path, [query], None, max_results=1000, truncate_blocks=False
            )
            if rationale:
                result_data["code_rationale"] = rationale

        # Episodes are additive rather than a fallback, unlike the two blocks
        # above. A well-governed file still has a history, and "what happened
        # here, dated" is the question this mode is asked; gating it on the
        # absence of decisions would hide it exactly where there is most to say.
        episode_population: list[dict] = []
        episodes, pending = await asyncio.to_thread(
            episode_evidence,
            ctx.path,
            paths=[query],
            full_population=episode_population,
        )
        if episodes:
            collector = _prepare_episode_bodies(
                episode_population, len(episodes), pending, collector, ctx.path
            )
            result_data["episodes"] = episode_population

        result_data["_meta"] = _build_meta(repository=repository)
        await _attach_response_decision_evidence(session, result_data, all_decisions)
        await annotate_response_evidence_async(
            result_data, ctx.alias, all_decisions, repo_root=ctx.path
        )
        _cap_supporting_lanes(result_data, collector, label=query)
        if episodes:
            cap_collection(
                result_data,
                "episodes",
                result_data["episodes"],
                len(episodes),
                collector,
                label="episodes beyond construction cap",
            )
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
        return _fit_path_response(result_data, ctx.path, collector=collector)


async def _build_target_context(
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    targets: list[str],
    collector: OmissionCollector | None = None,
    accepted: set[str] | None = None,
) -> dict[str, Any]:
    """Per-target governing decisions + origin story, with archaeology fallback.

    ``governing_decisions`` holds only records an acceptance binds; unaccepted
    ones go to ``candidate_decisions`` under the name they have earned. The two
    lanes were one, and on a store with no acceptances — the state of every
    repository whose maintainer has not worked through the acceptance UI — that
    one lane presented candidates as governing the file.

    The archaeology fallback is therefore keyed on the *accepted* lane. A file
    whose only records are candidates is a file no decision governs, which is
    exactly the case the fallback exists for, and its commits are stronger
    evidence than an unconfirmed candidate is.
    """
    async with get_session(ctx.session_factory) as session2:
        # Load all git metadata for cross-file search
        all_git_res = await session2.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repository.id,
            )
        )
        all_git_meta_list = all_git_res.scalars().all()

        target_context: dict[str, Any] = {}
        for t in targets:
            governing_records = []
            for d in all_decisions:
                # Same gate as ``_why_path``, which one target routes to.
                if not binds_to_paths(d.scope_basis):
                    continue
                affected = json.loads(d.affected_files_json)
                affected_mods = json.loads(d.affected_modules_json)
                if t in affected or any(t.startswith(m + "/") for m in affected_mods):
                    governing_records.append(d)
            governing_records.sort(key=_path_decision_sort_key)
            accepted_ids = accepted or set()
            rows = [
                {
                    "id": d.id,
                    "title": d.title,
                    "status": d.status,
                    "source": d.source,
                    "authority": _authority_of(d.id, accepted_ids),
                }
                for d in governing_records
            ]
            t_governing = [r for r in rows if r["authority"] == "accepted"]
            t_candidates = [r for r in rows if r["authority"] != "accepted"]
            git_m = target_git.get(t)
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
                    repository,
                    collector,
                )
            target_context[t] = ctx_entry
        return target_context


async def _why_targets(targets: list[str], repo: str | None) -> dict:
    """Mode 2b: targets and no query — the paths themselves are the question.

    One target is path mode outright: its lineage walk, alignment score and
    origin story are the fullest answer this tool has about a file, and that
    content is why the mode exists. Several get the per-target card instead —
    the same evidence a target already earns in search mode — because running
    path mode once per target would mean a corpus scan and a currency
    subprocess each, for a shape no caller renders.
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
            ctx, repository, all_decisions, target_git, targets, collector, accepted
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
