"""Search mode: a natural-language question, optionally anchored to targets."""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Callable, Collection
from typing import Any

from sqlalchemy.exc import SQLAlchemyError

from repowise.core.analysis.decision_semantic_match import DECISION_VECTOR_PREFIX
from repowise.core.analysis.next_call import render_call
from repowise.core.providers.embedding import store_has_semantic_vectors
from repowise.server.mcp_server._budget import OmissionCollector, cap_collection
from repowise.server.mcp_server._code_rationale import mine_rationale as _mine_rationale
from repowise.server.mcp_server._code_rationale import repo_rationale
from repowise.server.mcp_server._episodes import episode_evidence
from repowise.server.mcp_server._helpers import _decision_body
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server._why_evidence import annotate_response_evidence_async
from repowise.server.mcp_server._why_relevance import (
    clears_floor,
    query_scorer,
    question_terms,
    recovery_note,
    redirect_for,
    redirect_when_governed,
    redirect_when_served,
    relevance,
    term_idf,
    word_set,
)
from repowise.server.mcp_server.tool_why.archaeology import question_commits
from repowise.server.mcp_server.tool_why.basis import _has_archaeology, rests_on_decision
from repowise.server.mcp_server.tool_why.caps import (
    _MAX_SEARCH_DECISIONS,
    _SEMANTIC_WINDOW,
    _cap_episodes,
    _cap_supporting_lanes,
    _cap_target_context,
    _serve_episodes,
)
from repowise.server.mcp_server.tool_why.commit_bodies import attach_body_excerpts
from repowise.server.mcp_server.tool_why.lineage import _lineage_for_matches
from repowise.server.mcp_server.tool_why.loading import (
    _defining_files,
    _hydrate_response_decision_evidence,
    _load_corpus,
)
from repowise.server.mcp_server.tool_why.path_mode import _build_target_context
from repowise.server.mcp_server.tool_why.projection import _merge_decisions
from repowise.server.mcp_server.tool_why.ranking import (
    _collapse_restatements,
    _rank_keyword_matches,
    _record_text,
)


async def _fts_doc_results(ctx: Any, query: str) -> list:
    """Documentation hits from the lexical index.

    The keyless path, and the fallback whenever the vector store is present but
    unusable: a store that cannot rank gives the same answer as no store.
    """
    doc_results: list = []
    with contextlib.suppress(Exception):
        doc_results = await ctx.fts.search(query, limit=_SEMANTIC_WINDOW)
    return doc_results


async def _semantic_lanes(ctx: Any, query: str) -> tuple[list, list]:
    """``(decision_hits, doc_hits)`` from **one** embedding of *query*.

    One ``embed_texts`` plus ``search_by_vector`` (see ``vector_store._base``),
    with ``search`` as the fallback for a backend without raw-vector search. The
    window is split by namespace, so decisions never come back as documentation.

    The decision lane stays empty on a keyless index, deliberately: a window of
    arbitrary decisions is worse than none.
    """
    if not store_has_semantic_vectors(getattr(ctx, "vector_store", None)):
        return [], await _fts_doc_results(ctx, query)

    raw: list | None = None
    with contextlib.suppress(Exception):
        vectors = await ctx.vector_store.embed_texts([query], kind="query")
        if vectors:
            raw = await ctx.vector_store.search_by_vector(vectors[0], limit=_SEMANTIC_WINDOW)
        if raw is None:
            # Backend holds no embedder, or cannot search by raw vector.
            raw = await ctx.vector_store.search(query, limit=_SEMANTIC_WINDOW)
    if raw is None:
        return [], await _fts_doc_results(ctx, query)

    decision_hits, doc_hits = [], []
    for r in raw:
        page_id = getattr(r, "page_id", "")
        if page_id.startswith(DECISION_VECTOR_PREFIX):
            decision_hits.append(r)
        else:
            doc_hits.append(r)
    return decision_hits, doc_hits


def _focus_target_context(
    target_context: dict[str, Any],
    query: str,
    targets: list[str],
    all_decisions: list,
    served_ids: Collection[str] = (),
) -> None:
    """Drop from each target card whatever does not bear on *query*, in place.

    A card is built from a path, so without this it would answer every
    question about a file with the same bytes. Only when a query is present.

    Each emptied block becomes a count plus the call that recovers it, so "no
    history mentions what you asked" reads apart from "no history".
    """
    recall = f"get_why(targets={json.dumps(targets)})"
    # Term weights from the whole decision corpus: a card's few rows are too
    # small a sample for ``term_idf`` (see its warning).
    by_id = {d.id: d for d in all_decisions}
    record_score = query_scorer(query, [_record_text(d) for d in all_decisions])

    def relevant(row: Any) -> bool:
        return clears_floor(record_score(json.dumps(row, default=str)))

    for entry in target_context.values():
        if not isinstance(entry, dict):
            continue

        # ``governing_decisions`` is exempt: an accepted rule binds whatever the
        # question. A candidate binds nothing, so it must earn its place.
        _expand_answering_rules(entry, by_id, record_score, served_ids)
        _keep_relevant(entry, "candidate_decisions", relevant, recall)

        # Only the origin's decision lane; the author and first commit are
        # cheap and wanted whatever the question.
        origin = entry.get("origin")
        if isinstance(origin, dict):
            _keep_relevant(origin, "linked_decisions", relevant, recall)

        arch = entry.get("git_archaeology")
        if isinstance(arch, dict):
            _focus_archaeology(arch, query, recall)


#: A card rule's quoted body. Enough for the ruling; the rest is one id call away.
_CARD_DECISION_CHARS = 300


def _expand_answering_rules(
    entry: dict[str, Any],
    by_id: dict[str, Any],
    record_score: Callable[[str], float],
    served_ids: Collection[str],
) -> None:
    """Quote the rules on a card that answer the question, and list them first.

    The rest stay title-only: they bind the file but say nothing about what was
    asked. A rule already served in ``decisions`` is not quoted twice.
    """
    rows = entry.get("governing_decisions")
    if not isinstance(rows, list):
        return
    answering = []
    for row in rows:
        record = by_id.get(row.get("id")) if isinstance(row, dict) else None
        if record is None or row["id"] in served_ids:
            continue
        if clears_floor(record_score(_record_text(record))):
            row["decision"] = _bounded_body(record)
            answering.append(row)
    if answering:
        entry["governing_decisions"] = answering + [r for r in rows if r not in answering]


def _bounded_body(record: Any) -> str:
    body = _decision_body(record)
    if len(body) > _CARD_DECISION_CHARS:
        body = body[: _CARD_DECISION_CHARS - 3].rstrip() + "..."
    return body


_COMMIT_LANES = ("file_commits", "cross_references", "git_log")
# A commit carrying one question word shares a word with the question, no more.
_MIN_COMMIT_TERMS = 2
# The served commits must beat every other commit by this factor; a near tie
# says the question does not single any commit out, so the card serves none.
_COMMIT_MARGIN = 1.25
# Ties are equal sums of the same weights, up to float rounding.
_TIE_EPSILON = 1e-9


def _focus_archaeology(arch: dict[str, Any], query: str, recall: str) -> None:
    """Keep the card's best commits for *query*, count the rest.

    Here no decision cleared the floor, so these commits are the best evidence
    left. Scored on their messages by the question's prose terms, rarity over
    this card's commits; not the decision corpus, whose rarity is zero for every
    term on a store with no records.

    Nor the decision floor: a subject is one line, and the commits that answered
    measured questions carried 0.18 to 0.48 of their weight, so the floor served
    none. A card serves its strongest commits instead, ties kept, when they
    carry at least two of the question's terms and clear the margin.
    """
    # One entry per commit: the indexed lane and the live log repeat commits.
    key = _commit_keys(arch)
    words: dict[str, frozenset[str]] = {}
    for lane in _COMMIT_LANES:
        for r in arch.get(lane) or []:
            if isinstance(r, dict):
                words.setdefault(key[id(r)], _message_words(r))
    idf = term_idf(question_terms(query, prose=True), list(words.values()))
    score = {sha: relevance(w, idf) for sha, w in words.items()}
    eligible = {
        sha: s for sha, s in score.items() if sum(t in words[sha] for t in idf) >= _MIN_COMMIT_TERMS
    }
    best = max(eligible.values(), default=0.0)
    serve = {sha for sha, s in eligible.items() if best > 0 and s >= best - _TIE_EPSILON}
    runner_up = max((s for sha, s in score.items() if sha not in serve), default=0.0)
    if best < _COMMIT_MARGIN * runner_up:
        serve = set()

    served: set[str] = set()
    for lane in _COMMIT_LANES:
        rows = arch.get(lane)
        if not isinstance(rows, list) or not rows:
            continue
        kept, dropped = [], 0
        for r in rows:
            sha = key.get(id(r))
            if sha in serve and sha not in served:
                served.add(sha)
                kept.append(r)
            elif sha not in serve:
                dropped += 1
            # else: served in an earlier lane; not a suppressed commit.
        if kept:
            arch[lane] = kept
        else:
            arch.pop(lane)
            if dropped:
                arch[f"{lane}_omitted"] = recovery_note(dropped, recall)


def _full_sha(row: dict) -> str | None:
    """The row's full commit sha: its ``commit`` field or its commit evidence ref."""
    refs = row.get("evidence_refs") or []
    candidates = [row.get("commit"), row.get("sha")]
    candidates += [r.get("commit") for r in refs if isinstance(r, dict) and r.get("kind") == "commit"]
    for c in candidates:
        if isinstance(c, str) and _FULL_SHA_RE.fullmatch(c):
            return c.lower()
    return None


_FULL_SHA_RE = re.compile(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}")


def _commit_keys(arch: dict[str, Any]) -> dict[int, str]:
    """``{id(row): commit key}`` for every commit row of *arch*.

    The indexed lanes abbreviate a sha to eight characters and the live log to
    twelve, and either may carry the full sha beside it. A full sha is the key;
    an abbreviated one resolves to the card's single full sha it prefixes, else
    stays its own key. A row with no sha is its own commit.
    """
    rows = [r for lane in _COMMIT_LANES for r in arch.get(lane) or [] if isinstance(r, dict)]
    full = {id(r): _full_sha(r) for r in rows}
    known = {f for f in full.values() if f}
    keys: dict[int, str] = {}
    for r in rows:
        if full[id(r)]:
            keys[id(r)] = full[id(r)]
            continue
        short = str(r.get("sha") or "").lower()
        if not short:
            keys[id(r)] = f"row:{id(r)}"
            continue
        matches = [f for f in known if f.startswith(short)]
        keys[id(r)] = matches[0] if len(matches) == 1 else short
    return keys


def _message_words(row: dict) -> frozenset[str]:
    return word_set(str(row.get("message") or ""))


def _keep_relevant(
    block: dict[str, Any], key: str, relevant: Callable[[Any], bool], recall: str
) -> None:
    """Keep the rows of ``block[key]`` that are ``relevant``.

    When none are, the lane becomes ``<key>_omitted``: a count and the recall.
    """
    rows = block.get(key)
    if not isinstance(rows, list) or not rows:
        return
    kept = [r for r in rows if relevant(r)]
    if kept:
        block[key] = kept
    else:
        block.pop(key, None)
        block[f"{key}_omitted"] = recovery_note(len(rows), recall)


async def _why_no_match(
    query: str,
    targets: list[str] | None,
    ctx: Any,
    repository: Any,
    all_decisions: list,
    target_git: dict[str, Any],
    accepted: set[str] | None = None,
) -> dict[str, Any]:
    """The whole response when no record clears the relevance floor.

    Returns before the semantic lookup and episodes: nearest neighbours always
    return something, and serving them beside a redirect is padding.

    Named targets are the exception: their git archaeology and rationale
    comments are evidence about the thing asked, as in path mode. With no
    targets, the question itself is searched: rationale comments across the
    repository, else the commits carrying its terms. Both must clear the same
    floor the records missed, and are served as evidence, never as a decision.
    """
    result: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": [],
        **redirect_for(query),
    }
    collector: OmissionCollector | None = None
    if targets:
        collector = OmissionCollector("get_why", repo_root=ctx.path)
        result["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted
        )
        rationale = _mine_rationale(
            ctx.path,
            targets,
            query,
            max_results=1000,
            rarity_corpus=[word_set(_record_text(d)) for d in all_decisions],
            for_why=True,
        )
        if rationale:
            result["code_rationale"] = rationale
    else:
        result.update(await _question_lanes(ctx.path, query, plain=not accepted))
        if not _served_lanes(result):
            result.update(await _definition_pointer(ctx, repository, query))
    await _hydrate_response_decision_evidence(ctx, result, all_decisions)
    await annotate_response_evidence_async(
        result, ctx.alias, all_decisions, repo_root=ctx.path
    )
    if collector is not None:
        _focus_target_context(
            result["target_context"], query, list(targets or []), all_decisions
        )
        _cap_supporting_lanes(result, collector, label=query)
        _cap_target_context(result["target_context"], collector)
    await attach_body_excerpts(result, ctx.path, target_git, query)
    # Don't redirect away from evidence this response is already serving.
    served = _served_lanes(result)
    if served:
        result.pop("try_instead", None)
        recall = f"get_why(targets={json.dumps(targets)})" if targets else None
        result.update(redirect_when_served(served, recall))
    elif rests_on_decision(result):
        result.pop("try_instead", None)
        result.pop("reason", None)
    elif any(
        isinstance(e, dict) and e.get("governing_decisions")
        for e in (result.get("target_context") or {}).values()
    ):
        result.update(redirect_when_governed())
    result["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    if collector is not None:
        collector.attach(result)
    return result


async def _question_lanes(repo_root: Any, query: str, *, plain: bool) -> dict[str, Any]:
    """Rationale comments carrying *query*, else the commits that do, else nothing.

    *plain* admits comments with no rationale marker: on a store with no
    accepted decision, the code's own comments are the only stated reasons.
    """
    rationale = await asyncio.to_thread(repo_rationale, repo_root, query, plain=plain)
    if rationale:
        return {"code_rationale": rationale}
    commits = await asyncio.to_thread(question_commits, repo_root, query)
    if not commits:
        return {}
    return {
        "git_archaeology": {
            "triggered": True,
            "git_log": commits,
            "summary": (
                f"No recorded rationale for this question. {len(commits)} "
                "commit(s) carry its terms: history, not a stated reason."
            ),
        }
    }


async def _definition_pointer(ctx: Any, repository: Any, query: str) -> dict[str, Any]:
    """Where the code a refused question names is defined, and the call that reads it.

    A question naming no file is searched by its words alone. When those find
    nothing, the file defining a name it uses still has comments and history a
    targeted call reads. Empty when the question names no indexed code.
    """
    try:
        defined = await _defining_files(ctx, repository.id, query)
    except SQLAlchemyError:
        return {}
    if not defined:
        return {}
    paths = list(dict.fromkeys(d["path"] for d in defined))
    call = render_call("get_why", {"query": query, "targets": paths})
    return {"defined_in": defined, "next_call": call}


def _served_lanes(result: dict[str, Any]) -> list[str]:
    """Labels of the evidence lanes *result* carries, top level or on a target card."""
    cards = (result.get("target_context") or {}).values()
    return [
        label
        for key, label in (
            ("code_rationale", "rationale comments"),
            ("git_archaeology", "commit history"),
        )
        if result.get(key)
        or any(isinstance(e, dict) and _has_archaeology(e.get(key)) for e in cards)
    ]


async def _why_search(query: str, targets: list[str] | None, repo: str | None) -> dict:
    """Mode 3: natural-language, target-aware decision + documentation search."""
    ctx, repository, all_decisions, target_git, accepted = await _load_corpus(
        repo, targets
    )

    target_set = set(targets) if targets else set()
    # Rank wide, collapse, and project the whole pool; the cap comes last so
    # the rows it sheds are banked whole for recovery.
    ranked = _rank_keyword_matches(all_decisions, query, target_set)
    if not ranked:
        return await _why_no_match(
            query, targets, ctx, repository, all_decisions, target_git, accepted
        )
    collector = OmissionCollector("get_why", repo_root=ctx.path)
    collapsed = _collapse_restatements(ranked)
    decision_results, doc_results = await _semantic_lanes(ctx, query)
    lineage_by_id = await _lineage_for_matches(
        ctx, [d for d, _ in collapsed], all_decisions
    )
    merged_decisions = _merge_decisions(
        collapsed, decision_results, lineage_by_id, collector, accepted
    )

    # Semantic hits are small id-plus-snippet rows, not worth dropping.
    result_data: dict[str, Any] = {
        "mode": "search",
        "query": query,
        "decisions": merged_decisions,
        "related_documentation": _related_documentation(query, all_decisions, doc_results),
    }

    if targets:
        result_data["target_context"] = await _build_target_context(
            ctx, repository, all_decisions, target_git, targets, accepted
        )

    # Targets resolve through the node index; without them the question itself
    # is the only handle, so it is ranked against the bodies.
    episode_population: list[dict] = []
    episodes, pending = await asyncio.to_thread(
        episode_evidence,
        ctx.path,
        paths=targets or None,
        query=None if targets else query,
        full_population=episode_population,
    )
    if episodes:
        episodes, pending = _focus_episodes(
            query, all_decisions, episodes, episode_population, pending
        )
    collector = _serve_episodes(
        result_data, episodes, episode_population, pending, collector, ctx.path
    )

    await _hydrate_response_decision_evidence(ctx, result_data, all_decisions)
    await annotate_response_evidence_async(
        result_data, ctx.alias, all_decisions, repo_root=ctx.path
    )
    if targets:
        _focus_target_context(
            result_data["target_context"],
            query,
            list(targets),
            all_decisions,
            {d["id"] for d in result_data["decisions"][:_MAX_SEARCH_DECISIONS] if "decision" in d},
        )
    _cap_supporting_lanes(result_data, collector, label=query)
    if targets:
        _cap_target_context(result_data["target_context"], collector)
    _cap_episodes(result_data, episodes, collector)
    result_data["_meta"] = _build_meta(repository=repository, targets=targets if targets else None)
    # The middleware owns the search-mode shed order; the path-only fitter would
    # drop the primary decision lane too early.
    cap_collection(
        result_data,
        "decisions",
        result_data["decisions"],
        _MAX_SEARCH_DECISIONS,
        collector,
        label=f"search decisions beyond cap={_MAX_SEARCH_DECISIONS}",
    )
    cap_collection(
        result_data,
        "related_documentation",
        result_data["related_documentation"],
        3,
        collector,
        label="related_documentation beyond cap=3",
    )
    collector.attach(result_data)
    return result_data


def _episode_text(e: dict) -> str:
    """What an episode says, without its field names or currency prose."""
    scope = e.get("scope")
    files = scope.get("matching") or [] if isinstance(scope, dict) else []
    parts = [e.get("subject"), e.get("recorded"), e.get("evidence"), *files]
    return " ".join(str(p) for p in parts if p).lower()


def _floor_scorer(
    query: str, all_decisions: list, population: list[str]
) -> Callable[[str], float]:
    """``query_scorer`` over the decision corpus plus *population*, so an empty store still weighs terms."""
    return query_scorer(query, [_record_text(d) for d in all_decisions] + population)


def _related_documentation(
    query: str, all_decisions: list, doc_results: list
) -> list[dict[str, Any]]:
    """Documentation hits that clear the relevance floor, as served rows.

    A nearest-neighbour window always returns pages; one sharing a word with
    the question is not about it.
    """
    texts = [f"{r.title} {r.snippet}".lower() for r in doc_results]
    score = _floor_scorer(query, all_decisions, texts)
    return [
        {
            "page_id": r.page_id,
            "title": r.title,
            "page_type": r.page_type,
            "snippet": r.snippet,
            "relevance_score": r.score,
        }
        for r, text in zip(doc_results, texts, strict=True)
        if clears_floor(score(text))
    ]


def _focus_episodes(
    query: str,
    all_decisions: list,
    episodes: list[dict],
    population: list[dict],
    pending: list[tuple[dict, str, str]],
) -> tuple[list[dict], list[tuple[dict, str, str]]]:
    """Narrow episodes to those bearing on *query*.

    Trims *population* in place and returns the matching ``(episodes,
    pending)``, unchanged when every episode clears the floor.
    """
    # Scoped retrieval ignores the query and full-text retrieval matches any
    # one shared word, so both are held to the floor here.
    texts = [_episode_text(e) for e in population]
    score = _floor_scorer(query, all_decisions, texts)
    kept = [e for e, text in zip(population, texts, strict=True) if clears_floor(score(text))]
    if len(kept) == len(population):
        return episodes, pending
    population[:] = kept
    return [e for e in episodes if e in kept], [t for t in pending if t[0] in kept]
