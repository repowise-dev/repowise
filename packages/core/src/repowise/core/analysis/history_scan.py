"""Full git-history secret/risk scanning.

Complements :mod:`~repowise.core.analysis.security_scan` (which only looks at
the working tree during indexing). ``HistorySecurityScanner.scan_history`` walks
the full git history of a repo and reuses the exact same pattern registry, so a
leaked credential that was "deleted" in a later commit still surfaces — tagged
with the commit that introduced it.

Everything lands in the shared ``security_findings`` table. The
``(repository_id, file_path, kind, line_number, commit_sha)`` unique constraint
(migration 0037) makes re-runs idempotent.

Design notes (in response to review)
------------------------------------
* **Scan unique blobs, not commits x files.** ``git rev-list --objects --all``
  enumerates every object once, deduped by blob SHA, so each distinct blob's
  content is scanned a single time. Hits are then attributed to the
  first-introducing commit for provenance. Git already dedups content by blob,
  so we get natural dedup + a big speedup for free (vs. ``git ls-tree`` per
  commit, which re-reads identical content thousands of times).

* **History mode defaults to the secret-oriented subset.** Most of the 11
  patterns are code smells (``eval``/``os.system``/``weak_hash``) rather than
  leaked credentials; running those across all of history produces mostly noise
  ("os.system in a two-year-old commit") with little to act on. The
  history-relevant subset is ``hardcoded_password`` / ``hardcoded_secret``. This
  positions history scanning as complementary to a real secret scanner
  (gitleaks / trufflehog) rather than a noisy replacement. ``--all-patterns``
  opts back into the full registry when desired.

The git layer is isolated so the iteration logic can be exercised in unit tests
without a real repository.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.analysis.security_scan import (
    SECRET_KINDS,
    SecurityScanner,
)
from repowise.core.ingestion.models import EXTENSION_TO_LANGUAGE


def _run_git(repo_path: Path, args: list[str], *, timeout: float = 30.0) -> str:
    """Run a ``git`` command in *repo_path* and return stdout (best-effort).

    Returns ``""`` on any failure so callers degrade gracefully (a repo with no
    git history, a missing binary, or an unexpected ref all yield an empty
    scan rather than a crash).
    """
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout


def _parse_author_date(iso: str) -> datetime | None:
    """Parse a git ``%aI`` timestamp into a timezone-aware datetime (or None)."""
    iso = iso.strip()
    if not iso:
        return None
    try:
        # git's %aI is strict ISO-8601; normalise a trailing Z for older parsers.
        return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


@dataclass
class HistoryScanSummary:
    """Aggregate result of a full-history scan, for CLI/JSON output."""

    commits_scanned: int = 0
    blobs_scanned: int = 0
    files_scanned: int = 0
    findings_inserted: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    by_kind: dict[str, int] = field(default_factory=dict)


class HistorySecurityScanner:
    """Scan the full git history of a repository for security signals."""

    def __init__(self, session: Any, repo_id: str) -> None:
        self._session = session
        self._repo_id = repo_id
        self._scanner = SecurityScanner(session, repo_id)

    # ------------------------------------------------------------------
    # Git layer (thin wrappers around _run_git; overridable for tests)
    # ------------------------------------------------------------------

    def _list_commits(self, repo_path: Path, since: str | None, to: str | None) -> list[tuple[str, str]]:
        """Return ``[(sha, author_iso), ...]`` oldest→newest for the range.

        *since* / *to* mirror ``git rev-list`` range syntax: ``since..to``.
        When both are None, the whole reachable history is scanned (``--all``).
        """
        if since and to:
            rev_range = f"{since}..{to}"
        elif to:
            rev_range = to
        elif since:
            rev_range = f"{since}..HEAD"
        else:
            rev_range = "--all"

        # %x1f is a unit separator; %H is the full SHA, %aI the author date.
        raw = _run_git(repo_path, ["log", "--reverse", "--format=%H%x1f%aI", rev_range])
        commits: list[tuple[str, str]] = []
        for line in raw.splitlines():
            line = line.strip()
            if not line or "\x1f" not in line:
                continue
            sha, _, iso = line.partition("\x1f")
            if sha:
                commits.append((sha.strip(), iso))
        return commits

    def _unique_blobs(self, repo_path: Path, since: str | None, to: str | None) -> dict[str, str]:
        """Return ``{blob_sha: first_seen_path}`` across the requested range.

        Uses ``git rev-list --objects`` over the range so each distinct blob is
        enumerated once and deduped by content hash. The first path a blob is
        seen at is kept for attribution/provenance; the scan only runs once per
        blob regardless of how many commits reference it.
        """
        if since and to:
            rev_range = f"{since}..{to}"
        elif to:
            rev_range = to
        elif since:
            rev_range = f"{since}..HEAD"
        else:
            rev_range = "--all"

        raw = _run_git(
            repo_path,
            ["rev-list", "--objects", rev_range],
            timeout=60.0,
        )
        blobs: dict[str, str] = {}
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            obj_sha, obj_type = parts[0], parts[1]
            if obj_type != "blob":
                continue
            path = parts[2] if len(parts) > 2 else ""
            # Only the first reference to a blob is kept; later duplicates are
            # ignored — that is the dedup the maintainer asked for.
            blobs.setdefault(obj_sha, path)
        return blobs

    def _read_blob(self, repo_path: Path, blob_sha: str) -> str:
        """Return the textual content of *blob_sha* (empty on failure)."""
        return _run_git(repo_path, ["cat-file", "-p", blob_sha], timeout=60.0)

    @staticmethod
    def _is_source(path: str) -> bool:
        """True when *path* has a language we scan (mirrors the indexer)."""
        suffix = Path(path).suffix.lower().lstrip(".")
        return suffix in EXTENSION_TO_LANGUAGE

    @staticmethod
    def _passes_gate(kind: str, *, secrets_only: bool) -> bool:
        """Filter a finding kind against the history scan gate.

        When *secrets_only* is True (the default for history mode), only the
        secret-oriented patterns survive — the rest are code smells that are
        mostly noise across history.
        """
        if secrets_only:
            return kind in SECRET_KINDS
        return True

    def _blobs_in_commit(self, repo_path: Path, commit: str) -> list[str]:
        """Return the blob SHAs tracked by *commit* (git ls-tree -r)."""
        raw = _run_git(repo_path, ["ls-tree", "-r", commit])
        out: list[str] = []
        for line in raw.splitlines():
            parts = line.split()
            if len(parts) < 4 or parts[1] != "blob":
                continue
            out.append(parts[2])
        return out

    # ------------------------------------------------------------------
    # Scan driver
    # ------------------------------------------------------------------

    async def scan_history(
        self,
        repo_path: Path,
        *,
        since: str | None = None,
        to: str | None = None,
        secrets_only: bool = True,
        progress: Any = None,
    ) -> HistoryScanSummary:
        """Scan the full git history and persist findings with commit provenance.

        Parameters
        ----------
        repo_path:
            Repository root.
        since / to:
            Optional git rev-range bounds (``since..to``). ``None`` scans all
            reachable history.
        secrets_only:
            When True (default), only the secret-oriented patterns
            (hardcoded_password / hardcoded_secret) are reported, to avoid the
            code-smell noise of scanning all of history. Pass False to scan the
            full pattern registry.
        progress:
            Optional callable ``progress(message)`` for CLI feedback.
        """
        summary = HistoryScanSummary()

        commits = self._list_commits(repo_path, since, to)
        summary.commits_scanned = len(commits)
        if not commits:
            return summary

        # Distinct blobs, deduped by content hash. Each blob is scanned once.
        blobs = self._unique_blobs(repo_path, since, to)
        summary.blobs_scanned = len(blobs)

        # Attribute each blob to the oldest commit (by walk order) that
        # contains it, so a hit is reported against its first introduction.
        blob_introduced_at: dict[str, str] = {}
        for commit, _iso in commits:
            for blob_sha in self._blobs_in_commit(repo_path, commit):
                blob_introduced_at.setdefault(blob_sha, commit)

        scanned = 0
        for blob_sha, path in blobs.items():
            scanned += 1
            if path and not self._is_source(path):
                continue
            content = self._read_blob(repo_path, blob_sha)
            findings = await self._scanner.scan_file(path, content, [])
            if not findings:
                continue
            # Gate to the secret-oriented subset by default.
            kept = [
                f for f in findings
                if self._passes_gate(f["kind"], secrets_only=secrets_only)
            ]
            if not kept:
                continue
            commit_sha = blob_introduced_at.get(blob_sha)
            commit_at: datetime | None = None
            for c, iso in commits:
                if c == commit_sha:
                    commit_at = _parse_author_date(iso)
                    break
            inserted = await self._scanner.persist(
                path or "<unknown>",
                kept,
                commit_sha=commit_sha,
                commit_at=commit_at,
            )
            summary.findings_inserted += inserted
            for f in kept:
                sev = f.get("severity", "unknown")
                kind = f.get("kind", "unknown")
                summary.by_severity[sev] = summary.by_severity.get(sev, 0) + 1
                summary.by_kind[kind] = summary.by_kind.get(kind, 0) + 1

            if progress is not None:
                progress(f"scanned blob {scanned}/{summary.blobs_scanned}")

        summary.files_scanned = scanned
        return summary
