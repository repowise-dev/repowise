"""``state.json`` readers and the indexed-commit resolver.

Pipeline-free by construction: this module imports nothing from
``repowise.core.pipeline`` (directly or transitively), because the cold
``impacted-tests`` path reads the indexed commit through it — importing the
``repowise.core.workspace.update`` package instead runs
``update/__init__.py``, which imports ``phases.py``, which pulls the whole
pipeline stack at module scope (~0.5s per cold call, measured with
``-X importtime``).

The update orchestrator re-exports every name defined here, so existing
``from repowise.core.workspace.update import ...`` imports keep working.
"""

from __future__ import annotations

import json as _json
import logging
from pathlib import Path
from typing import Any, NamedTuple

_log = logging.getLogger("repowise.workspace.state")


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


class IndexedCommit(NamedTuple):
    """The commit an index describes, or why it cannot be named."""

    sha: str | None
    problem: str | None = None


def resolve_indexed_commit(repo_path: Path | None, row_commit: str | None) -> IndexedCommit:
    """The commit the index describes, from ``state.json`` and the repository row.

    Every update writes both from one captured head, so they normally agree.
    When one is an ancestor of the other (an update interrupted between the
    two writes, or a row an older build left behind), the index describes the
    older: the files between them then count as changed since the index was
    built, which over-selects rather than misses. Two commits neither of which
    contains the other (a rebase, a restored backup), or a sha git cannot
    read, leave the commit unknown.
    """
    state = read_state_commit(repo_path) if repo_path is not None else None
    if not state or not row_commit or state == row_commit:
        return IndexedCommit(state or row_commit)
    older = _older_commit(str(repo_path), state, row_commit)
    if older is not None:
        return IndexedCommit(older)
    return IndexedCommit(
        None,
        f"The index records two commits ({state[:7]} in state.json, {row_commit[:7]} "
        "in its database) and neither contains the other, so what it describes is "
        "unknown; run `repowise update`.",
    )


# (repo_path, a, b) -> the older commit, or None when neither contains the
# other. Only definite answers: a timeout or an unknown sha is asked again.
_ancestry_cache: dict[tuple[str, str, str], str | None] = {}
_ANCESTRY_CACHE_MAX = 32


def _older_commit(repo_path: str, a: str, b: str) -> str | None:
    """The one of *a*, *b* that is an ancestor of the other, else ``None``.

    Cached because the freshness envelope asks on every response while the two
    stamps disagree. Ancestry between two known shas never changes, and the
    next update makes the stamps equal, which ends the asking.
    """
    # Deferred: git_refs reaches into the analysis tree; importing it here
    # keeps this module's import-time contract (no pipeline) for the common
    # case where the two stamps already agree.
    from ..git_refs import is_ancestor

    key = (repo_path, a, b)
    if key in _ancestry_cache:
        return _ancestry_cache[key]
    a_first = is_ancestor(repo_path, a, b)
    b_first = None if a_first else is_ancestor(repo_path, b, a)
    older = a if a_first else b if b_first else None
    if older is not None or (a_first is False and b_first is False):
        if len(_ancestry_cache) >= _ANCESTRY_CACHE_MAX:
            _ancestry_cache.clear()
        _ancestry_cache[key] = older
    return older
