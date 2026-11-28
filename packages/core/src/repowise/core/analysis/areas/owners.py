"""Who owns each file, resolved to one identity per person.

Keys come from :func:`repowise.core.analysis.owners.people_resolver`, the
resolver every people-shaped figure goes through, so an area's owner key is the
key the contributors pages link to.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.rows import field
from repowise.core.analysis.owners import people_resolver

__all__ = ["FileOwner", "file_owners", "owner_breakdown", "primary_owner"]


@dataclass(frozen=True)
class FileOwner:
    key: str
    name: str
    #: ``human``, ``agent`` or ``bot``.
    kind: str


def file_owners(git_rows: Sequence[Any], commit_rows: Iterable[Any] = ()) -> dict[str, FileOwner]:
    """``{file_path: owner}`` for every git row with a resolvable primary owner."""
    resolve = people_resolver(git_rows, commit_rows)
    out: dict[str, FileOwner] = {}
    for row in git_rows:
        key = resolve(field(row, "primary_owner_name"), field(row, "primary_owner_email"))
        if key:
            owner = FileOwner(key, resolve.display_name(key), resolve.kind(key))
            out[field(row, "file_path")] = owner
    return out


def owner_breakdown(
    paths: Iterable[str], owners: Mapping[str, FileOwner], total: int
) -> list[dict[str, Any]]:
    """Owners of *paths* by files owned, most first; ``share`` is of *total* files.

    Ties break on the key so two reads agree.
    """
    counts: Counter[FileOwner] = Counter(owners[p] for p in paths if p in owners)
    ranked = sorted(counts.items(), key=lambda item: (-item[1], item[0].key))
    return [
        {
            "key": owner.key,
            "name": owner.name,
            "kind": owner.kind,
            "files": files,
            "share": round(files / total, 4) if total else 0.0,
        }
        for owner, files in ranked
    ]


def primary_owner(breakdown: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """The person owning the most files; automation never leads an area."""
    return next((row for row in breakdown if row["kind"] == "human"), None)
