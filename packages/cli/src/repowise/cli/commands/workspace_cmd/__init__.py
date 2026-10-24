"""``repowise workspace`` — manage multi-repo workspaces.

Modules:

* :mod:`.group`: the Click group plus list/add/remove/scan/set-default
* :mod:`._index`: indexing (and optional docs) for a freshly added repo
* :mod:`.diagnostics`, :mod:`.check`, :mod:`.metrics`, :mod:`.impacted`:
  the read-only reports over the system graph
* :mod:`.update`: ``workspace update``, the same command as ``update --workspace``
* :mod:`._shared`: workspace loading and artifact provenance

Importing the report modules registers their subcommands on the group. The
names other commands and the test-suite import from
``repowise.cli.commands.workspace_cmd`` are re-exported here.
"""

from __future__ import annotations

from . import check, diagnostics, impacted, metrics, update  # noqa: F401  (registers subcommands)
from ._index import (
    _generate_docs_for_added_repo,
    _inherit_distill_verdict,
    _resolve_docs_flag,
    _run_index_for_repo,
    inherit_workspace_distill_verdict,
)
from ._shared import _require_workspace
from .check import workspace_check
from .diagnostics import workspace_diagnostics
from .group import (
    workspace_add,
    workspace_group,
    workspace_list,
    workspace_remove,
    workspace_scan,
    workspace_set_default,
)
from .impacted import workspace_impacted_tests
from .metrics import workspace_metrics

__all__ = [
    "_generate_docs_for_added_repo",
    "_inherit_distill_verdict",
    "_require_workspace",
    "_resolve_docs_flag",
    "_run_index_for_repo",
    "inherit_workspace_distill_verdict",
    "workspace_add",
    "workspace_check",
    "workspace_diagnostics",
    "workspace_group",
    "workspace_impacted_tests",
    "workspace_list",
    "workspace_metrics",
    "workspace_remove",
    "workspace_scan",
    "workspace_set_default",
]
