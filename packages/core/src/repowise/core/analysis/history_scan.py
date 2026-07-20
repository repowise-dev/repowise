"""Full git-history secret/risk scanning.

Complements :mod:`~repowise.core.analysis.security_scan` (which only looks at
the working tree during indexing). ``HistorySecurityScanner.scan_history`` walks
the full git history of a repo and reuses the exact same pattern registry, so a
leaked credential that was "deleted" in a later commit still surfaces — tagged
with the commit that introduced it.

Everything lands in the shared ``security_findings`` table. The
``(repository_id, file_path, kind, line_number, commit_sha)`` unique constraint
(migration 0041) makes re-runs idempotent.

Design notes (in response to review)
------------------------------------
* **Scan unique blobs, not commits x files.** ``git rev-list --objects --all``
  enumerates every object once, deduped by blob SHA, so each distinct blob's
  content is scanned a single time. First-introducing commit provenance comes
  from a single ``git log --reverse --raw`` pass (not ``git ls-tree`` per
  commit). ``git cat-file --batch`` streams blob contents over one process.

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


def _parse_cat_file_batch(data: bytes) -> dict[str, str]:
    """Parse ``git cat-file --batch`` stdout into ``{blob_sha: content}``."""
    contents: dict[str, str] = {}
    pos = 0
    while pos < len(data):
        nl = data.find(b"\n", pos)
        if nl == -1:
            break
        header = data[pos:nl].decode("ascii", errors="replace")
        pos = nl + 1
        parts = header.split()
        if len(parts) < 3:
            continue
        sha = parts[0]
        try:
            size = int(parts[2])
        except ValueError:
            continue
        chunk = data[pos : pos + size]
        pos += size
        if pos < len(data) and data[pos : pos + 1] == b"\n":
            pos += 1
        contents[sha] = chunk.decode("utf-8", errors="replace")
    return contents


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
            parts = line.split(maxsplit=1)
            if len(parts) < 2:
                continue
            obj_sha, path = parts[0], parts[1]
            blobs.setdefault(obj_sha, path)
        return blobs

    def _blob_introductions(
        self, repo_path: Path, since: str | None, to: str | None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """Return ``({blob_sha: first_commit}, {commit_sha: author_iso})``.

        One ``git log --reverse --raw`` pass attributes each blob to the oldest
        commit that introduced it, avoiding an O(commits x files) ``ls-tree``
        loop.
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
            ["log", "--reverse", "--format=%H%x1f%aI", "--raw", rev_range],
            timeout=120.0,
        )
        blob_introduced_at: dict[str, str] = {}
        commit_dates: dict[str, str] = {}
        current_commit: str | None = None

        for line in raw.splitlines():
            if not line:
                continue
            if line.startswith(":"):
                if current_commit is None:
                    continue
                parts = line.split()
                if len(parts) < 3:
                    continue
                blob_sha = parts[2]
                blob_introduced_at.setdefault(blob_sha, current_commit)
                continue
            if "\x1f" not in line:
                continue
            sha, _, iso = line.partition("\x1f")
            sha = sha.strip()
            if not sha:
                continue
            current_commit = sha
            commit_dates[sha] = iso.strip()

        return blob_introduced_at, commit_dates

    def _read_blobs_batch(self, repo_path: Path, blob_shas: list[str]) -> dict[str, str]:
        """Return ``{blob_sha: content}`` via a single ``git cat-file --batch``."""
        if not blob_shas:
            return {}
        try:
            result = subprocess.run(
                ["git", "cat-file", "--batch"],
                cwd=str(repo_path),
                input=("\n".join(blob_shas) + "\n").encode(),
                capture_output=True,
                timeout=120.0,
            )
        except (OSError, subprocess.SubprocessError):
            return {}
        if result.returncode != 0:
            return {}
        return _parse_cat_file_batch(result.stdout)

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

        blobs = self._unique_blobs(repo_path, since, to)
        summary.blobs_scanned = len(blobs)
        blob_introduced_at, commit_dates = self._blob_introductions(repo_path, since, to)

        source_items = [
            (blob_sha, path)
            for blob_sha, path in blobs.items()
            if not path or self._is_source(path)
        ]
        contents_map = self._read_blobs_batch(
            repo_path, [blob_sha for blob_sha, _ in source_items]
        )

        for idx, (blob_sha, path) in enumerate(source_items, start=1):
            summary.files_scanned += 1
            if progress is not None:
                progress(f"scanned blob {idx}/{len(source_items)}")

            content = contents_map.get(blob_sha, "")
            findings = await self._scanner.scan_file(path, content, [])
            if not findings:
                continue

            kept = [
                f for f in findings
                if self._passes_gate(f["kind"], secrets_only=secrets_only)
            ]
            if not kept:
                continue

            commit_sha = blob_introduced_at.get(blob_sha)
            commit_at: datetime | None = None
            if commit_sha:
                commit_at = _parse_author_date(commit_dates.get(commit_sha, ""))

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

        return summary
