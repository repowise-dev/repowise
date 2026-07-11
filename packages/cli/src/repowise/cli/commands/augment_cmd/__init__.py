"""``repowise augment`` command package.

Split out of a single ~1300-line module into cohesive submodules:

* :mod:`._shared` — leaf helpers (repo-root walk, output-text extraction,
  path relativisation) that every other submodule builds on.
* :mod:`.search` — PostToolUse Grep/Glob rescue / triage / flood digest.
* :mod:`.read_state` — per-session Read/Edit read-intelligence state machine.
* :mod:`.decision_inject` — relevance-ranked standing-decision injection
  (SessionStart block + edit-time governing-decision notice).
* :mod:`.bash_staleness` — post-git-commit stale-wiki detection.
* :mod:`.codex` — Codex lifecycle hooks.
* :mod:`.command` — the Click command, stdin dispatch, and emission dedup.

The public command plus the leading-underscore names that ``augment_hook``
and the test-suite import from ``repowise.cli.commands.augment_cmd`` are
re-exported here, so the import surface is unchanged. Kept deliberately
lean at module scope (no sqlalchemy/asyncio/subprocess) because
``augment_hook`` imports ``_run_augment`` on the cold hook path.
"""

from __future__ import annotations

from . import bash_staleness, codex, command, read_state, search
from ._shared import _extract_output_text
from .bash_staleness import _handle_bash_post
from .command import (
    _handle_post_tool_use,
    _run_augment,
    augment_command,
)
from .read_state import (
    _load_session_state,
    _save_session_state,
    _session_state_path,
)
from .search import (
    _TRIAGE_THRESHOLD,
    _count_search_results,
    _handle_search_post,
    _looks_like_path_lookup,
    _looks_like_regex,
    _name_variants,
    _search_enrich,
    _search_result_count,
    _targets_single_non_code_file,
)

__all__ = [
    "_TRIAGE_THRESHOLD",
    "_count_search_results",
    "_extract_output_text",
    "_handle_bash_post",
    "_handle_post_tool_use",
    "_handle_search_post",
    "_load_session_state",
    "_looks_like_path_lookup",
    "_looks_like_regex",
    "_name_variants",
    "_run_augment",
    "_save_session_state",
    "_search_enrich",
    "_search_result_count",
    "_session_state_path",
    "_targets_single_non_code_file",
    "augment_command",
    "bash_staleness",
    "codex",
    "command",
    "read_state",
    "search",
]
