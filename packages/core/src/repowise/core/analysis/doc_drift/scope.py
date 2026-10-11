"""Which drift findings a change is answerable for: the predictive gate.

A repository with old drift cannot adopt a whole-tree gate without a baseline,
and a baseline hides exactly the drift a change introduces when it deletes the
file an old document names. This scopes the gate to the change instead. A
finding is in scope when the change:

* edits, adds or renames the document it sits in (``edited``);
* deletes or renames away the file or directory it names (``removed``);
* edits or removes the document its anchor points into (``anchor_host``);
* edits the kind of manifest that declares its command (``manifest``);
* edits or removes a file that defined its symbol (``definition``), read from
  the finding's ``defined_in``, which only a run that read the documents has.

The last rule is per kind of manifest, not per file: a finding names a target,
not the manifest expected to declare it. A change to any ``package.json``
therefore scopes every ``npm`` command finding.

Pure over :class:`~repowise.core.analysis.change_health.sources.FileChange`
records and finding dicts, so a PR bot or hosted check run that lists a pull
request's files through an API reuses it without a checkout.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from ..change_health.sources import FileChange, GitRevisionSource
from ..git_cli import _git, split_revspec
from .constants import MANIFEST_NAMES
from .extractor import is_checkable_document
from .resolver import join_relative

#: Command runner (the part of a command target before ``:``) to the manifest
#: basenames that declare its targets.
_RUNNER_MANIFESTS: dict[str, frozenset[str]] = {
    "make": frozenset(n for n in MANIFEST_NAMES if n != "package.json"),
    "npm": frozenset({"package.json"}),
}


@dataclass(frozen=True)
class ChangeScope:
    """The parts of a change the drift gate reads."""

    label: str
    """What was diffed, for display (``origin/main...HEAD``)."""
    documents: frozenset[str]
    """Checkable documents the change adds, edits or renames (head paths)."""
    changed: frozenset[str]
    """Every head path the change adds, edits or renames."""
    removed: frozenset[str]
    """Paths the change deletes or renames away (base paths)."""
    manifests: frozenset[str]
    """Basenames of the manifests the change touches on either side."""

    @classmethod
    def from_changes(cls, changes: Iterable[FileChange], *, label: str = "") -> ChangeScope:
        changed: set[str] = set()
        removed: set[str] = set()
        for change in changes:
            if change.head_path and not change.is_deleted:
                changed.add(change.head_path)
            if change.base_path and (change.is_deleted or change.is_rename):
                removed.add(change.base_path)
        touched = changed | removed
        return cls(
            label=label,
            documents=frozenset(p for p in changed if is_checkable_document(p)),
            changed=frozenset(changed),
            removed=frozenset(removed),
            manifests=frozenset(
                name for p in touched if (name := p.rsplit("/", 1)[-1]) in MANIFEST_NAMES
            ),
        )

    def summary(self, out_of_scope: int) -> dict[str, Any]:
        """The scope as a report states it, beside the findings it kept."""
        return {
            "revspec": self.label,
            "documents_changed": len(self.documents),
            "paths_removed": len(self.removed),
            "out_of_scope": out_of_scope,
        }

    def reason(self, finding: Mapping[str, Any]) -> str | None:
        """Why the change is answerable for *finding*, or ``None`` when it is not."""
        doc = str(finding["file_path"])
        if doc in self.documents:
            return "edited"
        kind = str(finding["kind"])
        target = str(finding["target"])
        if kind in ("path", "link"):
            if any(self._removes(p) for p in _spellings(doc, target)):
                return "removed"
        elif kind == "anchor":
            host = target.partition("#")[0]
            if host and any(
                p in self.changed or p in self.removed for p in _spellings(doc, host)
            ):
                return "anchor_host"
        elif kind == "command":
            runner = target.partition(":")[0]
            if self.manifests & _RUNNER_MANIFESTS.get(runner, frozenset()):
                return "manifest"
        elif kind == "symbol" and any(
            p in self.changed or p in self.removed for p in finding.get("defined_in", ())
        ):
            return "definition"
        return None

    def _removes(self, path: str) -> bool:
        """Whether *path*, a file or a directory, is gone because of the change."""
        path = path.rstrip("/")
        if not path:
            return False
        if path in self.removed:
            return True
        prefix = path + "/"
        return any(r.startswith(prefix) for r in self.removed)


def _spellings(doc: str, target: str) -> tuple[str, ...]:
    """*target* as written and joined relative to *doc*: the resolver's two readings."""
    relative = join_relative(doc, target)
    return (target,) if relative == target else (target, relative)


def scope_findings(
    findings: Iterable[Mapping[str, Any]], scope: ChangeScope
) -> tuple[list[dict], int]:
    """``(in-scope findings, count left out)``; each kept row gains ``scope_reason``."""
    kept: list[dict] = []
    left_out = 0
    for finding in findings:
        why = scope.reason(finding)
        if why is None:
            left_out += 1
            continue
        kept.append({**finding, "scope_reason": why})
    return kept, left_out


def scope_since(root: str, revspec: str) -> ChangeScope:
    """The scope of *revspec* in the checkout at *root*; ``ValueError`` on a bad diff.

    ``base...head`` diffs from the merge-base, as change risk and changed lines
    do, and a bare ref means ``ref...HEAD``. When the head is ``HEAD`` the
    uncommitted and untracked changes count too, because the check reads the
    working tree.
    """
    parts = split_revspec(revspec)
    if parts is None:
        revspec = f"{revspec}...HEAD"
        parts = split_revspec(revspec)
    source = GitRevisionSource(root, hunks=False)
    changes = list(source.resolve(revspec).changes)
    if parts is not None and parts[2] == "HEAD":
        changes += source.resolve(None).changes
        changes += [
            FileChange(head_path=path, base_path=None, status="added")
            for path in _git(["ls-files", "-z", "--others", "--exclude-standard"], root)
            .split("\0")
            if path
        ]
    return ChangeScope.from_changes(changes, label=revspec)
