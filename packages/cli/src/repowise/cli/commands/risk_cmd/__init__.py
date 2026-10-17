"""``repowise risk`` — just-in-time change risk, for a change or for paths.

Two questions, one command, because they are the same question asked of
different subjects.

A REVSPEC scores a *change* from its diff shape (size, diffusion, author
familiarity) and leads with its percentile of recent commits by size and
spread, then the bug-fix record of the files it touches, then a supporting 0-10
diff-shape score with an attributable breakdown. Runs
in-process: pure git + learned constants, no LLM, no network. A natural
pre-merge / PR gate, complementary to ``repowise health`` (which scores files).

``--target <path>`` instead asks what history says about touching those files:
bug-fix pressure, churn trend, dependents, co-change partners, ownership. That
is the ``get_risk`` MCP tool, and this command is a thin adapter over it — the
same seam ``ask`` / ``context`` / ``symbol`` / ``why`` use. It reads the index
rather than git, so the repo must be indexed.

``--fail-above-percentile P`` makes the REVSPEC path a CI size gate on the
repo-relative percentile (never the 0-10 score), with the shared CI exit codes
(:mod:`repowise.cli.ci`): 1 above the gate, 2 when the change cannot be read
or ranked.

Examples:
    repowise risk                       # score uncommitted work, else HEAD
    repowise risk HEAD                  # score the last commit
    repowise risk abc123                # score a single commit
    repowise risk main..HEAD            # score a branch / PR range as one change
    repowise risk --fail-above-percentile 95 --format github  # gate the CI change
    repowise risk --target a.py -t b.py # what history says about those files

Modules:

* :mod:`.command`: the Click command
* :mod:`.change`: resolving and scoring a REVSPEC
* :mod:`.change_render`: table, json, markdown and github output for a change
* :mod:`.target`: ``--target`` (the ``get_risk`` adapter and its projection)
* :mod:`.card`: one target's risk card
* :mod:`.records`: directive blocks whose entries are dicts

Names are re-exported here; patch a name on the submodule that defines it.
"""

from __future__ import annotations

from .card import _print_list, _render_card
from .change import (
    _git_error_detail,
    _resolve_subject,
    _score,
    _Subject,
    _verify_revision,
)
from .change_render import (
    _UNGROUPED_SHOWN,
    _emit_machine,
    _independent_changes_for,
    _print_change,
    _print_change_facts,
    _print_diff_shape,
    _print_fix_headline,
    _print_fix_history_and_shape,
    _print_hot_files,
    _print_independent_changes,
    _print_size,
)
from .command import risk_command
from .records import _DIRECTIVE_RECORD_BLOCKS, _print_records, _record_text
from .target import (
    _DROPPED_TARGET_KEYS,
    _project_target,
    _render_target_risk,
    _target_risk,
    project_risk,
)

__all__ = [
    "_DIRECTIVE_RECORD_BLOCKS",
    "_DROPPED_TARGET_KEYS",
    "_UNGROUPED_SHOWN",
    "_Subject",
    "_emit_machine",
    "_git_error_detail",
    "_independent_changes_for",
    "_print_change",
    "_print_change_facts",
    "_print_diff_shape",
    "_print_fix_headline",
    "_print_fix_history_and_shape",
    "_print_hot_files",
    "_print_independent_changes",
    "_print_list",
    "_print_records",
    "_print_size",
    "_project_target",
    "_record_text",
    "_render_card",
    "_render_target_risk",
    "_resolve_subject",
    "_score",
    "_target_risk",
    "_verify_revision",
    "project_risk",
    "risk_command",
]
