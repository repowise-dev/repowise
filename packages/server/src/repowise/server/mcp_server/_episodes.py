"""Reading episodes on an MCP path: currency, quoting, and the served tiers.

Two tools serve episodes and a third counts them, and the parts they share are
exactly the parts that are easy to get subtly wrong: which tiers may be shown
to somebody who did not derive them, whether a dated claim is still true, and
how a free-text body is quoted without spending the response's whole budget.

They are here rather than in :mod:`repowise.core.precedent.store` because they
are about *serving* an episode, not storing one — the store stays stdlib-only
so a hook can import it under a 155 ms budget, and this module reaches into the
MCP budget helpers.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from repowise.core.precedent.currency import (
    episode_currency as currency,
)
from repowise.core.precedent.currency import (
    episode_still_true as still_true,
)
from repowise.core.precedent.currency import (
    free_currency as _free_currency,
)
from repowise.core.precedent.store import (
    DEFAULT_MAX_ROWS,
    SHAREABLE_TIERS,
    EpisodeStore,
    default_store_path,
)
from repowise.server.mcp_server._budget import OmissionCollector

#: Declared because three of these are re-exports. ``currency`` /
#: ``still_true`` / ``_free_currency`` moved to ``core.precedent.currency``
#: when the HTTP layer became a third caller, and the names are kept here so
#: this module's own callers did not have to move with them.
__all__ = [
    "SERVED_TIERS",
    "bank_overflow",
    "currency",
    "enrich_episode_counts",
    "episode_evidence",
    "quote_body",
    "still_true",
]

_log = logging.getLogger(__name__)

#: How many episodes an evidence block carries. Small on purpose: this is the
#: showing stage, where noise is discardable but costs budget and trust, and a
#: reader who wants more has a whole tool for asking.
_MAX_EVIDENCE_EPISODES = 3

#: Ceiling on one quoted body. Git episode bodies measure ~818 chars on this
#: repository and structural ones ~300, so this cuts the tail rather than the
#: common case, and what it cuts stays recoverable through the omission store.
_MAX_EVIDENCE_BODY_CHARS = 400

#: Ceiling on the listed scope. Measured worst case on this repository's
#: shareable tiers is 23 paths / 1,522 chars, so this trims nothing in practice
#: — it is here because ``nodes`` is free text in a budgeted response and a
#: sweep commit is allowed to name a hundred files.
_MAX_SCOPE_NODES = 12

#: The tiers a reader may put in front of a user, named rather than inherited
#: from whatever the store happens to hold.
#:
#: An unnamed default is how the store's second tier reached the ``get_answer``
#: guard uninvited, and the third would have been worse: a session touches far
#: more files than a fix commit and has no birth commit, so it outranks the
#: others and can never be suppressed by the currency query. Beyond ranking,
#: a transcript episode is per-machine — two people asking one question of one
#: repository would get different answers — which is the property that decides
#: it. Structural and git episodes describe the repository and travel with it.
SERVED_TIERS = SHAREABLE_TIERS

#: ``currency`` / ``still_true`` / ``_free_currency`` are imported above rather
#: than defined here. They moved to ``core.precedent.currency`` when the HTTP
#: layer became a third caller; the names are kept so this module's callers did
#: not have to move with them.




def quote_body(
    row: dict,
    *,
    tool: str,
    repo_root: Path,
    max_chars: int,
    collector: OmissionCollector | None = None,
) -> tuple[str, OmissionCollector | None]:
    """The episode's body, capped, with whatever was cut left recoverable.

    A cap on a *count* is not a bound on a response whose fields are free text
    — the lesson ``get_why`` path mode paid 81,854 characters for — so every
    caller that quotes a body goes through here. Overflow is handed to the
    omission store so it stays reachable via ``repowise expand`` rather than
    vanishing; the returned collector is the caller's to ``attach``, because
    only the caller knows which payload the marker belongs on.

    *collector* is reused when given, and that is a correctness requirement
    rather than an optimisation: ``attach`` **overwrites** ``_meta.omitted``
    with its own refs, so two collectors finalising onto one response leave
    the first one's markers pointing at content the response no longer
    advertises. One response, one collector.
    """
    body = row.get("body") or ""
    if len(body) <= max_chars:
        return body, collector
    if collector is None:
        collector = OmissionCollector(tool, repo_root)
    marker = collector.add_inline(f"episode:{row.get('kind')}", body)
    return body[:max_chars].rstrip() + (f" {marker}" if marker else " …"), collector


def episode_evidence(
    root: Path,
    *,
    paths: Sequence[str] | None = None,
    query: str | None = None,
    limit: int = _MAX_EVIDENCE_EPISODES,
    max_body_chars: int = _MAX_EVIDENCE_BODY_CHARS,
    full_population: list[dict] | None = None,
) -> tuple[list[dict], list[tuple[dict, str, str]]]:
    """Episodes as an evidence block, bounded. Never raises.

    Two ways in, matching the two shapes of question a reader asks. *paths*
    resolves the scope through the store's node index — the reader named a
    file, and the episodes bound at, above or below it are the answer. *query*
    ranks bodies by full-text search, for a reader who asked in prose.

    **Currency is a label here, not a gate**, which is a deliberate divergence
    from the ``get_answer`` guard and worth stating because the two look alike.
    That guard appends a claim beside an answer *about the present*, so an
    episode whose scope has moved is suppressed outright. This is answering
    "what happened here", where a superseded episode is still a true statement
    about the past — a fix that landed and was later changed is exactly the
    history the question asked for. Suppressing it would answer a different
    question than the one asked.

    Only the top-ranked episode is asked about, because the git query is ~60 ms
    and the reader acts on the first one: the same bound, for the same reason,
    that this mode already applies to the record it ranks first. The rest carry
    the free re-observation signal where their tier supports it, and no
    ``still_true`` key at all where it would be a guess.

    **Returns the overflow rather than banking it**, as ``(entries, pending)``
    where each pending item is ``(entry, label, full_body)``. Callers run this
    in a worker thread — it does SQLite and a git subprocess — and the omission
    store is a ``sqlite3`` connection with ``check_same_thread`` left on, so a
    collector opened here and finalised on the event loop raises
    ``ProgrammingError`` inside ``_put``, which swallows it and silently drops
    *every* banked block. Banking belongs to whichever thread will attach.
    """
    if not paths and not query:
        return [], []
    store_path = default_store_path(root)
    # Opening the store would CREATE it. A repo that never derived episodes
    # must not grow a database because somebody asked a question.
    if not store_path.is_file():
        return [], []

    try:
        with EpisodeStore(store_path) as store:
            query_limit = DEFAULT_MAX_ROWS if full_population is not None else limit
            rows = (
                store.list_by_node(list(paths), tiers=SERVED_TIERS, limit=query_limit)
                if paths
                else store.search(query or "", tiers=SERVED_TIERS, limit=query_limit)
            )
    except Exception:
        _log.warning("episode store read failed", exc_info=True)
        return [], []
    if not rows:
        return [], []

    entries: list[dict] = []
    pending: list[tuple[dict, str, str]] = []
    for rank, row in enumerate(rows):
        body = row.get("body") or ""
        entry: dict = {
            "tier": row.get("tier"),
            "kind": row.get("kind"),
            "subject": row.get("subject"),
            "recorded": body,
            "evidence": row.get("evidence"),
            "scope": _scope_field(row),
        }
        if len(body) > max_body_chars:
            entry["recorded"] = body[:max_body_chars].rstrip()
            pending.append((entry, f"episode:{row.get('kind')}", body))
        verdict = currency(row, root=root).sentence if rank == 0 else _free_currency(row)
        if verdict:
            entry["still_true"] = verdict
        entries.append(entry)
    if full_population is not None:
        full_population.extend(entries)
    return entries[:limit], pending


def bank_overflow(
    pending: Sequence[tuple[dict, str, str]],
    *,
    tool: str,
    repo_root: Path,
    collector: OmissionCollector | None = None,
) -> OmissionCollector | None:
    """Persist capped bodies and stamp each entry with its recovery marker.

    Call on the thread that will ``attach`` the returned collector — see
    :func:`episode_evidence` for why that is not the thread that read them.

    Identical bodies are banked once. The omission store keys on a content
    hash, so a repeated body already resolved to one row, but the collector
    appends a ref per call and would have advertised the same ref three times
    and counted its tokens three times over.
    """
    if not pending:
        return collector
    if collector is None:
        collector = OmissionCollector(tool, repo_root)
    markers: dict[str, str | None] = {}
    for entry, label, body in pending:
        if body not in markers:
            markers[body] = collector.add_inline(label, body)
        marker = markers[body]
        entry["recorded"] = f"{entry['recorded']} {marker}" if marker else f"{entry['recorded']} …"
    return collector


def _scope_field(row: dict) -> list[str] | str:
    """The episode's scope, bounded.

    An empty node set is a claim about the checkout as a whole rather than an
    unknown one. A long one is truthful but is free text in a response that has
    a budget, so it is capped and says how many it stood for.
    """
    nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str)]
    if not nodes:
        return "the checkout as a whole"
    if len(nodes) <= _MAX_SCOPE_NODES:
        return nodes
    return [*nodes[:_MAX_SCOPE_NODES], f"… and {len(nodes) - _MAX_SCOPE_NODES} more"]
def episode_counts(root: Path, paths: Sequence[str]) -> dict[str, int]:
    """How many episodes each of *paths* carries. Never raises.

    A count and nothing else. A number invites a follow-up call to the tool
    that serves the bodies; a paragraph spends every caller's budget whether
    they wanted it or not, and these two tools are called on every target an
    agent triages.

    Opened once for the whole call rather than once per target, because the
    open is the expensive part: 3.0 ms against 0.2 ms for the lookup itself on
    this repository's store. Paths with no episodes are absent from the result
    so a caller can omit the field instead of serving a zero.
    """
    if not paths:
        return {}
    store_path = default_store_path(root)
    # Opening the store would CREATE it. A repo that never derived episodes
    # must not grow a database because somebody triaged a file.
    if not store_path.is_file():
        return {}
    try:
        with EpisodeStore(store_path) as store:
            return store.count_by_node(list(paths), tiers=SERVED_TIERS)
    except Exception:
        _log.warning("episode count failed", exc_info=True)
        return {}


def enrich_episode_counts(results: Sequence[dict], root: Path) -> None:
    """Stamp ``episodes: N`` onto each target card that has any. Never raises.

    Shared by ``get_risk`` and ``get_context``, which build their per-target
    dicts independently and have no common assembly point — the same shape of
    post-hoc enrichment both already use for cross-repo and health data.

    Three things decide which paths are asked about, and each was a wrong
    answer first:

    *A card carrying an ``error`` is skipped.* An unresolved target still has
    its requested string, and a directory-bound episode matches any path
    beneath it, so a typo under a governed directory came back with an
    authoritative-looking count attached to "target not found".

    *A symbol target is asked about as its file.* ``mod.py::Name`` splits on
    ``/`` to a leaf that matches no node, leaving only the ancestor
    directories to match — so a symbol card showed the count of its parent
    directory while the sibling file card showed the file's own, and the
    symbol read as the quieter of the two. Episodes bind to files; the honest
    answer for a symbol is its file's.

    *A renamed file is asked about under both names.* ``get_risk`` already
    resolves ``original_path``, and episodes are filed under the path the
    commit touched, so without it a file loses its entire history the moment
    it moves — precisely when an agent most wants it.

    Synchronous, and both callers hand it to a thread: the work is SQLite and
    belongs off the event loop.
    """
    wanted: dict[int, tuple[str, str | None]] = {}
    for i, card in enumerate(results):
        target = card.get("target")
        if not isinstance(target, str) or card.get("error"):
            continue
        current = _file_of(target)
        if not current:
            continue
        original = card.get("original_path")
        former = _file_of(original) if isinstance(original, str) and original else None
        wanted[i] = (current, former if former and former != current else None)

    asked = {name for pair in wanted.values() for name in pair if name}
    counts = episode_counts(root, sorted(asked))
    if not counts:
        return
    for i, (current, former) in wanted.items():
        # Whichever name the history is filed under, not the sum of both: one
        # commit can touch both sides of a rename, so adding them would count
        # it twice, and after a rename the episodes are under the old name.
        total = counts.get(current) or (counts.get(former, 0) if former else 0)
        if total:
            results[i]["episodes"] = total


def _file_of(target: str) -> str:
    """The file a target names, dropping any ``::symbol`` suffix."""
    return target.split("::", 1)[0].strip()
