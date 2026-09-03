"""The get_answer answer cache: when a stored row may be served, and how one is written.

Keyed on (repo, normalized question, normalized scope). The read side is mostly a list of reasons
NOT to serve a row — a stale row is worse than a re-synthesis, because it pins a
bad answer until the TTL expires and hides every improvement made since.
"""

from __future__ import annotations

import contextlib
import json as _json
import logging
import time

from sqlalchemy import delete, select

from repowise.core.persistence.database import get_session
from repowise.core.persistence.models import AnswerCache
from repowise.server.mcp_server._helpers import is_excluded
from repowise.server.mcp_server._meta import answer_hint as _answer_hint
from repowise.server.mcp_server._meta import build_meta as _build_meta
from repowise.server.mcp_server.tool_answer.confidence import _answer_is_hedged
from repowise.server.mcp_server.tool_answer.config import (
    _ANSWER_CACHE_TTL_DAYS,
    _ANSWER_SCHEMA_VERSION,
)
from repowise.server.mcp_server.tool_answer.episodes import attach_episode as _attach_episode

_log = logging.getLogger("repowise.mcp.answer")


def _json_default(obj):
    """Serialize the non-JSON types retrieval hits carry (``_sources`` sets).

    Before this fallback existed, EVERY cache write failed on the sets the
    hybrid retriever attaches to hits — silently, under the old blanket
    suppress. The cache never stored a single post-hybrid-pipeline answer.
    """
    if isinstance(obj, (set, frozenset)):
        # str-key the sort: a serializer whose whole job is "never fail the
        # cache write" must not raise TypeError on a mixed-type set.
        return sorted(obj, key=str)
    return str(obj)


def _cache_entry_expired(created_at) -> bool:
    """True when an answer-cache row is older than the hard TTL."""
    if created_at is None:
        return False
    from datetime import UTC, datetime, timedelta

    ts = created_at if created_at.tzinfo else created_at.replace(tzinfo=UTC)
    return (datetime.now(UTC) - ts) > timedelta(days=_ANSWER_CACHE_TTL_DAYS)


def _cached_payload_paths(payload: dict) -> list:
    """Every file path a cached row mentions.

    Read twice: once to decide whether the row references a path that has since
    been excluded, and once as the ``_meta`` targets of the served reply.
    """
    return [
        *(payload.get("citations") or []),
        *(payload.get("fallback_targets") or []),
        # "path" is the serialized key; "target_path" survives in rows cached
        # before the clean retrieval view existed.
        *(h.get("path") or h.get("target_path") for h in (payload.get("retrieval") or [])),
        *(g.get("file") for g in (payload.get("best_guesses") or [])),
    ]


def _cache_bypass_reason(
    payload: dict, created_at, repository, exclude_spec, cached_paths: list
) -> str | None:
    """Why this cached row must not be served, or None when it is good to serve.

    Each reason is logged where it is decided, with its own values, so the log
    says why a caller got a fresh synthesis rather than merely that it did.

    * schema: payloads from a pre-rework code path do not carry the fields the
      current consumer expects. Serving them masks every later improvement until
      the row happens to expire, so bypass silently and let the next write
      upgrade the row.
    * hedged: the retrieval and symbol pipeline has been upgraded since, so give
      synthesis another shot with the new context rather than pinning a bad
      answer.
    * empty: older versions cached gated empty-answer payloads, which pinned a
      retrieval miss until TTL. The write side no longer does this; the check
      retires the rows that predate the fix.
    * excluded: a row cached before ``exclude_patterns`` changed may reference a
      now-excluded file in its fields or in its prose. Re-synthesize rather than
      scrub the fields and leave the prose dangling.
    * commit / TTL: a row synthesised against a previous index may cite moved
      code or stale values. The TTL covers pre-stamping rows and gitless repos,
      where there is no commit to compare.
    """
    cached_version = payload.get("_schema_version", 1)
    if cached_version < _ANSWER_SCHEMA_VERSION:
        _log.info(
            "Bypassing cache entry at schema v%s (current v%s)",
            cached_version,
            _ANSWER_SCHEMA_VERSION,
        )
        return "schema"
    if _answer_is_hedged(payload.get("answer", "")):
        _log.info("Bypassing hedged cache entry for re-synthesis")
        return "hedged"
    if not (payload.get("answer") or "").strip():
        _log.info("Bypassing cached empty-answer (gated) entry")
        return "empty"
    if any(is_excluded(p, exclude_spec) for p in cached_paths):
        _log.info("Bypassing cache entry referencing a now-excluded path")
        return "excluded"
    current_commit = getattr(repository, "head_commit", None)
    cached_commit = payload.get("_indexed_commit")
    if cached_commit and current_commit and cached_commit != current_commit:
        _log.info(
            "Bypassing cache entry from commit %s (repo now at %s)",
            cached_commit,
            current_commit,
        )
        return "stale-commit"
    if _cache_entry_expired(created_at):
        _log.info("Bypassing cache entry past the %d-day TTL", _ANSWER_CACHE_TTL_DAYS)
        return "expired"
    return None


async def _serve_cached_answer(
    *, ctx, question: str, repository, repo_id, qhash: str, exclude_spec, t0: float
) -> dict | None:
    """The cached answer for this question, or None to synthesize a fresh one.

    None covers every reason a row must not be served: no row at all, a row
    :func:`_cache_bypass_reason` rejects, and any failure reading one. A row
    that will not parse is not a reason to fail the call; the next write
    replaces it.
    """
    async with get_session(ctx.session_factory) as session:
        res = await session.execute(
            select(AnswerCache).where(
                AnswerCache.repository_id == repo_id,
                AnswerCache.question_hash == qhash,
            )
        )
        cached = res.scalar_one_or_none()
    if cached is None:
        return None
    with contextlib.suppress(Exception):
        payload = _json.loads(cached.payload_json)
        cached_paths = _cached_payload_paths(payload)
        if _cache_bypass_reason(payload, cached.created_at, repository, exclude_spec, cached_paths):
            return None
        # Cache-internal fields never reach the consumer (response keys must not
        # start with "_" except _meta).
        payload.pop("_indexed_commit", None)
        payload.pop("_schema_version", None)
        retrieval_degraded = payload.pop("_retrieval_degraded", None)
        payload["_meta"] = _build_meta(
            timing_ms=(time.perf_counter() - t0) * 1000,
            cached=True,
            hint=_answer_hint(payload.get("confidence", "low")),
            repository=repository,
            targets=[p for p in cached_paths if isinstance(p, str) and p],
            extra=({"retrieval_degraded": retrieval_degraded} if retrieval_degraded else None),
        )
        # Serve-time, on this path as well as the fresh one: the episode is read
        # on every call and never cached into an answer, so a disagreement
        # cannot be frozen into a row and served after it has been superseded.
        await _attach_episode(
            payload,
            question=question,
            repo_path=getattr(ctx, "path", None),
            repo_name=getattr(repository, "name", None),
        )
        return payload
    return None


async def _write_answer_cache(
    payload: dict,
    *,
    ctx,
    question: str,
    repository,
    repo_id,
    qhash: str,
    legacy_qhash: str,
    provider,
) -> None:
    """Persist this answer as the cache row for the question (upsert).

    Best-effort: a cache failure must never block the response, but it must be
    LOGGED rather than suppressed. A plain INSERT under a blanket suppress
    violated ``uq_answer_cache_q`` on every bypass-and-resynthesize round and
    failed silently, so hedged and stale rows were never upgraded.
    Delete-then-insert in one transaction is the dialect-agnostic upsert. It
    also removes the legacy question-only identity on the first successful
    synthesis, while the versioned lookup guarantees that row is never served.
    The stamped ``_indexed_commit`` is what the read-side freshness check reads.

    The row is a shallow copy taken here, so anything the caller attaches to the
    payload after this point reaches the caller and never the cache.
    """
    cache_payload = dict(payload)
    cache_payload["_schema_version"] = _ANSWER_SCHEMA_VERSION
    commit_now = getattr(repository, "head_commit", None)
    if commit_now:
        cache_payload["_indexed_commit"] = commit_now
    try:
        async with get_session(ctx.session_factory) as session:
            await session.execute(
                delete(AnswerCache).where(
                    AnswerCache.repository_id == repo_id,
                    AnswerCache.question_hash.in_({qhash, legacy_qhash}),
                )
            )
            row = AnswerCache(
                repository_id=repo_id,
                question_hash=qhash,
                question=question.strip(),
                payload_json=_json.dumps(cache_payload, default=_json_default),
                provider_name=getattr(provider, "provider_name", "") or "",
                model_name=getattr(provider, "model_name", "") or "",
            )
            session.add(row)
            await session.commit()
    except Exception as exc:
        _log.warning("get_answer cache write failed: %s", exc)
