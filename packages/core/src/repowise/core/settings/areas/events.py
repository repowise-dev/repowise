"""``events:``: what changes are recorded each run and where they go.

Kind names live behind a heavy import; a parity test holds them to ``KINDS``.
"""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "events"
_s = factory(AREA)

#: ``KINDS`` in ``repowise.core.events.model``: name, on by default, plain words.
KINDS: tuple[tuple[str, bool, str], ...] = (
    ("ownership_shift", True, "a file's main owner changes"),
    ("owner_inactive", True, "a file's owner goes quiet"),
    ("bus_factor_drop", True, "fewer people know a component"),
    ("new_hotspot", False, "a file becomes a hotspot"),
    ("contract_added", True, "a new API contract appears"),
    ("contract_broken", True, "an API contract breaks"),
    ("decision_stale", True, "a decision goes stale"),
    ("dead_code_growth", True, "dead code grows"),
    ("test_reach_drop", True, "test reach drops"),
)

SETTINGS: tuple[Setting, ...] = (
    _s("events.enabled", "bool", True, "Record what changed in each run."),
    _s(
        "events.retention_days",
        "int",
        365,
        "Days recorded runs are kept.",
        minimum=0,
        exclusive_minimum=True,
    ),
    _s(
        "events.compact_after_days",
        "int",
        30,
        "Days after which only one run a week is kept.",
        minimum=0,
        exclusive_minimum=True,
    ),
    *(
        _s(f"events.kinds.{name}", "bool", on, f"Record when {words}.")
        for name, on, words in KINDS
    ),
    _s(
        "events.sinks",
        "list",
        [],
        "Where events go: jsonl, stdout or a plugin sink. Empty writes jsonl.",
    ),
)
