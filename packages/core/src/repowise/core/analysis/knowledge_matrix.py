"""Per-file knowledge rows, compact enough to ship whole to the browser.

The Contributors page simulates off-boarding client side ("if these people
left, which files lose most of the knowledge behind them"), so it needs every
file at once, not a page. Rows are positional tuples and name people and
directories by index into two dictionaries, which keeps a 6k-file repository
well under half a megabyte gzipped.

People only: automation (bots and coding agents) is left out of every share,
on both sides of the fraction, the same rule the truck factor and the
concentration figure follow. A file only automation touched has no authors.
``bus_factor`` is the indexer's own figure, as every other surface shows it.

Sync, no I/O. Rows may be dicts, dataclasses or ORM rows.
"""

from __future__ import annotations

import posixpath
from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from repowise.core.analysis.health.rows import field as row_field
from repowise.core.analysis.health.rows import json_field
from repowise.core.analysis.owners import OwnerAccumulator, is_active
from repowise.core.analysis.ownership_concentration import person_owner
from repowise.core.author_identity import IdentityResolver

#: Authors kept per file, strongest first.
TOP_AUTHORS = 5


def file_author_shares(
    row: Any, resolve: IdentityResolver, top: int = TOP_AUTHORS
) -> list[tuple[str, float]]:
    """``(identity key, share of the file's human commits)``, strongest first.

    Entries of one person under two names or emails merge before ranking.
    Shares are of every human commit the row records (its top 50 authors),
    so the kept *top* may sum to under 1.
    """
    commits: Counter[str] = Counter()
    for a in json_field(row, "top_authors_json", []):
        key = resolve(a.get("name"), a.get("email") or None)
        count = int(a.get("commit_count") or 0)
        if key and count > 0 and not resolve.is_bot(key):
            commits[key] += count
    total = commits.total()
    ranked = sorted(commits.items(), key=lambda kv: (-kv[1], kv[0]))[:top]
    return [(key, n / total) for key, n in ranked]


class _Index:
    """Values to dense positions, in first-seen order."""

    def __init__(self) -> None:
        self.items: list[str] = []
        self._pos: dict[str, int] = {}

    def __call__(self, value: str) -> int:
        pos = self._pos.get(value)
        if pos is None:
            pos = self._pos[value] = len(self.items)
            self.items.append(value)
        return pos


def _author_entry(
    key: str, accs: Mapping[str, OwnerAccumulator], resolve: IdentityResolver
) -> dict[str, Any]:
    acc = accs.get(key)
    last = acc.last_commit_at if acc else None
    return {
        "key": key,
        "name": acc.name if acc else resolve.display_name(key),
        "email": acc.email if acc else None,
        "last_commit_at": last.isoformat() if last else None,
        "active": bool(acc and is_active(acc)),
    }


def build_knowledge_matrix(
    git_rows: Iterable[Any],
    resolve: IdentityResolver,
    accs: Mapping[str, OwnerAccumulator],
    health: Mapping[str, tuple[float | None, int]],
) -> dict[str, Any]:
    """The matrix payload; see ``KnowledgeMatrixResponse`` for the fields.

    ``health`` maps a path to its ``(score, nloc)``; a file without a health
    row gets ``None`` and 0. Files come sorted by path; authors are indexed by
    how many files they appear on, most first, so the commonest indices are
    the shortest.
    """
    shaped = []
    seen: Counter[str] = Counter()
    for m in sorted(git_rows, key=lambda r: row_field(r, "file_path")):
        shares = file_author_shares(m, resolve)
        primary = person_owner(m, resolve)
        seen.update({key for key, _ in shares} | ({primary} if primary else set()))
        shaped.append((m, shares, primary))

    authors = sorted(seen, key=lambda k: (-seen[k], k))
    author_pos = {key: i for i, key in enumerate(authors)}
    dirs = _Index()
    files = []
    for m, shares, primary in shaped:
        path = row_field(m, "file_path")
        directory, name = posixpath.split(path)
        score, nloc = health.get(path, (None, 0))
        files.append(
            (
                dirs(directory),
                name,
                nloc or 0,
                round(score, 1) if score is not None else None,
                bool(row_field(m, "is_hotspot")),
                int(row_field(m, "bus_factor") or 0),
                author_pos[primary] if primary else None,
                [(author_pos[key], round(share, 3)) for key, share in shares],
                # Changed in the activity window: knowledge loss only counts code still moving.
                int(row_field(m, "commit_count_90d") or 0) > 0,
            )
        )
    return {
        "authors": [_author_entry(key, accs, resolve) for key in authors],
        "dirs": dirs.items,
        "files": files,
    }


__all__ = [
    "TOP_AUTHORS",
    "build_knowledge_matrix",
    "file_author_shares",
]
