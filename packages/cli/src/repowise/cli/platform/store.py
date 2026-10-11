"""Persistence for cross-machine platform state (``~/.repowise/platform.json``).

A single user-global JSON file holds machine-wide, repo-independent platform
state: the anonymous install id and the telemetry consent/notice flags today;
account/login tokens later. Reads tolerate a missing or corrupt file (returning
an empty dict); writes are best-effort and never raise into a CLI command.

Distinct from a repo's local ``.repowise/state.json`` — this is per-user, not
per-repo.
"""

from __future__ import annotations

import contextlib
import json
from typing import Any

PLATFORM_STATE_FILENAME = "platform.json"


def _path():
    from repowise.cli.helpers import user_global_dir

    return user_global_dir() / PLATFORM_STATE_FILENAME


def load() -> dict[str, Any]:
    """Return the parsed platform state, or an empty dict if absent/corrupt."""
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(state: dict[str, Any]) -> None:
    """Persist *state*. Best-effort: any I/O error is swallowed."""
    with contextlib.suppress(Exception):
        _path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def update(**changes: Any) -> dict[str, Any]:
    """Shallow-merge *changes* into the stored state and persist. Returns it."""
    state = load()
    state.update(changes)
    save(state)
    return state
