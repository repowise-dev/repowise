"""Per-repo doctor checks: runs the check groups in order, then render and repair.

The checks live in :mod:`._checks`, the table in :mod:`._render` and
``--repair`` in :mod:`._repair`. Names they define are re-exported here so
existing imports keep working; patch them on their defining module.
"""

from __future__ import annotations

from pathlib import Path as _DoctorPath

from repowise.cli.helpers import console, get_repowise_dir

from ._checks.context import CheckContext
from ._checks.core import (
    _attribution_notes_check,
    _autosync_hook_check,
    _events_config_check,
    _page_count,
    _repowise_dir_check,
    core_checks,
)
from ._checks.integrations import (
    _agent_target_checks,
    _claude_json_entries,
    _claude_registration_check,
    _distill_checks,
    _mcp_responds_check,
    _registered_mcp_entry,
    _registers_repo,
    _registration_target,
    _unregistered_detail,
    integration_checks,
)
from ._checks.providers import (
    _provider_checks,
    _stale_page_counts,
    _stale_page_guidance,
    provider_checks,
)
from ._checks.stores import (
    _all_pages_for_reconciliation,
    _decision_vector_ids,
    _is_stub_fallback_row,
    store_checks,
)
from ._render import render_checks
from ._repair import run_repair
from ._types import DoctorCheck

__all__ = [
    "_agent_target_checks",
    "_all_pages_for_reconciliation",
    "_attribution_notes_check",
    "_autosync_hook_check",
    "_claude_json_entries",
    "_claude_registration_check",
    "_decision_vector_ids",
    "_distill_checks",
    "_events_config_check",
    "_is_stub_fallback_row",
    "_mcp_responds_check",
    "_page_count",
    "_provider_checks",
    "_registered_mcp_entry",
    "_registers_repo",
    "_registration_target",
    "_repowise_dir_check",
    "_run_repo_checks",
    "_stale_page_counts",
    "_stale_page_guidance",
    "_unregistered_detail",
    "collect_repo_checks",
    "console",
]


def _collect(
    repo_path: _DoctorPath, *, quick: bool = False
) -> tuple[CheckContext, list[DoctorCheck]]:
    ctx = CheckContext(
        repo_path=repo_path, repowise_dir=get_repowise_dir(repo_path), quick=quick
    )
    checks: list[DoctorCheck] = []
    for group in (core_checks, provider_checks, store_checks, integration_checks):
        checks.extend(group(ctx))
    return ctx, checks


def collect_repo_checks(repo_path: _DoctorPath) -> list[DoctorCheck]:
    """Every per-repo check for *repo_path*, in table order, without printing.

    Read-only: nothing is repaired. Each row carries a stable ``id``, a
    ``level`` and, when known, the ``fix`` command; ``repowise doctor
    --format json`` serialises exactly these rows.
    """
    return _collect(repo_path)[1]


def _run_repo_checks(
    repo_path: _DoctorPath, repair: bool, *, fmt: str = "table", quick: bool = False
) -> tuple[bool, list[DoctorCheck]]:
    """Run the standard health checks against one repo.

    Returns ``(all_ok, checks)``. Extracted so workspace mode can iterate
    over every repo without duplicating the full check body. When
    ``fmt != "table"`` the table and advisory lines are not printed (repair
    is also skipped; callers must not pass ``repair=True`` with a
    non-table format). *quick* skips the slow checks (see ``CheckContext.quick``).
    """
    ctx, checks = _collect(repo_path, quick=quick)
    all_ok = all(c.ok for c in checks)

    if fmt != "table":
        return all_ok, checks

    render_checks(ctx, checks, all_ok)
    if repair:
        run_repair(ctx)
    return all_ok, checks
