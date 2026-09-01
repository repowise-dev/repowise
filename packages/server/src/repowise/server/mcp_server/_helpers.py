"""Shared helpers used by multiple MCP tool modules."""

from __future__ import annotations

import json
import logging
import os
import os.path
from collections.abc import Collection
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.decisions.lifecycle import is_governing
from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.persistence.models import (
    Repository,
)

# Re-exported: MCP tools import their helpers from here, but the definition
# lives in core because the CRUD layer needs the same escaping.
from repowise.core.persistence.sql import LIKE_ESCAPE, escape_like  # noqa: F401
from repowise.server.mcp_server import _state

_log = logging.getLogger("repowise.mcp")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CODE_EXTS = _LANG_REGISTRY.all_code_extensions()

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


def vector_search_timeout_s() -> float:
    """Seconds one vector query may take, from env or the cold-start default.

    An unparseable or non-positive value warns and keeps the default instead of
    silently disabling the leg, matching how REPOWISE_EMBEDDING_TIMEOUT is
    resolved.
    """
    raw = (os.environ.get(_VECTOR_TIMEOUT_ENV) or "").strip()
    if not raw:
        return _VECTOR_TIMEOUT_DEFAULT_S
    try:
        seconds = float(raw)
    except ValueError:
        seconds = float("nan")
    # NaN fails this too, so it lands on the default rather than on a budget
    # asyncio.wait_for would treat as already expired.
    if not seconds > 0:
        _log.warning("Ignoring unusable %s=%r", _VECTOR_TIMEOUT_ENV, raw)
        return _VECTOR_TIMEOUT_DEFAULT_S
    return min(seconds, _VECTOR_TIMEOUT_MAX_S)

# Words that mark a string as a natural-language question rather than a path.
# Keep this small — false positives here send genuine paths to the NL branch,
# which is harmless (path lookup also runs as a fallback) but slower.
_NL_QUESTION_TOKENS = frozenset(
    {
        "why",
        "how",
        "what",
        "when",
        "where",
        "who",
        "which",
        "should",
        "can",
        "does",
        "do",
        "is",
        "are",
        "was",
        "were",
    }
)


# ---------------------------------------------------------------------------
# Repository resolution
# ---------------------------------------------------------------------------


async def _get_repo(session: AsyncSession, repo: str | None = None) -> Repository:
    """Resolve a repository — by path, by ID, or return the first one."""
    if repo:
        # Try by path
        result = await session.execute(select(Repository).where(Repository.local_path == repo))
        obj = result.scalar_one_or_none()
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

    # Default: return the first (and often only) repository
    result = await session.execute(select(Repository).limit(1))
    obj = result.scalar_one_or_none()
    if obj is None:
        raise LookupError("No repositories found. Run 'repowise init' first.")
    return obj


# ---------------------------------------------------------------------------
# Path detection
# ---------------------------------------------------------------------------


def _is_path(query: str) -> bool:
    """Heuristic: does this string look like a file or module path?

    Natural-language questions take precedence over the slash heuristic
    because phrases like "two-phase plan/apply flow" or "client/server
    boundary" contain a slash without being paths. We treat anything with
    a question mark, that starts with a question word, or that has 4+
    whitespace-separated tokens including a question word, as NL.
    """
    stripped = query.strip()
    if not stripped:
        return False

    # Trailing "?" is an unambiguous NL signal.
    if stripped.endswith("?"):
        return False

    tokens = stripped.split()

    # First token is a question word → NL.
    if tokens and tokens[0].lower().rstrip(",.;:") in _NL_QUESTION_TOKENS:
        return False

    # Sentence-shaped input (multiple words including a question word) → NL.
    if len(tokens) >= 4 and any(t.lower().rstrip(",.;:") in _NL_QUESTION_TOKENS for t in tokens):
        return False

    # A path can't contain whitespace.
    if any(ch.isspace() for ch in stripped):
        return False

    if "/" in stripped or "\\" in stripped:
        return True
    _, ext = os.path.splitext(stripped)
    return ext in _CODE_EXTS


# ---------------------------------------------------------------------------
# Workspace-aware repo context resolution
# ---------------------------------------------------------------------------


def _is_workspace_mode() -> bool:
    """Return True if the MCP server is running in workspace mode."""
    return _state._registry is not None


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
            from repowise.core.persistence.database import get_session as _get_session

            async with _get_session(_state._session_factory) as session:
                await _get_repo(session, repo)  # raises LookupError if invalid

        return RepoContext(
            alias="default",
            path=Path(_state._repo_path) if _state._repo_path else Path.cwd(),
            session_factory=_state._session_factory,
            fts=_state._fts,
            vector_store=_state._vector_store,
            decision_store=_state._decision_store,
            vector_store_ready=_state._vector_store_ready or __import__("asyncio").Event(),
            _engine=None,
        )

    # Workspace mode — resolve via registry
    resolved = registry.resolve_repo_param(repo)
    if isinstance(resolved, list):
        raise ValueError(
            "repo='all' must be handled explicitly by each tool. "
            "Use _resolve_all_contexts() instead."
        )
    return await registry.get(resolved)


async def _resolve_all_contexts() -> list[Any]:
    """Return ``RepoContext`` objects for all repos in the workspace.

    In single-repo mode, returns a single-element list wrapping ``_state``.
    """
    registry = _state._registry
    if registry is None:
        ctx = await _resolve_repo_context(None)
        return [ctx]
    contexts = []
    for alias in registry.get_all_aliases():
        contexts.append(await registry.get(alias))
    return contexts


def _unsupported_repo_all(tool_name: str) -> dict:
    """Return an error dict for tools that don't support ``repo='all'``."""
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

# Common stop-words stripped before commit/decision keyword overlap.
_ORIGIN_STOP_WORDS = frozenset(
    {"the", "a", "an", "is", "for", "to", "of", "in", "and", "or", "with"}
)

#: Decisions the origin summary names, and the ceiling on its evidence clauses.
_ORIGIN_NARRATED_DECISIONS = 3


def _meaningful_words(text: str) -> set[str]:
    """Lowercase keyword set with common stop-words removed."""
    return set(text.lower().split()) - _ORIGIN_STOP_WORDS


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
    """Return commits whose messages share at least one keyword with *decision*."""
    decision_text = (
        f"{decision.get('title', '')} {decision.get('decision', '')} "
        f"{decision.get('rationale', '')}"
    )
    decision_words = _meaningful_words(decision_text)

    related_commits = []
    for c in commits:
        overlap = decision_words & _meaningful_words(c.get("message", ""))
        if not overlap:
            continue
        related_commits.append(
            {
                "sha": c.get("sha", ""),
                "message": c.get("message", ""),
                "author": c.get("author", ""),
                "date": c.get("date", ""),
                "matching_keywords": sorted(overlap)[:5],
            }
        )
    return related_commits


def _link_decisions_to_commits(governing_decisions: list[dict], commits: list[dict]) -> list[dict]:
    """Attach commit evidence to each governing decision via keyword overlap."""
    linked_decisions = []
    for d in governing_decisions:
        linked_decisions.append(
            {
                "title": d.get("title", ""),
                "status": d.get("status", ""),
                "source": d.get("source", ""),
                "rationale": d.get("rationale", ""),
                "evidence_commits": _commits_matching_decision(d, commits),
            }
        )
    return linked_decisions


def _origin_summary_parts(
    authors: list,
    earliest_commit: dict | None,
    linked_decisions: list[dict],
    primary: str,
    total: int,
    first_date: str,
    last_date: str,
    age: int,
) -> list[str]:
    """Assemble the narrative sentences for an origin story."""
    parts = [
        f"Created ~{first_date}, last modified {last_date} ({age} days old).",
        f"Primary author: {primary} ({total} total commits).",
    ]

    if earliest_commit:
        parts.append(
            f'Earliest key commit: "{earliest_commit.get("message", "")}" '
            f"by {earliest_commit.get('author', 'unknown')} on {earliest_commit.get('date', 'unknown')}."
        )

    if linked_decisions:
        named = linked_decisions[:_ORIGIN_NARRATED_DECISIONS]
        parts.append(f"Governed by: {', '.join(d['title'] for d in named)}.")
        # Unbounded, a file with 40 linked decisions got 16 evidence clauses,
        # restating titles the caller's own cap had already removed.
        for ld in named:
            if ld["evidence_commits"]:
                ec = ld["evidence_commits"][0]
                parts.append(
                    f'Commit "{ec["message"]}" by {ec["author"]} is evidence for "{ld["title"]}".'
                )

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
            "summary": f"No git history available for {file_path}.",
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
        linked_decisions,
        primary,
        total,
        first_date,
        last_date,
        age,
    )

    return {
        "available": True,
        "primary_author": primary,
        "author_commit_pct": git_meta.primary_owner_commit_pct,
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
            "Every accepted decision here is deprecated/superseded. "
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
        return {
            "score": "none",
            "explanation": (
                f"No architectural decisions govern {file_path}. "
                "This file is ungoverned — it may be an outlier or simply undocumented."
            ),
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
        if currencies.get(d["id"]) in ("superseded", "dismissed")
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
    # up to ``governing_count``; without it a reader could subtract them and
    # find records unaccounted for with nothing saying where they went.
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
        # Unchanged meaning, and deliberately not renamed: how many records
        # name this file at all, which is what every existing consumer reads
        # it as. The authority split is carried by the four counts below, and
        # they sum to it: accepted + withdrawn + uncheckable + candidate.
        "governing_count": len(governing),
        "active_count": len(accepted),
        "candidate_count": len(candidates),
        # Accepted once and withdrawn since, whether superseded or dismissed.
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


def read_repo_file_text(repo_root: Path | str | None, file_path: str) -> str | None:
    """Read a repo-relative file's live text, or None when it cannot be served.

    Refuses any path that resolves outside *repo_root*: several tools serve
    live bytes for a path that came out of the index, and an index row is not
    a trust boundary. Decoding is lossy on purpose (``errors="replace"``): a
    card describing a latin-1 file should degrade to mojibake in one field,
    rather than failing the whole call.

    Several tool modules still carry their own near-identical copy of this,
    each with one extra behaviour bolted on (a size ceiling, lines instead of
    text). Those are left alone here rather than churned, but new callers
    belong on this one, and a copy that needs a variation should wrap it.
    """
    if repo_root is None:
        return None
    try:
        root = Path(str(repo_root))
        abs_path = (root / file_path).resolve()
        abs_path.relative_to(root.resolve())
        return abs_path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
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
