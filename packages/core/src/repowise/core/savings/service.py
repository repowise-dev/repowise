"""The one savings report every first-party surface reads.

Before this, three consumers each aggregated and priced the ledger themselves:
the costs endpoint, the repository overview headline and ``repowise saved``.
They disagreed in at least four ways that mattered -- which ledger rows counted
as distillation, whether MCP tokens entered the dollar figure, whether an
output credit was added, and what time window applied -- so the same repository
reported three different savings and three different dollar values depending on
where you looked.

So this module is deliberately the only reader. It opens the sidecar read-only,
runs the bounded aggregates, and hands back a :class:`SavingsReport`. It does
no formatting and no presentation: a caller maps the report to its own output
shape, but never recomputes a number from its parts.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from repowise.core.savings.contracts import SavingsReport
from repowise.core.savings.recorder import sidecar_path
from repowise.core.savings.repository import SavingsRepository


def load_report(
    repo_root: str | Path | None,
    *,
    as_of: datetime | None = None,
    days: int | None = None,
    max_breakdowns: int = 100,
) -> SavingsReport | None:
    """The savings report for *repo_root*, or ``None`` when unavailable.

    ``None`` means "no sidecar to read", which is the ordinary state of a
    repository that has never run a distill, hook or MCP call -- not an error
    and not zero. Callers distinguish the two: an absent report is "we have not
    measured anything here", a report of zero is "we measured, and it is zero".

    Read-only and never raises. Reporting runs inside a dashboard endpoint and
    a CLI command, neither of which should fail because a sidecar is locked,
    truncated or written by a newer build.
    """
    if repo_root is None:
        return None
    database = sidecar_path(repo_root)
    if not database.is_file():
        return None
    moment = as_of or datetime.now(UTC)
    try:
        # A read-only URI handle never contends with the hook and MCP writers,
        # and never runs the schema upgrade a read path has no business doing.
        connection = sqlite3.connect(
            f"file:{database.as_posix()}?mode=ro", uri=True, timeout=1
        )
    except sqlite3.Error:
        return None
    try:
        return SavingsRepository(connection).report(
            # The recorder files events under the path it wrote to, so the
            # reader has to ask with the same string or it reads an empty
            # repository and reports a confident zero. Both sides normalize
            # through Path for exactly that reason: a caller-supplied path can
            # carry a trailing separator or forward slashes on Windows, and the
            # server stores whatever a client registered, verbatim.
            str(Path(repo_root)),
            as_of=moment,
            days=days,
            max_breakdowns=max_breakdowns,
        )
    # OverflowError is not a ValueError: a large enough ``days`` overflows the
    # timedelta rather than failing validation, and this function promises not
    # to raise into a dashboard endpoint.
    except (sqlite3.Error, ValueError, OverflowError):
        return None
    finally:
        connection.close()
