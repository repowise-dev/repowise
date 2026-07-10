"""``repowise doctor`` command package.

Split out of a single ~1k-line module into cohesive parts:

* :mod:`._types` — the ``DoctorCheck`` NamedTuple + tiny formatting helpers
* :mod:`.advisories` — advisory-only signals (CLAUDE.md stamp, CLI version)
* :mod:`.repo_checks` — the per-repo check battery + ``--repair``
* :mod:`.workspace_checks` — workspace-level validation + MCP detection
* :mod:`.command` — the Click command + single/workspace orchestration

This package re-exports the public command plus the leading-underscore names
that the test-suite imports from ``repowise.cli.commands.doctor_cmd``, so the
import surface is unchanged. ``console``/``err_console`` are re-exported too
because some tests monkeypatch them at the package level.
"""

from __future__ import annotations

from repowise.cli.helpers import console, err_console

from . import advisories, command, repo_checks, workspace_checks
from ._types import DoctorCheck, _check
from .advisories import (
    _advise_claude_md_stamp,
    _claude_md_stamp_status,
    _print_cli_version_status,
)
from .command import doctor_command
from .repo_checks import _decision_vector_ids, _distill_checks
from .workspace_checks import _check_mcp_registered, _run_workspace_checks

__all__ = [
    "DoctorCheck",
    "_advise_claude_md_stamp",
    "_check",
    "_check_mcp_registered",
    "_claude_md_stamp_status",
    "_decision_vector_ids",
    "_distill_checks",
    "_print_cli_version_status",
    "_run_workspace_checks",
    "advisories",
    "command",
    "console",
    "doctor_command",
    "err_console",
    "repo_checks",
    "workspace_checks",
]
