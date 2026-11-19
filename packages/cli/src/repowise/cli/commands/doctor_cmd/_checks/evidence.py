"""The evidence rows: is coverage and runtime evidence stored, and is it fresh?

One row per kind, read through the same freshness block ``status --format
json`` prints. Neither row warns: evidence goes stale with every commit, and
the Do next list asks for a refresh once it is old enough. An absent or stale
row names the setup command that collects it.
"""

from __future__ import annotations

from .._types import DoctorCheck, _check
from .context import CheckContext


def _row(block: dict, kind: str, name: str, *, check_id: str) -> DoctorCheck:
    from repowise.cli.commands._evidence_freshness import SETUP, freshness_words

    entry = block[kind]
    fix = SETUP[kind] if entry["freshness"] == "stale" or not entry["present"] else None
    words = freshness_words(entry, block["reference_commit"])
    return _check(
        name, True, f"{words}; run {fix}" if fix else words, check_id=check_id, fix=fix, data=entry
    )


def evidence_rows(ctx: CheckContext) -> list[DoctorCheck]:
    """Coverage and runtime evidence: present or absent, commit, age, stale."""
    from repowise.cli.commands._evidence_freshness import stored_freshness
    from repowise.cli.helpers import StaleIndexError
    from repowise.core.evidence.setup import UPDATE_COMMAND

    try:
        block = stored_freshness(ctx.repo_path)
    except Exception as exc:
        stale = isinstance(exc, StaleIndexError)
        return [
            _check(
                "Evidence freshness",
                True,
                f"Could not check: {exc.message if stale else f'{type(exc).__name__}: {exc}'}",
                check_id="evidence-freshness",
                warn=True,
                fix=UPDATE_COMMAND if stale else None,
            )
        ]
    if block is None:
        return []
    return [
        _row(block, "coverage", "Coverage evidence", check_id="coverage-evidence"),
        _row(block, "runtime", "Runtime evidence", check_id="runtime-evidence"),
    ]
