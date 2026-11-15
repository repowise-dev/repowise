"""Whether an index still describes the checkout it was built from.

One answer for the web Setup page and ``repowise doctor``: both compare the
commit the index records with the HEAD read from the ref files, so a quick
doctor run and every Setup request stay free of git subprocesses while the two
agree. Only once they differ is git asked whether the indexed commit is still
in the history, since plain ``repowise update`` refuses one that is not.
"""

from __future__ import annotations

from pathlib import Path
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


def indexed_commit_missing(repo_path: str | Path, indexed: str | None, live: str | None) -> bool:
    """Whether HEAD moved off *indexed* and git says this history no longer has it.

    A rebase or reset leaves the index naming such a commit; ``update`` then has
    no base to diff from and needs ``--since``. ``False`` while the commits
    agree (git is not asked) and whenever git cannot answer.
    """
    from repowise.core.git_refs import commit_exists

    if not indexed or commit_state(indexed, live) == "up_to_date":
        return False
    return commit_exists(str(repo_path), indexed) is False
