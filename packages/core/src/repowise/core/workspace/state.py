"""Lightweight on-disk repo state helpers.

Split out of :mod:`.update` so cheap consumers (``impacted-tests``,
``doctor``) can read ``last_sync_commit`` without importing the whole
update module, which pulls the pipeline stack at module scope.
"""

from __future__ import annotations

import json as _json
from pathlib import Path
from typing import Any


def read_repo_state(repo_path: Path) -> dict[str, Any]:
    """Return the parsed ``<repo>/.repowise/state.json``, or ``{}``."""
    state_path = repo_path / ".repowise" / "state.json"
    if not state_path.is_file():
        return {}
    try:
        data = _json.loads(state_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def read_state_commit(repo_path: Path) -> str | None:
    """Return ``last_sync_commit`` from ``<repo>/.repowise/state.json`` or None."""
    sha = read_repo_state(repo_path).get("last_sync_commit")
    return str(sha) if sha else None
