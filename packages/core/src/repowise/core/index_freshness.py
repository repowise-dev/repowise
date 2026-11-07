"""Whether an index still describes the checkout it was built from.

One answer for the web Setup page and ``repowise doctor``: both compare the
commit the index records with the HEAD read from the ref files, so a quick
doctor run and every Setup request stay free of git subprocesses.
"""

from __future__ import annotations

from typing import Literal

IndexState = Literal["incomplete", "behind", "up_to_date"]


def commit_state(indexed: str, live: str | None) -> IndexState:
    """``behind`` when HEAD moved off the indexed commit; an unreadable HEAD is not."""
    if live and live != indexed:
        return "behind"
    return "up_to_date"


def index_state(file_count: int, indexed: str | None, live: str | None) -> IndexState:
    """``incomplete`` without files or a resolvable commit; else :func:`commit_state`."""
    if not file_count or not indexed:
        return "incomplete"
    return commit_state(indexed, live)
