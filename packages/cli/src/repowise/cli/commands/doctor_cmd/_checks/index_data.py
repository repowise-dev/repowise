"""The index-data row: is the stored index complete and current?

The other store rows say the stores agree with each other; none says the data
in them is complete or was written by the rules this build runs. An index whose
git, graph, symbol or health tables are empty, or whose stamps predate the
current git history rules, health analyzer, security scanner or page renderer,
passed every one of them. This row reads the same stamps ``repowise update``
reads to decide what to redo, so it names exactly what the next update will
rework. Stamp reads plus one ``EXISTS`` probe per table keep it cheap enough
for ``doctor --quick``.
"""

from __future__ import annotations

from pathlib import Path as _DoctorPath
from typing import Any

from repowise.cli.helpers import run_async

from .._types import DoctorCheck, _check
from .context import CheckContext, open_store

NAME = "Index data"
CHECK_ID = "index-data"
#: No model calls: re-walks git history, re-scores health and security and
#: re-renders template pages.
FIX = "repowise update --index-only"
#: Re-renders model-written pages, which ``--index-only`` leaves alone.
FIX_MODEL_PAGES = "repowise update"
#: Rebuilds empty tables. ``update`` cannot: at an unchanged HEAD with current
#: stamps it reports "already up to date" and writes nothing.
FIX_EMPTY = "repowise init"

#: The tables behind each part of the index. A set counts as empty only when
#: every table in it is, since some (``git_function_blame`` below the full
#: tier, ``health_findings`` on a clean repo) are legitimately empty alone.
GIT_TABLES = ("git_metadata", "git_commits", "git_commit_files", "git_function_blame")
CODE_TABLE_SETS = {
    "graph": ("graph_nodes", "graph_edges"),
    "symbol": ("wiki_symbols",),
    "health": ("health_findings", "health_file_metrics"),
}


def git_indexed(state: dict[str, Any]) -> bool:
    """Whether the stored index walked git history.

    ``init`` writes ``git_tier`` in every mode, so the recorded HEAD is what
    tells a git checkout apart from a plain folder, which never stamps the
    history version and has no git rows to count.
    """
    return bool(state.get("git_tier")) and bool(state.get("last_sync_commit"))


def _stamp(state: dict[str, Any], key: str) -> str:
    stored = state.get(key)
    return "unstamped" if stored is None else f"v{stored}"


def _history_part(state: dict[str, Any]) -> str:
    from repowise.core.ingestion.git_indexer import (
        GIT_HISTORY_BACKFILL_REWALK,
        GIT_HISTORY_VERSION,
        GIT_HISTORY_VERSION_KEY,
    )

    if state.get(GIT_HISTORY_VERSION_KEY) == GIT_HISTORY_BACKFILL_REWALK:
        return "git history awaits a re-walk after attribution backfill"
    return (
        f"git history written by older rules "
        f"({_stamp(state, GIT_HISTORY_VERSION_KEY)}, current v{GIT_HISTORY_VERSION})"
    )


def _rescore_parts(state: dict[str, Any]) -> list[str]:
    from repowise.cli.commands.status_cmd import _rescore_pending
    from repowise.core.analysis.health import HEALTH_ANALYZER_VERSION
    from repowise.core.analysis.security_scan import SECURITY_SCANNER_VERSION

    labels = {
        "health": (
            f"health scored by an older analyzer "
            f"({_stamp(state, 'health_analyzer_version')}, current v{HEALTH_ANALYZER_VERSION})"
        ),
        "security": (
            f"security findings from an older scanner "
            f"({_stamp(state, 'security_scanner_version')}, current v{SECURITY_SCANNER_VERSION})"
        ),
    }
    return [labels[part] for part in _rescore_pending(state)]


def stale_parts(state: dict[str, Any]) -> list[str]:
    """What the next ``repowise update`` redoes because a stamp is out of date.

    Reuses the predicates update itself runs (``git_history_stale``,
    ``health_analyzer_changed``, ``security_scanner_changed``), so doctor and
    update cannot disagree about what is stale.
    """
    from repowise.core.ingestion.git_indexer import git_history_stale

    history = [_history_part(state)] if git_indexed(state) and git_history_stale(state) else []
    return history + _rescore_parts(state)


def renderer_part(state: dict[str, Any], current_fp: str) -> str | None:
    """The stale-renderer problem, or None while the pages match *current_fp*.

    Runs update's own ``renderer_changed`` predicate as a full update would
    (``index_only=False``), so a model wiki counts as stale too.
    """
    from repowise.cli.commands.update_cmd.persistence import renderer_changed
    from repowise.core.docs_mode import resolve_docs_mode

    stale = renderer_changed(
        state.get("renderer_fingerprint"),
        current_fp,
        resolve_docs_mode(state),
        index_only=False,
    )
    return "pages rendered by an older page renderer" if stale else None


def code_tables_expected(state: dict[str, Any], page_count: int) -> bool:
    """Whether the index covered any source files, so graph, symbol and health
    tables should hold rows. Falls back to the page count for a state.json
    written before ``index_scope`` recorded the eligible files.
    """
    scope = state.get("index_scope")
    pages = scope.get("file_pages") if isinstance(scope, dict) else None
    eligible = pages.get("eligible") if isinstance(pages, dict) else None
    return eligible > 0 if isinstance(eligible, int) else page_count > 0


def expected_table_sets(state: dict[str, Any], page_count: int) -> dict[str, tuple[str, ...]]:
    """The table sets this index should have filled, by label."""
    git = {"git": GIT_TABLES} if git_indexed(state) else {}
    code = CODE_TABLE_SETS if code_tables_expected(state, page_count) else {}
    return {**git, **code}


def empty_sets(expected: dict[str, tuple[str, ...]], empty_tables: set[str]) -> list[str]:
    """Labels of the *expected* sets whose every table is in *empty_tables*."""
    return [label for label, tables in expected.items() if set(tables) <= empty_tables]


def _empty_problem(label: str, state: dict[str, Any]) -> str:
    if label == "git":
        return f"git tables are empty though the {state.get('git_tier')} git tier was indexed"
    return f"{label} tables are empty"


def fix_for(empty: list[str], model_pages_stale: bool) -> str:
    """The one command that clears every problem found.

    An empty table set wins: only ``init`` refills it, and ``init`` also
    rewrites every stamp. Model pages need a full ``update``; everything else
    clears with ``update --index-only``.
    """
    if empty:
        return FIX_EMPTY
    return FIX_MODEL_PAGES if model_pages_stale else FIX


def index_data_row(
    parts: list[str],
    empty: list[str],
    checked: list[str],
    state: dict[str, Any],
    *,
    model_pages_stale: bool = False,
) -> DoctorCheck:
    """The row for the stale *parts* and *empty* sets among the *checked* ones.

    A WARN, never a FAIL: the index still answers, from data the next run
    replaces, so a healthy-but-stale index keeps doctor's exit code.
    """
    problems = [_empty_problem(label, state) for label in empty] + list(parts)
    if problems:
        fix = fix_for(empty, model_pages_stale)
        return _check(
            NAME,
            True,
            f"{'; '.join(problems)}. Run: {fix}",
            check_id=CHECK_ID,
            warn=True,
            fix=fix,
        )
    detail = f"current; {_joined(checked)} tables populated" if checked else "current"
    return _check(NAME, True, detail, check_id=CHECK_ID)


def _joined(labels: list[str]) -> str:
    return labels[0] if len(labels) == 1 else f"{', '.join(labels[:-1])} and {labels[-1]}"


async def _empty_tables(repo_path: _DoctorPath, tables: list[str]) -> set[str] | None:
    """Which of *tables* hold no row for this repository; None if it is not in the store.

    One ``EXISTS`` probe per table stops at the first row, so a large index
    costs no more than an empty one.
    """
    from sqlalchemy import text

    from repowise.core.persistence import get_repository_by_path, get_session

    engine, sf = await open_store(repo_path)
    try:
        async with get_session(sf) as session:
            repo = await get_repository_by_path(session, str(repo_path))
            if repo is None:
                return None
            empty = set()
            for table in tables:
                # Table names come from the constants above, never from input.
                query = text(f"SELECT EXISTS(SELECT 1 FROM {table} WHERE repository_id = :rid)")
                if not await session.scalar(query, {"rid": repo.id}):
                    empty.add(table)
            return empty
    finally:
        await engine.dispose()


def _current_renderer_fp(repo_path: _DoctorPath) -> str:
    from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint

    return _current_renderer_fingerprint(repo_path)


def index_data_check(ctx: CheckContext) -> DoctorCheck:
    """The index-data row for *ctx*, whose database and state.json both loaded."""
    from repowise.core.docs_mode import resolve_docs_mode

    try:
        state = ctx.state
        expected = expected_table_sets(state, ctx.page_count)
        tables = [table for group in expected.values() for table in group]
        empty_tables = run_async(_empty_tables(ctx.repo_path, tables)) if tables else set()
        checked = list(expected) if empty_tables is not None else []
        empty = empty_sets(expected, empty_tables or set())
        renderer = renderer_part(state, _current_renderer_fp(ctx.repo_path))
        parts = stale_parts(state) + ([renderer] if renderer else [])
        model_pages_stale = bool(renderer) and resolve_docs_mode(state) == "llm"
        return index_data_row(parts, empty, checked, state, model_pages_stale=model_pages_stale)
    except Exception as exc:
        return _check(
            NAME,
            True,
            f"Could not check: {type(exc).__name__}: {exc}",
            check_id=CHECK_ID,
            warn=True,
        )
