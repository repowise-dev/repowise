"""``.repowise/decisions.yaml`` — the tracked source of truth for accepted decisions.

Accepted decisions are the one part of the decision layer that belongs to the
repository rather than to a machine. A candidate is a local inference; a
decision is a constraint the team agreed to, and it should travel in the same
commit as the code it governs, be reviewable in a diff, and survive a deleted
index. The database keeps the same rows as an indexed projection.

The format is generated, not hand-authored prose: reading it back and writing it
again produces the same bytes, which is what makes it diffable. Comments are not
preserved, and the file says so in its own header.

Episodes and candidates deliberately stay out. They are evidence and inference,
they turn over constantly, and committing them would put a machine's opinions
under review as though they were the team's.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from repowise.core.fsutils import atomic_write_text

__all__ = [
    "MANIFEST_FILENAME",
    "MANIFEST_VERSION",
    "ManifestConflictError",
    "ManifestDecision",
    "load_manifest",
    "manifest_path",
    "render_manifest",
    "write_manifest",
]

MANIFEST_FILENAME = "decisions.yaml"

#: Bumped only for a change a previous reader could misread. Additive optional
#: keys do not bump it: an older repowise ignoring a key it does not know is
#: better than refusing to read the file at all.
MANIFEST_VERSION = 1

_HEADER = (
    "# Accepted architectural decisions for this repository.\n"
    "#\n"
    "# Generated and rewritten by repowise; edits survive, comments do not.\n"
    "# Commit this file: it is the source of truth, and the index is a copy.\n"
    "# Candidates and evidence stay out of it on purpose.\n"
)


@dataclass(frozen=True, slots=True)
class ManifestDecision:
    """One accepted decision, as the file represents it."""

    id: str
    title: str
    decision: str
    reason: str
    scope: list[str]
    accepted_at: str
    accepted_by: str = ""
    accepted_artifact: str = ""
    currency: str = "active"
    source: str = "cli"
    evidence: list[str] = field(default_factory=list)
    superseded_by: str = ""
    aliases: list[str] = field(default_factory=list)

    def to_mapping(self) -> dict[str, Any]:
        """The ordered projection written to disk.

        Field order is fixed here rather than left to the dataclass, and empty
        optionals are dropped, so two stores holding the same decisions render
        byte-identical files.
        """
        out: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "decision": self.decision,
            "reason": self.reason,
            "scope": sorted(self.scope),
            "currency": self.currency,
            "source": self.source,
            "accepted_at": self.accepted_at,
        }
        if self.accepted_by:
            out["accepted_by"] = self.accepted_by
        if self.accepted_artifact:
            out["accepted_artifact"] = self.accepted_artifact
        if self.evidence:
            out["evidence"] = sorted(self.evidence)
        if self.superseded_by:
            out["superseded_by"] = self.superseded_by
        if self.aliases:
            out["aliases"] = sorted(self.aliases)
        return out

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> ManifestDecision:
        def _text(key: str) -> str:
            value = data.get(key)
            return "" if value is None else str(value)

        def _list(key: str) -> list[str]:
            value = data.get(key) or []
            return [str(v) for v in value] if isinstance(value, list) else []

        return cls(
            id=_text("id"),
            title=_text("title"),
            decision=_text("decision"),
            reason=_text("reason"),
            scope=_list("scope"),
            accepted_at=_text("accepted_at"),
            accepted_by=_text("accepted_by"),
            accepted_artifact=_text("accepted_artifact"),
            currency=_text("currency") or "active",
            source=_text("source") or "cli",
            evidence=_list("evidence"),
            superseded_by=_text("superseded_by"),
            aliases=_list("aliases"),
        )


class ManifestConflictError(Exception):
    """The file on disk is not the one the caller read.

    Raised instead of overwriting: a manifest is a tracked artifact, and the
    change the caller would clobber may be a colleague's commit rather than a
    stale copy.
    """


def _check_version(path: Path, version: object) -> None:
    """Refuse a file this build would silently downgrade.

    A malformed version is refused for the same reason a newer one is: the file
    is tracked, and rewriting one this build does not understand loses whatever
    the newer writer put in it.
    """
    if version is None:
        return
    try:
        found = int(version)
    except (TypeError, ValueError) as exc:
        raise ManifestConflictError(
            f"{path} has a non-numeric format version {version!r}."
        ) from exc
    if found > MANIFEST_VERSION:
        raise ManifestConflictError(
            f"{path} was written by a newer repowise (format {found}, this build "
            f"reads {MANIFEST_VERSION}). Upgrade rather than rewriting it."
        )


def manifest_path(repo_path: Path | str) -> Path:
    return Path(repo_path) / ".repowise" / MANIFEST_FILENAME


def render_manifest(decisions: list[ManifestDecision]) -> str:
    """Serialize *decisions* deterministically.

    Ordered by id, not by acceptance time: an id is stable and a timestamp is
    not, so ordering by id keeps a re-accepted decision from moving and turning
    a one-line change into a whole-file diff.
    """
    payload = {
        "version": MANIFEST_VERSION,
        "decisions": [d.to_mapping() for d in sorted(decisions, key=lambda d: d.id)],
    }
    body = yaml.dump(
        payload,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    return _HEADER + body


def load_manifest(repo_path: Path | str) -> tuple[list[ManifestDecision], str]:
    """Read the manifest, returning its decisions and its raw bytes.

    The raw text is what :func:`write_manifest` compares against to detect a
    concurrent change; an absent file reads as no decisions and an empty string,
    which is a legitimate starting state rather than an error.
    """
    path = manifest_path(repo_path)
    if not path.exists():
        return [], ""
    raw = path.read_text(encoding="utf-8")
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise ManifestConflictError(f"{path} is not valid YAML: {exc}") from exc
    if data is None:
        return [], raw
    if not isinstance(data, dict):
        raise ManifestConflictError(f"{path} must hold a mapping, not {type(data).__name__}.")
    _check_version(path, data.get("version"))
    entries = data.get("decisions") or []
    if not isinstance(entries, list):
        raise ManifestConflictError(f"{path}: 'decisions' must be a list.")
    return [
        ManifestDecision.from_mapping(e) for e in entries if isinstance(e, dict)
    ], raw


def write_manifest(
    repo_path: Path | str,
    decisions: list[ManifestDecision],
    *,
    expected_raw: str | None = None,
    allow_empty: bool = False,
) -> bool:
    """Write the manifest atomically. Returns whether the bytes changed.

    With *expected_raw*, a file that no longer matches what the caller read
    raises :class:`ManifestConflictError` and nothing is written. An unchanged render
    is not rewritten at all, so a no-op update leaves the mtime — and any watcher
    keyed on it — alone.
    """
    path = manifest_path(repo_path)
    rendered = render_manifest(decisions)
    current = path.read_text(encoding="utf-8") if path.exists() else ""
    if expected_raw is not None and current != expected_raw:
        raise ManifestConflictError(
            f"{path} changed since it was read. Reconcile the two versions and retry."
        )
    if current:
        existing, _ = load_manifest(repo_path)
        # A store rebuilt from scratch has no acceptances yet, and writing its
        # empty projection over the committed file would delete the team's
        # decisions with no way back. The file is the source of truth; an empty
        # store is a store that has not imported it.
        if existing and not decisions and not allow_empty:
            raise ManifestConflictError(
                f"{path} holds {len(existing)} decision(s) and the store holds none. "
                "Import it first, or pass allow_empty to overwrite it deliberately."
            )
    if current == rendered:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    # Newline-normalized so the file does not churn between contributors on
    # different platforms, and fsynced because losing it loses the authority
    # itself rather than a cache.
    atomic_write_text(path, rendered, newline="\n", fsync=True)
    return True
