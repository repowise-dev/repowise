"""Is this claim still true? — the one git question, and its two callers.

A claim bound to code can be checked rather than guessed: ask git whether
anything in its scope has changed since it was born. That is arithmetic, not
judgement, and it is the only read-time computation this layer sanctions.

Two shapes of claim ask it. An **episode** is born at a commit, so it asks
``<sha>..HEAD``. A **decision record** has no birth commit column — it has a
``created_at`` — so it asks ``--since=<date>``. Same call, same failure
handling, one implementation; the alternative was a second copy that would
drift from this one the first time either grew a flag.

The episode half lived in ``server.mcp_server._episodes`` while MCP was its
only reader. It moved here when the HTTP layer became a third caller, because
the alternative was two implementations of one verdict and this layer has
already paid for that lesson once. The reason it was over there still holds
for what stayed: ``quote_body`` and ``episode_evidence`` reach into the MCP
budget helpers, and this module must not — it imports the store and nothing
heavier, so the import-budget probe stays clean.

Cost, measured on this checkout (1,011 commits): 55-66 ms cold and warm. That
is 0.5% of a ``get_answer`` synthesis and fine there. It is **not** fine on a
hook path (155 ms total budget) or on ``update`` (a 200 ms ceiling), and no
caller on either may reach this module.

Every failure mode returns ``None``. A budget that quietly produces a partial
count is worse than no answer, so the caller decides what silence means.
"""

from __future__ import annotations

import subprocess
import time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from repowise.core.precedent.store import TIER_STRUCTURAL

__all__ = [
    "EPISODE_GIT_TIMEOUT_S",
    "GIT_TIMEOUT_S",
    "RE_DERIVED_TIERS",
    "Currency",
    "commits_since",
    "describe_decision_currency",
    "episode_currency",
    "episode_still_true",
    "free_currency",
    "recorded_on",
    "short",
]

#: Hard ceiling on the one subprocess. Generous against the 55-66 ms measured
#: here, because a large repository is slower and a timeout costs the answer.
GIT_TIMEOUT_S = 5.0

#: The episode readers' tighter ceiling, and deliberately not the one above.
#: They ask inside a response a user is waiting on, where saying nothing beats
#: waiting five seconds; the decision path answers one record on demand.
EPISODE_GIT_TIMEOUT_S = 2.0

#: Tiers whose episodes are re-derived whole on every index, and for which a
#: refreshed ``last_seen_at`` is therefore proof of currency at no cost. A tier
#: that *accumulates* members has no such proof: re-observing that a commit
#: happened says nothing about whether the files it changed have moved since,
#: so the shortcut would fire always and hide the one question worth asking.
RE_DERIVED_TIERS = frozenset({TIER_STRUCTURAL})


def commits_since(
    root: Path | str,
    *,
    since_commit: str | None = None,
    since_date: datetime | None = None,
    nodes: Sequence[str] = (),
    timeout: float = GIT_TIMEOUT_S,
) -> int | None:
    """``git rev-list --count`` over *nodes* since a commit or a date.

    Exactly one of *since_commit* / *since_date* is used, commit first when
    both are given (a sha is exact where a timestamp is a boundary). Returns
    ``None`` on every failure — bad ref, git missing, timeout, unparsable
    output — never a partial count.

    *timeout* is a parameter because the two callers bought different budgets:
    ``get_answer`` sits inside a multi-second synthesis and can wait, while a
    CLI command answering one record should give up sooner.
    """
    if since_commit:
        rev = f"{since_commit}..HEAD"
        extra: list[str] = []
    elif since_date is not None:
        rev = "HEAD"
        extra = [f"--since={since_date.isoformat()}"]
    else:
        return None

    cmd = ["git", "rev-list", "--count", *extra, rev]
    if nodes:
        cmd += ["--", *nodes]
    try:
        completed = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    if completed.returncode != 0:
        return None
    try:
        return int(completed.stdout.strip())
    except ValueError:
        return None


def describe_decision_currency(
    root: Path | str,
    *,
    created_at: datetime | None,
    nodes: Sequence[str],
) -> str | None:
    """One sentence on whether a decision's code has moved since it was made.

    ``None`` means git could not decide and the caller should say nothing —
    silence beats a number nobody can stand behind.

    The unscoped case gets a sentence rather than silence, and deliberately.
    A record naming no file scores 0.0 in the stored column because the
    question cannot be asked of it, and 0.0 renders identically to a record
    whose code genuinely has not moved. Saying so is the difference between
    "checked, and fresh" and "never checkable", which is exactly what a reader
    deciding whether to trust it needs.
    """
    scope = [n for n in nodes if n]
    if not scope:
        return "not bound to any file, so whether it still holds cannot be checked"
    if created_at is None:
        return None

    count = commits_since(root, since_date=created_at, nodes=scope)
    if count is None:
        return None

    on = created_at.strftime("%Y-%m-%d")
    files = "file" if len(scope) == 1 else f"{len(scope)} files"
    if count == 0:
        return f"nothing in the {files} it governs has changed since {on}"
    commits = "1 commit" if count == 1 else f"{count} commits"
    return f"the {files} it governs changed in {commits} since {on}"


def recorded_on(birth_at: float) -> str:
    """The birth stamp as a date, or a phrase when there is no usable one.

    ``birth_at`` is a bare ``REAL`` in an untyped SQLite column, so no writer
    can be assumed to have put a sane value there. ``time.gmtime`` raises
    ``OSError`` outside ``time_t``, ``OverflowError`` on an infinity and
    ``ValueError`` on NaN, and this runs inside a sentence a reader is being
    shown — a bad row must cost its own date, never the whole response.
    """
    if not birth_at:
        return "at an unknown date"
    try:
        return time.strftime("%Y-%m-%d", time.gmtime(birth_at))
    except (OSError, OverflowError, ValueError):
        return "at an unknown date"


def short(sha: str) -> str:
    return sha[:12]


@dataclass(frozen=True)
class Currency:
    """What git says about an episode's claim, and whether it vouches for it.

    Two fields rather than one, because three readers need different halves of
    the same answer and collapsing them is how one of them ends up wrong. A
    guard appending a claim beside an answer *about the present* must stay
    silent unless ``current``; a reader asking *what happened here* wants the
    ``sentence`` either way, since a superseded episode is still a true
    statement about the past.
    """

    sentence: str
    current: bool


def episode_currency(row: dict, *, root: Path) -> Currency:
    """What is known about whether this episode still holds.

    Measured against the checkout's live ``HEAD``, not the indexed commit: an
    episode is a claim about the tree on disk, and that is the tree the reader
    is about to act on.

    Three cases, and the third is the one worth reading.

    *Re-observed.* Every index re-derives a structural fact that still holds,
    so a stamp later than ``birth_at`` is proof of currency that costs no git
    call at all. See :data:`RE_DERIVED_TIERS` for why it is not general.

    *Node-scoped.* ``git rev-list --count <birth>..HEAD -- <nodes>`` is the
    real question, and zero is a real answer. Anything else — including a git
    failure — means we cannot vouch for it.

    *Repo-wide, derived once.* Git cannot decide this one. Its scope is the
    whole tree, so any commit at all makes the count non-zero, and the fact it
    asserts ("the tree is not formatter-clean") is not what a commit falsifies
    — running the formatter is. Calling it not-current would retire the record
    on the very next commit and leave a guard that only passes its own tests,
    so the age is reported and the claim stands.

    **Costs one subprocess in the node-scoped and repo-wide branches**, ~60 ms
    on this checkout. No caller may run it per row of a list; see
    :func:`free_currency` for the form that is free.
    """
    birth_at = row.get("birth_at") or 0.0
    last_seen = row.get("last_seen_at") or 0.0
    recorded = recorded_on(birth_at)
    if last_seen > birth_at and row.get("tier") in RE_DERIVED_TIERS:
        return Currency(f"re-observed by a later index (recorded {recorded})", True)

    birth_commit = row.get("birth_commit")
    nodes = [n for n in (row.get("nodes") or []) if isinstance(n, str) and n]

    if nodes:
        if not birth_commit:
            # No birth commit means there is nothing to ask git *from*, which
            # is not the same as git answering badly: the claim is undated
            # rather than contradicted, and the guard has always served it.
            return Currency(f"recorded {recorded}; not re-checked since", True)
        changed = commits_since(
            root, since_commit=birth_commit, nodes=nodes, timeout=EPISODE_GIT_TIMEOUT_S
        )
        if changed is None:
            return Currency(
                f"recorded {recorded} at {short(birth_commit)}; git could not be asked", False
            )
        if changed > 0:
            plural = "commit has" if changed == 1 else "commits have"
            return Currency(
                f"recorded {recorded} at {short(birth_commit)}; {changed} {plural} "
                "touched its scope since",
                False,
            )
        return Currency(
            f"nothing in its scope has changed since {short(birth_commit)} "
            f"(recorded {recorded})",
            True,
        )

    if not birth_commit:
        return Currency(f"recorded {recorded}; a standing claim about this checkout", True)
    moved = commits_since(
        root, since_commit=birth_commit, nodes=[], timeout=EPISODE_GIT_TIMEOUT_S
    )
    if moved is None:
        return Currency(
            f"recorded {recorded} at {short(birth_commit)}; not re-checked since", True
        )
    if moved == 0:
        return Currency(
            f"recorded {recorded} at {short(birth_commit)}, the current commit", True
        )
    plural = "commit" if moved == 1 else "commits"
    return Currency(
        f"recorded {recorded} at {short(birth_commit)}; the tree has moved "
        f"{moved} {plural} since and this was not re-checked",
        True,
    )


def episode_still_true(row: dict, *, root: Path) -> str | None:
    """The gate form of :func:`episode_currency`: a sentence, or None to stay silent.

    For a surface that puts an episode beside an answer about the present,
    where precision is close to absolute and anything git cannot vouch for is
    not worth the risk of contradicting a correct synthesis.
    """
    verdict = episode_currency(row, root=root)
    return verdict.sentence if verdict.current else None


def free_currency(row: dict) -> str | None:
    """The currency signal available without a git call, or None to say nothing.

    Every index re-derives a structural fact that still holds, so a stamp later
    than the birth is proof of currency that costs nothing. It proves nothing
    for a tier that accumulates members, and a guess dressed as a verdict is
    worse than an absent field.

    This is the only form a list may use. :func:`episode_currency` shells out
    to git per row, so a page of fifty would be three seconds of subprocess.
    """
    birth_at = row.get("birth_at") or 0.0
    last_seen = row.get("last_seen_at") or 0.0
    if last_seen > birth_at and row.get("tier") in RE_DERIVED_TIERS:
        return f"re-observed by a later index (recorded {recorded_on(birth_at)})"
    return None
