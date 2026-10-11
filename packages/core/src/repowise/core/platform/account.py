"""Read-only facts about the local repowise.dev account and hint preference.

The CLI owns both files (``repowise login`` writes ``credentials.json``,
``repowise config hints`` writes ``platform.json``); this module only reads
them, so the server can answer "signed in?" without importing ``repowise.cli``.
No network calls: a signed-in answer means a token is on disk, not that it
still works.
"""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.platform.telemetry import _env_truthy, _load_state


def _credentials_path() -> Path:
    """The ``~/.repowise/credentials.json`` that ``repowise login`` writes."""
    return Path.home() / ".repowise" / "credentials.json"


def is_signed_in() -> bool:
    """A stored access token that a failed refresh has not marked stale."""
    try:
        creds = json.loads(_credentials_path().read_text(encoding="utf-8"))
    except Exception:
        return False
    return isinstance(creds, dict) and bool(creds.get("access_token")) and not creds.get("stale")


def hints_enabled() -> bool:
    """The CLI's switch for repowise.dev tips, shared by the local web UI."""
    if _env_truthy("REPOWISE_NO_HINTS") or _env_truthy("DO_NOT_TRACK"):
        return False
    return _load_state().get("hints_enabled") is not False
