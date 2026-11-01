"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import HISTORY_CURRENCIES, is_governing
from repowise.core.analysis.decisions.scope import binds_to_paths
from repowise.core.persistence.crud.repository import get_repository_by_path
from repowise.core.persistence.models import (
    Repository,
)

# Re-exported: MCP tools import their helpers from here, but the definition
# lives in core because the CRUD layer needs the same escaping.
from repowise.core.persistence.sql import (  # noqa: F401
    LIKE_ESCAPE,
    escape_like,
    is_missing_table,
)
from repowise.server.mcp_server import _checkout, _state
from repowise.server.mcp_server._query_shape import _is_path  # noqa: F401
from repowise.server.mcp_server._why_relevance import floor_ranked, question_terms

_log = logging.getLogger("repowise.mcp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Ceiling on one vector-store query, seconds. The first query in a process pays
# for the store open, the first embed and the first ANN probe; #1678 measured
# 6.3s + 13.4s on a cold Windows index where a warm query takes 0.19s. The old
# 8s budget therefore expired on the first query of every process and the
# vector leg degraded to full-text with nothing said. Raise it with
# REPOWISE_VECTOR_SEARCH_TIMEOUT_S on a slow disk or a very large index.
_VECTOR_TIMEOUT_ENV = "REPOWISE_VECTOR_SEARCH_TIMEOUT_S"
_VECTOR_TIMEOUT_DEFAULT_S = 30.0
# A search an agent blocks on; past this the client's own tool timeout fires
# first, so a larger value cannot produce results anyone still accepts.
_VECTOR_TIMEOUT_MAX_S = 120.0
# How long hybrid search waits for the vector leg once the other legs are in.
# A warm leg takes ~0.3s (one embedding round trip); a cold store or a
# rate-limited embedder takes 5-13s, and full-text hits are ready long before.
_SEMANTIC_GRACE_ENV = "REPOWISE_SEMANTIC_GRACE_S"
_SEMANTIC_GRACE_DEFAULT_S = 1.0
# How long a vector leg waits for the store to finish loading at server start.
_VECTOR_STORE_READY_TIMEOUT_S = 30.0


def vector_search_timeout_s() -> float:
    """Seconds one vector query may take, from env or the cold-start default."""
    return _env_seconds(_VECTOR_TIMEOUT_ENV, _VECTOR_TIMEOUT_DEFAULT_S)


def semantic_grace_s() -> float:
    """Seconds hybrid search waits for the vector leg after the other legs finish."""
    return _env_seconds(_SEMANTIC_GRACE_ENV, _SEMANTIC_GRACE_DEFAULT_S)


def _env_seconds(env: str, default: float) -> float:
    """A positive number of seconds from *env*, capped, else *default*.

    An unparseable or non-positive value warns and keeps the default instead of
    silently disabling the wait, matching how REPOWISE_EMBEDDING_TIMEOUT is
    resolved.
    """
    raw = (os.environ.get(env) or "").strip()
    if not raw:
        return default
    try:
        seconds = float(raw)
    except ValueError:
        seconds = float("nan")
    # NaN fails this too, so it lands on the default rather than on a budget
    # asyncio.wait_for would treat as already expired.
    if not seconds > 0:
        _log.warning("Ignoring unusable %s=%r", env, raw)
        return default
    return min(seconds, _VECTOR_TIMEOUT_MAX_S)


# Budget for embedding a get_answer question before question_vector() gives up
# and falls back to a lexical-only answer. 8s suits a warm hosted endpoint; a
# locally served model that has just been swapped in pays a cold load first
# and blows it — the run still succeeds (question_vector logs a warning and
# returns None), so nothing an operator isn't already tailing structlog for
# marks the semantic leg as lost. Raise it with REPOWISE_EMBED_TIMEOUT_S to
# buy a cold local model the time a warm hosted one never needed.
_EMBED_TIMEOUT_ENV = "REPOWISE_EMBED_TIMEOUT_S"
_EMBED_TIMEOUT_DEFAULT_S = 8.0
# Same client-side constraint as _VECTOR_TIMEOUT_MAX_S above: past this the
# MCP client's own tool-call timeout fires first, so a larger value here
# cannot produce results anyone still accepts.
_EMBED_TIMEOUT_MAX_S = 120.0


def embed_timeout_s() -> float:
    """Seconds one question-embedding call may take, from env or the default.

    An unparseable or non-positive value warns and keeps the default instead of
    silently disabling the leg, matching :func:`vector_search_timeout_s` and
    REPOWISE_EMBEDDING_TIMEOUT.
    """
    raw = (os.environ.get(_EMBED_TIMEOUT_ENV) or "").strip()
    if not raw:
        return _EMBED_TIMEOUT_DEFAULT_S
    try:
        seconds = float(raw)
    except ValueError:
        seconds = float("nan")
    if not seconds > 0:
        _log.warning("Ignoring unusable %s=%r", _EMBED_TIMEOUT_ENV, raw)
        return _EMBED_TIMEOUT_DEFAULT_S
    return min(seconds, _EMBED_TIMEOUT_MAX_S)


# ---------------------------------------------------------------------------
# Repository resolution
# ---------------------------------------------------------------------------


_warned_ambiguous: set[str] = set()


async def _get_repo(session: AsyncSession, repo: str | None = None) -> Repository:
    """Resolve a repository by path, ID or name; with none, the served directory's."""
    if repo:
        # Try by path
        result = await session.execute(select(Repository).where(Repository.local_path == repo))
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        # Try the resolved path: case, separators and a trailing slash may differ
        if Path(repo).is_absolute():
            obj = await get_repository_by_path(session, str(Path(repo).resolve()))
            if obj:
                return obj
        # Try by ID
        obj = await session.get(Repository, repo)
        if obj:
            return obj
        # Try by name
        result = await session.execute(select(Repository).where(Repository.name == repo))
        obj = result.scalar_one_or_none()
        if obj:
            return obj
        raise LookupError(f"Repository not found: {repo}")
    return await _default_repo(session)


async def _default_repo(session: AsyncSession) -> Repository:
    """The served directory's row, else the only (or first) row."""
    served = _state._repo_path
    if served:
        for key in dict.fromkeys((served, str(Path(served).resolve()))):
            obj = await get_repository_by_path(session, key)
            if obj:
                return obj
    rows = (await session.scalars(select(Repository).limit(2))).all()
    if not rows:
        raise LookupError("No repositories found. Run 'repowise init' first.")
    if len(rows) > 1 and served and served not in _warned_ambiguous:
        # A store split when an older version indexed a moved or copied repo.
        _warned_ambiguous.add(served)
        _log.warning(
            "The index holds several repositories and none at %s; serving %s. "
            "Pass `repo` to pick another.",
            served,
            rows[0].local_path,
        )
    return rows[0]


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Workspace-aware repo context resolution
# ---------------------------------------------------------------------------


def clean_signature(signature: str | None) -> str:
    """Collapse a stored signature onto one line.

    Signatures indexed from CRLF files carry literal ``\\r\\n`` plus the
    original indentation. Whitespace runs collapse to single spaces; the text
    is unchanged otherwise.
    """
    return " ".join((signature or "").split())


def _is_workspace_mode() -> bool:
    """Return True if the MCP server is running in workspace mode."""
    return _state._registry is not None


def _cross_repo_cut(alias: str | None) -> dict:
    """``lookup`` and ``alias`` for the dead-code cross-repo cut
    (``serve_findings``) on the repo *alias* names; both ``None`` outside
    workspace mode, and an empty artifact simply cuts nothing."""
    if not _is_workspace_mode():
        return {"lookup": None, "alias": None}
    return {"lookup": _state._cross_repo_enricher, "alias": alias}


async def _resolve_repo_context(repo: str | None = None) -> Any:
    """Resolve the per-repo resource context for the given ``repo`` parameter.

    In **single-repo mode** (no registry): returns a lightweight wrapper
    around the existing ``_state`` globals — zero overhead, full backward
    compatibility.

    In **workspace mode**: resolves the alias via the registry and returns
    the matching ``RepoContext``.

    Raises ``ValueError`` for ``repo="all"`` — callers must handle that
    case explicitly before calling this helper.
    """
    from repowise.core.workspace.registry import RepoContext

    registry = _state._registry
    if registry is None:
        # Single-repo mode — validate the repo param against the DB if given
        if repo is not None:
            own = await _single_repo_checkout(repo)
            if own is not None:
                return own

        return RepoContext(
            alias="default",
            path=Path(_state._repo_path) if _state._repo_path else Path.cwd(),
            session_factory=_state._session_factory,
            fts=_state._fts,
            vector_store=_state._vector_store,
            decision_store=_state._decision_store,
            vector_store_ready=_state._vector_store_ready,
            _engine=None,
        )

    # Workspace mode — resolve via registry
    resolved = registry.resolve_repo_param(repo)
    if isinstance(resolved, list):
        raise ValueError(
            "repo='all' must be handled explicitly by each tool. "
            "Fan out with _federation.Federation instead."
        )
    # Only a path can name another checkout; an alias resolves as before.
    is_path = repo is not None and Path(repo).is_absolute()
    matched = registry.match_checkout(repo) if is_path else None
    if matched is not None and matched[0] == resolved:
        entry = registry.ws_config.get_repo(resolved)
        indexed = (registry.workspace_root / entry.path).resolve()
        if _checkout.note_match(matched[1], indexed):
            return await _checkout.own_index_context(resolved, matched[1].checkout)
    elif repo is not None:
        _checkout.note_match(None, Path())
    return await registry.get(resolved)


async def _single_repo_checkout(repo: str) -> Any:
    """Validate *repo* on a single-repo server; a worktree's own context or None.

    A row the store knows (path, id or name) is the served repo. Otherwise an
    absolute path inside the served checkout, or inside another worktree of
    it, resolves too; anything else raises ``LookupError`` as before.
    """
    from repowise.core.git_head import match_checkout
    from repowise.core.persistence.database import get_session as _get_session

    async with _get_session(_state._session_factory) as session:
        try:
            await _get_repo(session, repo)
        except LookupError:
            served = _state._repo_path
            matched = (
                match_checkout(repo, served) if served and Path(repo).is_absolute() else None
            )
            if matched is None:
                raise
        else:
            _checkout.note_match(None, Path())
            return None
    if _checkout.note_match(matched, Path(served).resolve()):
        return await _checkout.own_index_context("default", matched.checkout)
    return None


async def wait_for_vector_store(ctx: Any) -> None:
    """Wait, bounded, for a vector store still loading at startup.

    A single-repo context is a copy of ``_state`` taken when the tool started,
    so one taken mid-load still holds the keyless placeholder and its vector
    leg would quietly return nothing. It is pointed at the loaded stores here.
    Workspace contexts are updated in place by their registry.
    """
    ready = getattr(ctx, "vector_store_ready", None)
    if ready is None:
        return
    with contextlib.suppress(TimeoutError):
        await asyncio.wait_for(ready.wait(), timeout=_VECTOR_STORE_READY_TIMEOUT_S)
    if _state._registry is None and ready is _state._vector_store_ready:
        ctx.vector_store = _state._vector_store
        ctx.decision_store = _state._decision_store


def _unsupported_repo_all(tool_name: str) -> dict:
    """Return an error dict for tools that don't support ``repo='all'``."""
    from repowise.server.mcp_server._savings.interaction import note_error_reason

    note_error_reason("invalid_args")
    registry = _state._registry
    available = registry.get_all_aliases() if registry is not None else []
    return {
        "error": (
            f"repo='all' is not supported for {tool_name}. "
            f"Specify a repo alias instead. Available: {available}"
        ),
    }


# ---------------------------------------------------------------------------
# Closed-vocabulary arguments (used by get_dead_code, get_context, search_codebase)
# ---------------------------------------------------------------------------


def resolve_enum_argument(
    value: str | None,
    valid: Collection[str],
    *,
    argument: str,
    ignored: list[dict[str, Any]],
) -> str | None:
    """Return *value* if it is in *valid*, else drop it and record it in *ignored*.

    Same rule as ``get_health``'s ``unknown_only_keys``: an argument the tool
    does not recognise is named rather than applied. Applying it is what makes
    a typo indistinguishable from a real negative — a misspelled filter matches
    nothing and the tool reports the empty result as an answer (issue #1496).
    Dropping it and saying so is recoverable; raising is not, because the caller
    loses the answer it could still have had.

    Repeated calls for one *argument* (``include`` takes a list) collect into a
    single entry, so the vocabulary is spelled out once however many values miss.
    """
    if value is None or value in valid:
        return value
    for entry in ignored:
        if entry["argument"] == argument:
            entry["values"].append(value)
            return None
    ignored.append({"argument": argument, "values": [value], "valid": sorted(valid)})
    return None


def attach_ignored_arguments(result: dict[str, Any], ignored: list[dict[str, Any]]) -> None:
    """Name the arguments the tool dropped, at the top level, or add nothing."""
    if ignored:
        result["ignored_arguments"] = ignored


# ---------------------------------------------------------------------------
# Origin story & alignment (used by get_context, get_why)
# ---------------------------------------------------------------------------

#: Decisions the origin summary names, and the ceiling on its evidence clauses.
_ORIGIN_NARRATED_DECISIONS = 3


def _decision_body(record: object) -> str:
    """Effective one-line body for a decision record.

    The anti-hallucination gate clears a paraphrased ``decision`` that is not a
    verbatim substring of the source while a survivor's evidence quote keeps the
    record alive, which historically left ``llm_inferred`` records with an empty
    ``decision`` and only a title to show. The harvest path now promotes the
    title into ``decision`` at write time; this fall back covers records stored
    before that fix so read surfaces never emit a body-less decision. The title
    is the model's own canonical one-line summary of the choice and is always
    present.
    """
    return (getattr(record, "decision", "") or "").strip() or (
        getattr(record, "title", "") or ""
    ).strip()


def _commits_matching_decision(decision: dict, commits: list[dict]) -> list[dict]:
    """Commits whose subject clears the relevance floor for the decision's title.

    The title is the record's one-line statement, so a commit is evidence when
    it says most of what the title says, rarer words weighing more. Rarity
    comes from *commits*, the file's own history: a word every commit on the
    file shares names the file, not the decision. Any one shared word used to
    link nearly every commit to every decision.
    """
    statement = decision.get("title") or decision.get("decision") or ""
    terms = question_terms(statement)
    matched = floor_ranked(statement, commits, lambda c: c.get("message", ""), len(commits))
    return [
        {
            "sha": c.get("sha", ""),
            "message": c.get("message", ""),
            "author": c.get("author", ""),
            "date": c.get("date", ""),
            "matching_keywords": [t for t in terms if t in c.get("message", "").lower()][:5],
        }
        for c in matched
    ]


def _link_decisions_to_commits(governing_decisions: list[dict], commits: list[dict]) -> list[dict]:
    """The governing decisions that commits state, each with those commits.

    A decision no commit states is left out: without evidence the row would
    only repeat the decision lane.
    """
    linked_decisions = []
    for d in governing_decisions:
        evidence = _commits_matching_decision(d, commits)
        if not evidence:
            continue
        linked_decisions.append(
            {
                "title": d.get("title", ""),
                "status": d.get("status", ""),
                "source": d.get("source", ""),
                "rationale": d.get("rationale", ""),
                "evidence_commits": evidence,
            }
        )
    return linked_decisions


def _origin_span(first_date: str, last_date: str, age: int, capped: bool) -> str:
    """When the file was made and last changed.

    *capped* history (the commit cap, or a shallow clone's cut) starts after
    the file was made, so its first commit is not called the creation.
    """
    if capped:
        return f"History starts ~{first_date} (older commits not indexed), last modified {last_date}."
    return f"Created ~{first_date}, last modified {last_date} ({age} days old)."


def _origin_summary_parts(
    authors: list,
    earliest_commit: dict | None,
    narrated: list[tuple[str, dict | None]],
    primary: str,
    total: int,
    span: str,
) -> list[str]:
    """Assemble the narrative sentences for an origin story.

    *narrated* is ``(title, first evidence commit or None)`` per governing
    decision the summary names; *span* is :func:`_origin_span`'s sentence.
    """
    parts = [
        span,
        f"Primary author: {primary} ({total} total commits).",
    ]

    if earliest_commit:
        parts.append(
            f'Earliest key commit: "{earliest_commit.get("message", "")}" '
            f"by {earliest_commit.get('author', 'unknown')} on {earliest_commit.get('date', 'unknown')}."
        )

    if narrated:
        parts.append(f"Governed by: {', '.join(title for title, _ in narrated)}.")
        for title, ec in narrated:
            if ec is not None:
                parts.append(f'Commit "{ec["message"]}" by {ec["author"]} is evidence for "{title}".')

    if len(authors) > 1:
        names = [a.get("name", "") for a in authors[:3]]
        parts.append(f"Contributors: {', '.join(names)}.")

    return parts


def _build_origin_story(
    file_path: str,
    git_meta: Any | None,
    governing_decisions: list[dict],
) -> dict:
    """Build the human context / origin story for a file from stored metadata."""
    if git_meta is None:
        return {
            "available": False,
            "summary": f"No git history is indexed for {file_path}.",
        }

    authors = json.loads(git_meta.top_authors_json) if git_meta.top_authors_json else []
    commits = (
        json.loads(git_meta.significant_commits_json) if git_meta.significant_commits_json else []
    )

    # Find the earliest significant commit as the "creation" context
    earliest_commit = None
    if commits:
        earliest_commit = sorted(commits, key=lambda c: c.get("date", ""))[0]

    linked_decisions = _link_decisions_to_commits(governing_decisions, commits)
    first_evidence = {ld["title"]: ld["evidence_commits"][0] for ld in linked_decisions}
    # Capped: unbounded, a file with 40 linked decisions got 16 evidence
    # clauses, restating titles the caller's own cap had already removed.
    narrated = [
        (title, first_evidence.get(title))
        for title in (d.get("title", "") for d in governing_decisions[:_ORIGIN_NARRATED_DECISIONS])
    ]

    primary = git_meta.primary_owner_name or "unknown"
    total = git_meta.commit_count_total or 0
    first_date = (
        git_meta.first_commit_at.strftime("%Y-%m-%d") if git_meta.first_commit_at else "unknown"
    )
    last_date = (
        git_meta.last_commit_at.strftime("%Y-%m-%d") if git_meta.last_commit_at else "unknown"
    )
    age = git_meta.age_days or 0

    parts = _origin_summary_parts(
        authors,
        earliest_commit,
        narrated,
        primary,
        total,
        _origin_span(
            first_date, last_date, age, bool(getattr(git_meta, "commit_count_capped", False))
        ),
    )

    return {
        "available": True,
        "primary_author": primary,
        "author_commit_pct": git_meta.primary_owner_commit_pct,
        "author_line_pct": getattr(git_meta, "primary_owner_line_pct", None),
        "contributors": authors,
        "total_commits": total,
        "first_commit": first_date,
        "last_commit": last_date,
        "age_days": age,
        "key_commits": commits,
        "linked_decisions": linked_decisions,
        "summary": " ".join(parts),
    }


def _sibling_coverage(
    file_path: str,
    governing: list[dict],
    all_decisions: list,
    accepted_ids: set[str],
) -> float | None:
    """Fraction of sibling-file decisions that also cover *file_path* (None if no siblings).

    Both sides count accepted records only. Filtering the numerator alone read
    a directory whose siblings are covered by proposals as one this file
    diverges from, when nothing there has been agreed to either.
    """
    dir_path = "/".join(file_path.split("/")[:-1])
    sibling_decision_ids = set()
    file_decision_titles = {d["title"] for d in governing}

    for d in all_decisions:
        if getattr(d, "id", None) not in accepted_ids:
            continue
        # An accepted footprint would count as a covered sibling in every
        # directory it touched.
        if not binds_to_paths(getattr(d, "scope_basis", "")):
            continue
        affected = json.loads(d.affected_files_json)
        for af in affected:
            af_dir = "/".join(af.split("/")[:-1])
            if af_dir == dir_path and af != file_path:
                sibling_decision_ids.add(d.title)

    if not sibling_decision_ids:
        return None  # No siblings to compare
    shared = file_decision_titles & sibling_decision_ids
    return len(shared) / len(sibling_decision_ids)


def _active_alignment(active: list, dir_path: str, sibling_coverage: float | None) -> tuple:
    """Score/explanation when active decisions govern the file."""
    if sibling_coverage is not None and sibling_coverage >= 0.5:
        return "high", (
            f"Follows {len(active)} active decision(s) shared with sibling files. "
            f"This file aligns with established patterns in {dir_path}/."
        )
    if sibling_coverage is not None and sibling_coverage < 0.5:
        return "medium", (
            f"Has {len(active)} active decision(s) but limited overlap with "
            f"sibling files in {dir_path}/. May use a different pattern than neighbors."
        )
    return "high", f"Governed by {len(active)} active decision(s)."


def _alignment_score(
    accepted: list,
    deprecated: list,
    stale: list,
    candidates: list,
    dir_path: str,
    sibling_coverage: float | None,
) -> tuple:
    """Derive the (score, explanation) tuple from accepted decisions.

    Only accepted records score, and accepted means an acceptance row exists,
    not that the status column reads ``active``. A candidate awaiting review
    scores nothing: counting one as governance let a machine-inferred record
    report a file as aligned with a decision nobody had agreed to.
    """
    if stale and len(stale) >= len(accepted) / 2:
        return "low", (
            f"{len(stale)} of {len(accepted)} accepted decision(s) are stale. "
            f"The architectural rationale may no longer apply."
        )
    if accepted:
        return _active_alignment(accepted, dir_path, sibling_coverage)
    if deprecated:
        trailer = (
            f" {len(candidates)} proposed candidate(s) await review."
            if candidates
            else ""
        )
        return "low", (
            "Every accepted decision here is deprecated/superseded, or names only "
            "files that no longer exist. "
            "This file likely contains technical debt that should be migrated." + trailer
        )
    if candidates:
        return "none", (
            f"No accepted decision governs this file. {len(candidates)} proposed "
            f"candidate(s) mention it, awaiting review."
        )
    return "none", "No accepted decision governs this file."


def _compute_alignment(
    file_path: str,
    governing: list[dict],
    all_decisions: list,
    currencies: dict[str, str],
) -> dict:
    """Compute how well a file aligns with established architectural decisions.

    *currencies* maps decision id to effective currency for every **accepted**
    record in the repository; a record absent from it is a candidate. That map
    is the authority test, and it is a required argument rather than an
    optional one on purpose. This function previously read
    ``status == "active"`` and called the result ``accepted``, so a record
    written straight to the column with no acceptance behind it made a file
    report as governed. Defaulting the argument would have left that path
    reachable from any caller that forgot it.
    """
    if not governing:
        # With nothing accepted anywhere, "ungoverned" describes the index, not
        # this file, so say that instead.
        reason = (
            "This file is ungoverned — it may be an outlier or simply undocumented."
            if currencies
            else "This index holds no accepted decisions, so no file is governed "
            "yet; review `repowise decision candidates` and accept one with "
            "`repowise decision confirm <id> --agent <your slug>`, adding "
            "`--evidence <commit|file>` or `--reason` if it lacks them."
        )
        return {
            "score": "none",
            "explanation": f"No architectural decisions govern {file_path}. {reason}",
            "governing_count": 0,
            "active_count": 0,
            "candidate_count": 0,
            "deprecated_count": 0,
            "stale_count": 0,
            "sibling_coverage": None,
        }

    # Three lanes, split by the acceptance rather than by the column: what a
    # person accepted and still binds, what they accepted and then withdrew,
    # and what nobody has reviewed.
    accepted = [d for d in governing if is_governing(currencies.get(d["id"], ""))]
    deprecated = [
        d
        for d in governing
        if currencies.get(d["id"]) in HISTORY_CURRENCIES
    ]
    # Repository-wide, not scoped to the matched records: the sibling
    # denominator is drawn from decisions naming *other* files in the same
    # directory, none of which appear in ``governing``.
    accepted_ids = {
        did for did, currency in currencies.items() if is_governing(currency)
    }
    # ``needs_review`` is the derived answer to "have the files it names
    # moved", which is the same 0.5 threshold this used to apply by hand.
    stale = [d for d in accepted if currencies.get(d["id"]) == "needs_review"]
    candidates = [d for d in governing if d["id"] not in currencies]
    # Accepted, and neither binding nor withdrawn: it names nothing the
    # repository can be asked about. Counted on its own so the four lanes add
    # up to the records naming the file; without it a reader could find
    # records unaccounted for with nothing saying where they went.
    uncheckable = [d for d in governing if currencies.get(d["id"]) == "uncheckable"]

    dir_path = "/".join(file_path.split("/")[:-1])
    # Scoped to accepted records for the same reason the score is: a sibling
    # pattern established only by candidates is not an established pattern.
    sibling_coverage = _sibling_coverage(
        file_path, accepted, all_decisions, accepted_ids
    )

    score, explanation = _alignment_score(
        accepted, deprecated, stale, candidates, dir_path, sibling_coverage
    )

    return {
        "score": score,
        "explanation": explanation,
        # Accepted records that still bind, so it never disagrees with a note
        # saying the candidates govern nothing. The four lane counts below
        # (accepted, withdrawn, uncheckable, candidate) sum to the records
        # naming this file.
        "governing_count": len(accepted),
        "active_count": len(accepted),
        "candidate_count": len(candidates),
        # Accepted once and now history: superseded, dismissed, or naming only
        # files gone at HEAD.
        "deprecated_count": len(deprecated),
        "uncheckable_count": len(uncheckable),
        "stale_count": len(stale),
        "sibling_coverage": round(sibling_coverage, 2) if sibling_coverage is not None else None,
    }


# ---------------------------------------------------------------------------
# Per-repo exclude_patterns filtering (issue 5 of #296)
#
# Excluded files are skipped at ingest time, but rows may predate an
# exclude_patterns change, so MCP tools filter their results at query time too.
# ---------------------------------------------------------------------------


def read_repo_file_text(
    repo_root: Path | str | None, file_path: str, *, allow_extracted: bool = True
) -> str | None:
    """Read a repo-relative file's live text, or None when it cannot be served.

    Refuses any path that resolves outside *repo_root*: several tools serve
    live bytes for a path that came out of the index, and an index row is not
    a trust boundary. Decoding is lossy on purpose (``errors="replace"``): a
    card describing a latin-1 file should degrade to mojibake in one field,
    rather than failing the whole call.

    Source-coordinate readers must set ``allow_extracted=False``. Async callers
    allowing extraction must offload this synchronous reader.

    Classification, containment and conversion belong to the core source reader;
    this optional serving boundary translates extraction failures to unavailable.
    """
    if repo_root is None:
        return None
    from repowise.core.ingestion.document_extractors import DocumentExtractionError
    from repowise.core.ingestion.source_reader import read_source

    try:
        source = read_source(Path(str(repo_root)), file_path, allow_extracted=allow_extracted)
        if source is None:
            return None
        return source.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, ValueError, DocumentExtractionError):
        return None


def _get_exclude_spec(repo_path: Path | str) -> Any:
    """Compile the repo's exclusion rules into a PathSpec, or None."""
    from repowise.core.exclusion import build_exclude_spec

    return build_exclude_spec(repo_path)


def is_excluded(path: str | None, spec: Any) -> bool:
    """True if *path* matches *spec* (None spec or path -> not excluded)."""
    from repowise.core.exclusion import is_excluded as _core_is_excluded

    return _core_is_excluded(path, spec)


def filter_rows_by_attr(rows: list, attr: str, spec: Any) -> list:
    """Shape A: drop ORM rows whose ``attr`` path is excluded."""
    if not spec:
        return rows
    return [r for r in rows if not is_excluded(getattr(r, attr, None), spec)]


def filter_graph_nodes(nodes: list, spec: Any) -> list:
    """Shape B: file nodes match on ``node_id``, symbol nodes on ``file_path``."""
    if not spec:
        return nodes
    out = []
    for n in nodes:
        path = n.node_id if getattr(n, "node_type", None) == "file" else n.file_path
        if is_excluded(path, spec):
            continue
        out.append(n)
    return out


def filter_dicts_by_key(items: list, key: str, spec: Any) -> list:
    """Shape C: drop result dicts whose ``key`` path is excluded."""
    if not spec:
        return items
    return [d for d in items if not is_excluded(d.get(key), spec)]


def decision_is_excluded(decision_row: Any, spec: Any) -> bool:
    """True when a DecisionRecord is anchored entirely in excluded paths."""
    from repowise.core.exclusion import decision_is_excluded as _core_decision_is_excluded

    return _core_decision_is_excluded(decision_row, spec)


def filter_path_list(paths: list | None, spec: Any) -> list:
    """Shape D: filter a list of path strings (None -> [])."""
    if not paths:
        return []
    if not spec:
        return list(paths)
    return [p for p in paths if not is_excluded(p, spec)]


def filter_embedded_path_ids(ids: list, spec: Any) -> list:
    """Shape E: ids look like ``"path::Name"``; match on the file portion."""
    if not spec:
        return ids
    return [i for i in ids if not is_excluded(i.split("::", 1)[0], spec)]


def drop_echoed_target(targets: Any) -> None:
    """Stop paying for a map key a second time inside its own value.

    ``get_risk`` and ``get_context`` both build ``{r["target"]: r for r in
    results}``, so the inner ``target`` is the key by construction. Every
    internal consumer reads it off the card while the response is still being
    assembled; this runs last, and the equality guard means a card that somehow
    disagrees with its key keeps the field rather than losing it silently.
    """
    if not isinstance(targets, dict):
        return
    for key, card in targets.items():
        if isinstance(card, dict) and card.get("target") == key:
            card.pop("target", None)
