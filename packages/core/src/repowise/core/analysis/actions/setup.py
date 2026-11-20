"""Setup steps: what this machine and checkout could add to the index, read live.

The stored rules read the index; these read the checkout and the machine (the
git hooks, report files on disk, the savings ledger, the settings), which
change without an index write. So their facts are gathered on every read
(``persistence.crud.analysis.setup_facts``) and their rules (``rules.setup``)
run after the stored snapshot, in the same ``improve_signal`` tier and the
same ranking.

The finish-setup pass is offered from the first agent call until no
``improve_signal`` action is left or the person dismisses it
(``repowise next --dismiss setup``), at most once per repository: once the
steps first reach zero it never comes back, and rows unlocked later by usage
appear quietly in the lists only. Surfaces that cannot afford the read
(the SessionStart hook, the MCP handshake) read the last answer from
``.repowise/setup.json`` (:mod:`repowise.core.setup_state`), which every
full read rewrites.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from repowise.core.setup_state import FINISH_SETUP_OFFER

#: Usage that unlocks the depth rows, never a calendar: Claude Code calls to
#: this index (always-load, distill), and commits the index synced after it
#: was first built (suggested settings).
AGENT_CALLS_UNLOCK = 20
SYNCED_COMMITS_UNLOCK = 10

#: Files a step names one by one; past this it counts them and names the first.
NAMED_FILES = 10

#: The ``action_states`` id that records the person's answer to the pass.
SETUP_STATE_ID = "setup"


@dataclass(frozen=True, slots=True)
class FoundFile:
    """A file on disk a setup step would read: repo-relative path, age, format."""

    path: str
    age_days: int
    kind: str = ""


@dataclass(frozen=True, slots=True)
class SetupFacts:
    """The live facts the setup rules read. ``None`` is "could not tell", never "no"."""

    #: The checkout these facts describe; where ``.repowise/setup.json`` lives.
    root: str = ""
    sync_hook: bool | None = None
    #: Commits between the index's commit and HEAD.
    unsynced_commits: int | None = None
    coverage_ingested: bool | None = None
    coverage_reports: tuple[FoundFile, ...] = ()
    runtime_stored: bool | None = None
    evidence_files: tuple[FoundFile, ...] = ()
    background_records: int | None = None
    #: Settings ``config list --suggested`` names, by key; read once unlocked.
    suggested_settings: tuple[str, ...] | None = None
    #: ``(provider, source line, command)`` when a key or local model could
    #: write the docs and none has.
    model_source: tuple[str, str, str] | None = None
    #: Calls to this index from every agent, and from Claude Code alone.
    agent_calls: int | None = None
    claude_code_calls: int = 0
    first_call_at: str | None = None
    #: Indexed commits made after the index was first built.
    synced_commits: int | None = None
    always_load: bool | None = None
    rewrite_hook: bool | None = None
    #: Setup reached zero steps once (``.repowise/setup.json``): the pass is
    #: never offered again, and rows unlocked later appear quietly.
    completed: bool = False
    unavailable: Mapping[str, str] = field(default_factory=dict)

    @property
    def readable(self) -> bool:
        """Every fact behind the setup steps was read on this pass.

        Only then can zero steps mean done: a step whose fact could not be
        read has no row, and must not switch the offer off for good. The
        rewrite hook is left out, since only the CLI reads it and its step
        unlocks later.
        """
        facts = (self.coverage_ingested, self.runtime_stored, self.background_records)
        return not self.unavailable and None not in facts and self.synced_commits is not None

    def unlocks(self) -> dict[str, bool]:
        """Which depth rows usage has unlocked."""
        calls = self.claude_code_calls >= AGENT_CALLS_UNLOCK
        commits = (self.synced_commits or 0) >= SYNCED_COMMITS_UNLOCK
        return {
            "always_load": calls,
            "distill": calls,
            "suggested_settings": commits,
        }


def setup_block(
    horizons: Mapping[str, Any], setup: SetupFacts, dismissed: bool, *, snoozed: int = 0
) -> dict[str, Any]:
    """The view's ``setup``: steps left, and whether the agent pass is offered.

    A step is any visible ``improve_signal`` action, stored or live: the pass
    works through exactly those. *snoozed* counts the steps hidden only by a
    snooze: they come back, so they keep setup from counting as complete.
    """
    week = horizons.get("week") or {}
    left = int((week.get("by_tier") or {}).get("improve_signal", 0))
    completed = setup.completed or (left == 0 and snoozed == 0 and setup.readable)
    offered = left > 0 and not dismissed and not completed and (setup.agent_calls or 0) > 0
    return {
        "open": left,
        "offer": FINISH_SETUP_OFFER if offered else None,
        "dismissed": dismissed,
        "completed": completed,
        "agent_calls": setup.agent_calls,
        "claude_code_calls": setup.claude_code_calls,
        "first_call_at": setup.first_call_at,
        "synced_commits": setup.synced_commits,
        "unlocks": setup.unlocks(),
    }


__all__ = [
    "AGENT_CALLS_UNLOCK",
    "NAMED_FILES",
    "SETUP_STATE_ID",
    "SYNCED_COMMITS_UNLOCK",
    "FoundFile",
    "SetupFacts",
    "setup_block",
]
