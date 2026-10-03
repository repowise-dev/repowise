"""One answer to "has this repo's index moved", for the server's per-index caches.

The repo row is stamped when an index or update run starts, before its edges
and layers are written, so the row alone can key a cache on a half-written
index. ``state.json`` is saved when the run ends, so its modification time
closes that window; it also moves on an update over uncommitted work, which
leaves ``head_commit`` unchanged.
"""

from __future__ import annotations

import os
from typing import Any

_STATE_FILE = os.path.join(".repowise", "state.json")


def index_state_key(repository: Any) -> str:
    """Head commit, row write time and the end-of-run state file's mtime."""
    head = getattr(repository, "head_commit", None) or ""
    updated = getattr(repository, "updated_at", None) or ""
    local_path = getattr(repository, "local_path", None)
    saved = ""
    if local_path:
        try:
            saved = str(os.stat(os.path.join(local_path, _STATE_FILE)).st_mtime_ns)
        except OSError:
            saved = ""
    return f"{head}:{updated}:{saved}"
