"""``repowise init`` command package.

Split out of a single 2k-line module into cohesive parts:

* :mod:`.command` — the Click command + single-repo orchestration
* :mod:`.workspace` — the multi-repo workspace flow
* :mod:`.generation` — shared coverage/cost-gate/generation core
* :mod:`.persistence` — database + state.json + config.yaml writers
* :mod:`.reporting` — analysis + completion console panels
* :mod:`._interactive` — interactive prompts

This package re-exports the public command plus the leading-underscore names
that sibling commands (``search``/``reindex``) and the test-suite import from
``repowise.cli.commands.init_cmd``, so the import surface is unchanged.
"""

from __future__ import annotations

from repowise.cli.providers import build_embedder as _build_embedder
from repowise.cli.providers import resolve_embedder as _resolve_embedder

from .command import init_command
from .generation import CostGateDeclined
from .persistence import effective_run_mode_for_resume as _effective_run_mode_for_resume
from .persistence import git_tier_for_run_mode as _git_tier_for_run_mode
from .persistence import persist_result as _persist_result
from .workspace import _workspace_init

__all__ = [
    "CostGateDeclined",
    "_build_embedder",
    "_effective_run_mode_for_resume",
    "_git_tier_for_run_mode",
    "_persist_result",
    "_resolve_embedder",
    "_workspace_init",
    "init_command",
]
