"""Per-machine onboarding state for the local web UI, in ``~/.repowise/platform.json``.

Held under one ``onboarding`` key so it never collides with what the CLI keeps
in the same file (install id, telemetry consent, hint schedule). Reads are
fresh, not the telemetry module's cached copy, so a write is visible on the
next read. The write merges into the file the way the CLI's store does, keeping
every key it does not own.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import UTC, datetime
from typing import Any

from repowise.core.platform.telemetry import _platform_json_path

ONBOARDING_KEY = "onboarding"

#: Bounds the welcome map, so a long-lived install cannot grow the file forever.
_WELCOME_SEEN_LIMIT = 200


def _read_platform_json(*, strict: bool = False) -> dict[str, Any]:
    """The parsed file; ``{}`` when absent. *strict* raises ``OSError`` on a file
    that exists but does not parse, so a write never replaces keys it could not read."""
    try:
        data = json.loads(_platform_json_path().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        if strict:
            raise OSError(f"platform.json could not be read: {exc}") from exc
        return {}
    if not isinstance(data, dict):
        if strict:
            raise OSError("platform.json is not a JSON object")
        return {}
    return data


def load_onboarding() -> dict[str, Any]:
    """``welcome_seen`` (repo id -> ISO time), ``setup_pill_hidden``, ``hosted_card_hidden``."""
    raw = _read_platform_json().get(ONBOARDING_KEY)
    raw = raw if isinstance(raw, dict) else {}
    seen = raw.get("welcome_seen")
    return {
        "welcome_seen": {
            str(k): str(v) for k, v in (seen.items() if isinstance(seen, dict) else [])
        },
        "setup_pill_hidden": raw.get("setup_pill_hidden") is True,
        "hosted_card_hidden": raw.get("hosted_card_hidden") is True,
    }


def update_onboarding(
    *,
    welcome_seen_repo: str | None = None,
    setup_pill_hidden: bool | None = None,
    hosted_card_hidden: bool | None = None,
) -> dict[str, Any]:
    """Apply the given changes, persist, and return the new state.

    ``welcome_seen_repo`` stamps that repo with the current time. Raises
    ``OSError`` when the file cannot be written, so the caller can say so.
    """
    state = load_onboarding()
    if welcome_seen_repo is not None:
        seen = state["welcome_seen"]
        seen[welcome_seen_repo] = datetime.now(UTC).isoformat()
        # Oldest stamps go first once the map is full.
        newest = sorted(seen.items(), key=lambda kv: kv[1])[-_WELCOME_SEEN_LIMIT:]
        state["welcome_seen"] = dict(newest)
    if setup_pill_hidden is not None:
        state["setup_pill_hidden"] = setup_pill_hidden
    if hosted_card_hidden is not None:
        state["hosted_card_hidden"] = hosted_card_hidden
    data = _read_platform_json(strict=True)
    data[ONBOARDING_KEY] = state
    _write_atomic(data)
    return state


def _write_atomic(data: dict[str, Any]) -> None:
    """Replace platform.json in one step, so a reader never sees half a file."""
    path = _platform_json_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".platform-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
