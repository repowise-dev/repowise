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
import time
from pathlib import Path

from repowise.core.precedent.currency import commits_since
from repowise.core.precedent.store import (
    SHAREABLE_TIERS,
    TIER_STRUCTURAL,
    EpisodeStore,
    default_store_path,
)
from repowise.server.mcp_server._budget import OmissionCollector, effective_char_budget

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

#: Tiers whose episodes are re-derived whole on every index, and for which a
#: refreshed ``last_seen_at`` is therefore proof of currency. A tier that
#: *accumulates* members has no such proof: re-observing that a commit happened
#: says nothing about whether the files it changed have moved since.
_RE_DERIVED_TIERS = frozenset({TIER_STRUCTURAL})

#: The tiers this guard is willing to put in front of a reader, named rather
#: than inherited from whatever the store happens to hold.
#:
#: An unnamed default is how the store's second tier reached this surface last
#: time, and reproducing it with the third showed why that is not survivable
#: here: with 56 of this repository's 426 sessions recorded, the guard went
#: **silent on its own reproduction** and served a session instead. Two things
#: compound. A session touches far more files than a fix commit, so it outranks
#: one on the window's specificity sort; and it has no birth commit, so
#: :func:`_still_true` never reaches the git query and can never suppress it.
#: It wins the window and holds it.
#:
#: The harmlessness bar for a surfaced episode is absolute and is unmeasured
#: for the transcript tier, which is also per-machine — two people asking one
#: question of one repository would get different answers. Until that has been
#: measured, this stays a shareable-tiers allowlist.
_SERVED_TIERS = SHAREABLE_TIERS

#: Room the block needs before it is worth attaching at all.
_BLOCK_OVERHEAD_CHARS = 400

#: Seconds allowed for the one sanctioned read-time git query. Measured at
#: 55-66 ms on a 1,000-commit repository; the timeout is for the pathological
#: case (a cold, very large repo), where saying nothing is the right answer.
_GIT_TIMEOUT_S = 2.0

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


def _still_true(row: dict, *, root: Path) -> str | None:
    """How this episode's truth was established, or None to stay silent.

    Measured against the checkout's live ``HEAD``, not the indexed commit: an
    episode is a claim about the tree on disk, and that is the tree the reader
    is about to act on.

    Three cases, and the third is the one worth reading.

    *Re-observed.* Every index re-derives a structural fact that still holds,
    so a stamp later than ``birth_at`` is proof of currency that costs no git
    call at all. It proves nothing for a tier whose members accumulate: a git
    episode is born at its commit and re-observed by every later index, so the
    shortcut would fire always and the real question below would never be
    asked. Hence :data:`_RE_DERIVED_TIERS`.

    *Node-scoped.* ``git rev-list --count <birth>..HEAD -- <nodes>`` is the
    real question, and zero is a real answer. Anything else — including a git
    failure — means we cannot vouch for it, so it is suppressed. Precision at
    the acting stage is close to absolute.

    *Repo-wide, derived once.* Git cannot decide this one. Its scope is the
    whole tree, so any commit at all makes the count non-zero, and the fact it
    asserts ("the tree is not formatter-clean") is not what a commit falsifies
    — running the formatter is. Suppressing here would retire the record on the
    very next commit and leave a guard that only passes its own tests. So the
    age **labels** rather than suppresses: it is served with its birth commit
    and how far the tree has moved since, and the reader discounts it. Under
    add-never-replace a stale episode overrides nothing, and the asymmetry runs
    the right way — believing it costs one skipped formatter run, disbelieving
    it costs a reformatted tree in a pull request.
    """
    birth_at = row.get("birth_at") or 0.0
    last_seen = row.get("last_seen_at") or 0.0
    recorded = _recorded_on(birth_at)
    if last_seen > birth_at and row.get("tier") in _RE_DERIVED_TIERS:
        return f"re-observed by a later index (recorded {recorded})"

    birth_commit = row.get("birth_commit")
    nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]

    if nodes:
        if not birth_commit:
            return f"recorded {recorded}; not re-checked since"
        changed = _commits_since(root, birth_commit, nodes)
        if changed is None or changed > 0:
            return None
        return f"nothing in its scope has changed since {_short(birth_commit)} (recorded {recorded})"

    if not birth_commit:
        return f"recorded {recorded}; a standing claim about this checkout"
    moved = _commits_since(root, birth_commit, [])
    if moved is None:
        return f"recorded {recorded} at {_short(birth_commit)}; not re-checked since"
    if moved == 0:
        return f"recorded {recorded} at {_short(birth_commit)}, the current commit"
    plural = "commit" if moved == 1 else "commits"
    return (
        f"recorded {recorded} at {_short(birth_commit)}; the tree has moved "
        f"{moved} {plural} since and this was not re-checked"
    )


def _commits_since(root: Path, birth_commit: str, nodes: list[str]) -> int | None:
    """``git rev-list --count <birth>..HEAD [-- nodes]``. None on any failure.

    The one sanctioned read-time computation in this path, and it is bounded:
    a single plumbing call with a hard timeout, measured at 55-66 ms against
    get_answer's multi-second synthesis. The call itself lives in
    :mod:`repowise.core.precedent.currency`, shared with the decision layer,
    which asks the same question from a date instead of a birth commit.
    """
    return commits_since(root, since_commit=birth_commit, nodes=nodes, timeout=_GIT_TIMEOUT_S)


def _recorded_on(birth_at: float) -> str:
    if not birth_at:
        return "at an unknown date"
    return time.strftime("%Y-%m-%d", time.gmtime(birth_at))


def _short(sha: str) -> str:
    return sha[:12]


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

    body = row.get("body") or ""
    collector: OmissionCollector | None = None
    if len(body) > _MAX_BODY_CHARS:
        collector = OmissionCollector("get_answer", repo_root)
        marker = collector.add_inline(f"episode:{row.get('kind')}", body)
        body = body[:_MAX_BODY_CHARS].rstrip() + (f" {marker}" if marker else " …")

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
