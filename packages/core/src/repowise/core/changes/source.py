"""The seam forge metadata reaches a Change Record through.

A :class:`ChangeMetadataSource` fills what git cannot say for certain:
reviewers, approvals, labels, linked issues. The local default, ``git``,
reads only what the history already holds and never touches the network: the
``Approved-by`` trailers GitLab writes on merge commits, and the issues a
message closes (``Fixes #12``). A field it cannot read stays ``None``, which
means unknown, never none. A plugin registers another source under
``SeamRegistry("change-metadata")`` and is selected with
``REPOWISE_SEAM_CHANGE_METADATA``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from repowise.core.registry.plugins import SeamRegistry

from .reconstruct import ReconstructedChange

_APPROVED_BY = re.compile(r"^Approved-by:\s*\S", re.MULTILINE)


@dataclass(frozen=True)
class ChangeMetadata:
    """Forge facts about one change; ``None`` is unknown."""

    reviewers: tuple[str, ...] | None = None
    approvals: int | None = None
    labels: tuple[str, ...] | None = None
    linked_issues: tuple[str, ...] | None = None


class ChangeMetadataSource(Protocol):
    def metadata(
        self, repo_path: Path, changes: Sequence[ReconstructedChange]
    ) -> Mapping[str, ChangeMetadata]:
        """Metadata by ``change_id``; a change left out gets none."""
        ...


class GitChangeMetadata:
    """What the commit messages say, and nothing else."""

    def metadata(
        self, repo_path: Path, changes: Sequence[ReconstructedChange]
    ) -> Mapping[str, ChangeMetadata]:
        from repowise.core.analysis.decisions.reverts import _CLOSES_RE

        out: dict[str, ChangeMetadata] = {}
        for change in changes:
            text = f"{change.title}\n{change.body}"
            approvals = len(_APPROVED_BY.findall(change.body)) or None
            issues = tuple(dict.fromkeys(f"#{n}" for n in _CLOSES_RE.findall(text))) or None
            if approvals is not None or issues is not None:
                out[change.change_id] = ChangeMetadata(approvals=approvals, linked_issues=issues)
        return out


change_metadata_sources: SeamRegistry[ChangeMetadataSource] = SeamRegistry("change-metadata")
change_metadata_sources.register("git", GitChangeMetadata(), default=True)


def resolve_source() -> tuple[str, ChangeMetadataSource]:
    """The selected source and the name its rows are marked with."""
    source = change_metadata_sources.get()
    return change_metadata_sources.default or "git", source


__all__ = [
    "ChangeMetadata",
    "ChangeMetadataSource",
    "GitChangeMetadata",
    "change_metadata_sources",
    "resolve_source",
]
