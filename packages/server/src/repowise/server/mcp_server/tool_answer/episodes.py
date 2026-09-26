"""Attach a dated episode that disagrees with a synthesised answer.

``get_answer`` reasons from the code as it is now and can be confidently wrong
about what was *decided*. **Add, never replace:** a matching episode is appended
beside the synthesis as a dated quotation, because sometimes the episode is the
stale one.

Three preconditions, checked in this order:

1. the episode's scope intersects the answer's,
2. the episode is still true,
3. the synthesis is below ``confidence: high``.

Any one missing means silence. Runs at serve time on the fresh and cached paths,
so an episode is never frozen into a cache row.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from repowise.core.precedent.store import EpisodeStore, default_store_path
from repowise.server.mcp_server._budget import effective_char_budget
from repowise.server.mcp_server._episodes import (
    SERVED_TIERS,
    quote_body,
    still_true,
)

_log = logging.getLogger(__name__)

#: Only ever one episode. A cap on a *count* is not a bound on a response whose
#: fields are free text, so the body is capped separately below.
_MAX_EPISODES = 1

#: Ceiling on the quoted body. Overflow is handed to the omission store so it
#: stays recoverable via ``repowise expand`` rather than vanishing.
_MAX_BODY_CHARS = 600

#: Node-scoped candidates tested for staleness. Each may cost one git query, so
#: this bounds the read-time git cost; with one possible repo-wide candidate
#: (see :func:`_candidate_window`) the ceiling is this plus one.
_MAX_SCOPED_CANDIDATES = 4

#: Shared with every episode reader: which tiers are shareable is a property
#: of the store, not of one tool.
_SERVED_TIERS = SERVED_TIERS

#: Room the block needs before it is worth attaching at all.
_BLOCK_OVERHEAD_CHARS = 400

_LEAD_IN = (
    "A dated record from this checkout is attached in `episodes` — evidence "
    "recorded at a point in time, not a correction of the answer above."
)


async def attach_episode(
    payload: dict,
    *,
    question: str,
    repo_path: str | Path | None,
    repo_name: str | None,
) -> None:
    """Append a scoped, still-true episode to *payload*. Never raises.

    Async only to keep the SQLite read and the git query off the event loop;
    all the work happens in :func:`attach_episode_sync`.
    """
    try:
        await asyncio.to_thread(
            attach_episode_sync,
            payload,
            question=question,
            repo_path=repo_path,
            repo_name=repo_name,
        )
    except Exception:  # pragma: no cover - defensive; a disagreement is a bonus
        _log.warning("get_answer episode attach failed", exc_info=True)


def attach_episode_sync(
    payload: dict,
    *,
    question: str,
    repo_path: str | Path | None,
    repo_name: str | None,
) -> None:
    """Synchronous body of :func:`attach_episode`. Never raises."""
    try:
        _attach(
            payload,
            question=question,
            repo_path=repo_path,
            repo_name=repo_name,
        )
    except Exception:
        _log.warning("get_answer episode attach failed", exc_info=True)


def _attach(
    payload: dict,
    *,
    question: str,
    repo_path: str | Path | None,
    repo_name: str | None,
) -> None:
    # Precondition 3 first: it is free, and a high-confidence answer is the one
    # case where appending a disagreement is more likely to mislead than help.
    if payload.get("confidence") == "high":
        return
    answer_text = (payload.get("answer") or "").strip()
    if not answer_text or not repo_path:
        return

    root = Path(repo_path)
    store_path = default_store_path(root)
    # Opening the store would CREATE it. A repo that never derived episodes
    # must not grow a database because someone called get_answer.
    if not store_path.is_file():
        return

    try:
        with EpisodeStore(store_path) as store:
            rows = store.list_episodes(tiers=_SERVED_TIERS)
    except Exception:
        _log.warning("episode store read failed", exc_info=True)
        return
    if not rows:
        return

    haystack = f"{question}\n{answer_text}".casefold()
    answer_paths = _answer_paths(payload)

    scoped = []
    for row in rows:
        matched = _scope(row, haystack=haystack, answer_paths=answer_paths, repo_name=repo_name)
        if matched is not None:
            scoped.append((row, matched))
    if not scoped:
        return

    # Most specific first: a path match is stronger evidence of relevance than
    # a subject phrase, and among equals the longer subject is the rarer term.
    scoped.sort(
        key=lambda pair: (len(pair[1]), len(pair[0]["subject"]), pair[0]["id"]),
        reverse=True,
    )

    # Staleness is evaluated in rank order and only until one episode passes,
    # so the git query runs at most once on the common path. A stale top match
    # falls through to the next rather than silencing a still-true one below
    # it, but only _MAX_EPISODES are ever emitted.
    emitted = 0
    for row, matched in _candidate_window(scoped):
        if emitted >= _MAX_EPISODES:
            break
        verdict = _still_true(row, root=root)
        if verdict is None:
            continue  # precondition 2 failed outright — say nothing
        if _emit(payload, row, matched=matched, verdict=verdict, repo_root=root):
            emitted += 1


def _candidate_window(scoped: list[tuple[dict, list[str]]]) -> list[tuple[dict, list[str]]]:
    """The candidates staleness is tested on: bounded, and never one-sided.

    A stale node-scoped episode is suppressed, while a repo-wide one is served
    with its age labelled, so a window of node-scoped candidates alone could
    all fall through. The best repo-wide candidate is therefore **appended**
    when the window holds none. Not substituted: a repo-wide verdict never
    suppresses, so in the last slot it would pre-empt a git-verified candidate.
    """
    window = scoped[:_MAX_SCOPED_CANDIDATES]
    if any(not matched for _row, matched in window):
        return window
    repo_wide = next(((row, matched) for row, matched in scoped if not matched), None)
    if repo_wide is None:
        return window
    return [*window, repo_wide]


# -- precondition 1: scope ---------------------------------------------------


def _scope(
    row: dict,
    *,
    haystack: str,
    answer_paths: set[str],
    repo_name: str | None,
) -> list[str] | None:
    """The part of the answer this episode is about, or None if it is not.

    An episode that names files is scoped by them. An empty node set is a claim
    about the whole checkout, not "unknown scope" that intersects everything,
    so repo-wide episodes are scoped by their subject instead.
    """
    nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]
    if nodes:
        return sorted({p for p in answer_paths if _covers(nodes, p)}) or None

    subject = (row.get("subject") or "").strip()
    # A punctuation-only subject (``.`` for the repo root) is not a topic, and
    # ``\b`` around punctuation matches nonsense.
    if not re.search(r"\w", subject):
        return None
    # Every answer names its own repo, so that subject scopes nothing.
    if repo_name and subject.casefold() == repo_name.strip().casefold():
        return None
    if not re.search(rf"\b{re.escape(subject.casefold())}\b", haystack):
        return None
    return []


def _covers(nodes: list[str], path: str) -> bool:
    """True when *path* is one of *nodes* or sits underneath one."""
    norm = path.replace("\\", "/").strip("/")
    for node in nodes:
        n = node.replace("\\", "/").strip("/")
        if norm == n or norm.startswith(f"{n}/"):
            return True
    return False


def _answer_paths(payload: dict) -> set[str]:
    """Every repo path the answer resolved, from all four places they land."""
    paths: set[str] = set()
    for key in ("citations", "fallback_targets"):
        paths.update(p for p in (payload.get(key) or []) if isinstance(p, str))
    for hit in payload.get("retrieval") or []:
        if isinstance(hit, dict):
            paths.update(
                p for p in (hit.get("path"), hit.get("target_path")) if isinstance(p, str)
            )
    for guess in payload.get("best_guesses") or []:
        if isinstance(guess, dict) and isinstance(guess.get("file"), str):
            paths.add(guess["file"])
    return {p for p in paths if p}


# -- precondition 2: still true ----------------------------------------------

#: The currency verdict, shared with every other episode reader. The one
#: sanctioned read-time git query lives behind it, bounded by a timeout and by
#: this module's candidate window.
_still_true = still_true


# -- emission ----------------------------------------------------------------


def _emit(
    payload: dict,
    row: dict,
    *,
    matched: list[str],
    verdict: str,
    repo_root: Path,
) -> bool:
    """Append the episode block, budgeted, without touching the answer.

    False when there was no room for it, so the caller does not count a block
    it did not emit.
    """
    if len(json.dumps(payload, default=str)) + _BLOCK_OVERHEAD_CHARS > effective_char_budget():
        return False

    body, collector = quote_body(
        row, tool="get_answer", repo_root=repo_root, max_chars=_MAX_BODY_CHARS
    )

    entry = {
        "tier": row.get("tier"),
        "kind": row.get("kind"),
        "subject": row.get("subject"),
        "recorded": body,
        "evidence": row.get("evidence"),
        "scope": matched or "the checkout as a whole",
        "still_true": verdict,
    }
    payload.setdefault("episodes", []).append(entry)
    if _LEAD_IN not in (payload.get("note") or ""):
        payload["note"] = f"{payload['note']} {_LEAD_IN}" if payload.get("note") else _LEAD_IN
    if collector is not None:
        collector.attach(payload)
    return True
