"""Attach a dated episode that disagrees with a synthesised answer.

``get_answer`` reasons from the code as it is now, and it is confidently wrong
about what was *decided*. The reproduction this module exists for: asked
whether to run the declared formatter before committing, the tool reads a
``format`` target out of the build files and answers yes — while the checkout's
own episode store holds a ``formatter_drift`` record saying the tree is not
formatter-clean and a repo-wide run would produce a large unrelated diff.

**Add, never replace.** The synthesis stays exactly as it was; a matching
episode is appended beside it as a dated, attributed quotation. Replacing the
answer would introduce the failure this exists to prevent, because the episode
is sometimes the stale one and the synthesis the correct one. A reader who sees
"recorded at ``acd24602``" next to "run the formatter" does not run it, and
nothing is lost when the episode has expired.

Three preconditions, checked in this order:

1. the episode's scope intersects the answer's,
2. the episode is still true,
3. the synthesis is below ``confidence: high``.

Any one missing means silence, and silence is byte-identical to today's
payload. This runs at **serve time on both the fresh and the cached path**, so
the episode is read fresh on every call and a disagreement is never frozen into
a cache row — which is also why adding it needs no answer-schema bump.
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

#: How many node-scoped candidates are tested for staleness before giving up.
#: Each test may cost one git query, so this is what keeps the sanctioned
#: read-time exception bounded no matter how large the store grows. One
#: repo-wide candidate may follow them (see :func:`_candidate_window`), so the
#: ceiling on git queries per call is this plus one.
_MAX_SCOPED_CANDIDATES = 4

#: The tiers this guard is willing to put in front of a reader. Shared with
#: every other episode reader (:mod:`repowise.server.mcp_server._episodes`),
#: because "which tiers are shareable" is a property of the store rather than
#: of one tool, and the first version of this constant reached the wrong answer
#: by being local: an unnamed default let the store's second tier arrive
#: uninvited, and with 56 of this repository's 426 sessions recorded the guard
#: went **silent on its own reproduction** and served a session instead.
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

    Ranking puts path matches above subject matches, which is right. But a
    node-scoped episode is *suppressed outright* when anything has touched its
    files since, while a repo-wide one is served with its age labelled. A store
    holding hundreds of the former and a handful of the latter would therefore
    fill the whole window with candidates that can only fall through, and the
    surface would go silent on a repository that had more history rather than
    less.

    So the best repo-wide candidate is **appended** when the window would
    otherwise hold none. Appended rather than substituted, and the difference
    is the whole point: a repo-wide verdict never suppresses, so putting one
    in the last slot would not fall back to it, it would pre-empt the
    node-scoped candidate standing there — trading a git-verified "nothing in
    its scope has changed" for an unchecked claim about the whole tree, which
    is backwards on a surface whose bar is precision at the acting stage.

    It costs one more possible git query in the worst case, and that is the
    honest price of a fifth candidate rather than something to hide by
    dropping the fourth.
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

    Two rules, one per shape of episode. An episode that names files is scoped
    by those files. An episode with an **empty node set is a claim about the
    checkout as a whole** — it is not "unknown scope", and it does not
    therefore intersect everything: read that way, the repo-wide facts would
    ride along on every sub-high-confidence answer in the repository, which is
    the noise this gate exists to prevent. Repo-wide episodes are scoped by
    their subject instead, which the store already defines as the field that
    discriminates episodes within a kind.
    """
    nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]
    if nodes:
        return sorted({p for p in answer_paths if _covers(nodes, p)}) or None

    subject = (row.get("subject") or "").strip()
    # A subject with no word character (``nested_repos`` uses ``.`` for the
    # repo root) is not a topic — and ``\b`` around pure punctuation matches in
    # places no reader would call a mention.
    if not re.search(r"\w", subject):
        return None
    # A subject equal to the repository's own name has no topic scope: every
    # answer in a repo names the repo, so matching on it is matching on
    # nothing.
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
