"""Is this claim still true? — the one git question, and its two callers.

A claim bound to code can be checked rather than guessed: ask git whether
anything in its scope has changed since it was born. That is arithmetic, not
judgement, and it is the only read-time computation this layer sanctions.

Two shapes of claim ask it. An **episode** is born at a commit, so it asks
``<sha>..HEAD``. A **decision record** has no birth commit column — it has a
``created_at`` — so it asks ``--since=<date>``. Same call, same failure
handling, one implementation; the alternative was a second copy that would
drift from this one the first time either grew a flag.

Cost, measured on this checkout (1,011 commits): 55-66 ms cold and warm. That
is 0.5% of a ``get_answer`` synthesis and fine there. It is **not** fine on a
hook path (155 ms total budget) or on ``update`` (a 200 ms ceiling), and no
caller on either may reach this module.

Every failure mode returns ``None``. A budget that quietly produces a partial
count is worse than no answer, so the caller decides what silence means.
"""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

__all__ = ["commits_since", "describe_decision_currency"]

#: Hard ceiling on the one subprocess. Generous against the 55-66 ms measured
#: here, because a large repository is slower and a timeout costs the answer.
GIT_TIMEOUT_S = 5.0


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
