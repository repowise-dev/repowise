"""Persistence of security findings to the ``security_findings`` table."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from .scanner import scan_source

# The pre-split module name, so log routing and filters keep matching.
logger = logging.getLogger("repowise.core.analysis.security_scan")


def _is_missing_table_error(exc: Exception) -> bool:
    """True when *exc* is a 'no such table' / 'does not exist' failure.

    ``replace_findings`` runs against a DB that may not yet have migrated the
    ``security_findings`` table (pre-migration indexing). Those failures are
    expected and skipped silently; everything else is a real error.
    """
    message = str(exc).lower()
    return "no such table" in message or "does not exist" in message


class SecurityScanner:
    """Scan a single file for security signals and persist to the database."""

    def __init__(self, session: AsyncSession, repo_id: str) -> None:
        self._session = session
        self._repo_id = repo_id

    async def scan_file(
        self,
        file_path: str,
        source: str,
        symbols: list[Any],
    ) -> list[dict]:
        """Async wrapper over :func:`scan_source`."""
        return scan_source(file_path, source, symbols)

    def _uses_sqlite(self) -> bool:
        """True when the bound session talks to SQLite (local/dev backend)."""
        try:
            name = self._session.bind.dialect.name  # type: ignore[attr-defined]
        except AttributeError:
            name = ""
        return name == "sqlite"

    async def persist(
        self,
        file_path: str,
        findings: list[dict],
        *,
        commit_sha: str | None = None,
        commit_at: datetime | None = None,
    ) -> int:
        """Insert security findings into the security_findings table.

        Re-runs never duplicate rows: the unique provenance constraint
        (``uq_security_finding_provenance``) makes a conflicting INSERT a no-op.
        We pick the conflict clause per dialect — Postgres supports
        ``ON CONFLICT ON CONSTRAINT ... DO NOTHING``; SQLite uses
        ``INSERT OR IGNORE`` (``ON CONFLICT ON CONSTRAINT`` is unsupported).

        ``commit_sha`` / ``commit_at`` carry the git-history provenance; omit
        them (working-tree scans) and the dedup key stores ``""`` for
        ``commit_sha`` (not NULL) so the unique constraint keys identically
        across runs.

        A per-row failure is skipped (``continue``) rather than aborting the
        whole batch, so one malformed finding cannot silently drop the rest.
        Returns the number of rows actually inserted, taken from the statement's
        ``rowcount`` (the constraint makes duplicate inserts report 0 affected
        rows on Postgres; SQLite reports the inserted count via ``rowcount`` too).
        """
        if not findings:
            return 0

        now = datetime.now(UTC)
        sha_key = commit_sha or ""
        uses_sqlite = self._uses_sqlite()
        if uses_sqlite:
            insert_prefix = "INSERT OR IGNORE INTO security_findings "
            conflict_suffix = ""
        else:
            insert_prefix = "INSERT INTO security_findings "
            conflict_suffix = " ON CONFLICT ON CONSTRAINT uq_security_finding_provenance DO NOTHING"

        inserted = 0
        for finding in findings:
            try:
                result = await self._session.execute(
                    text(
                        insert_prefix
                        + "(repository_id, file_path, kind, severity, snippet, line_number, "
                        "commit_sha, commit_at, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, "
                        ":commit_sha, :commit_at, :detected_at)" + conflict_suffix
                    ),
                    {
                        "repo_id": self._repo_id,
                        "file_path": file_path,
                        "kind": finding["kind"],
                        "severity": finding["severity"],
                        "snippet": finding.get("snippet", ""),
                        "line": finding.get("line", 0),
                        "commit_sha": sha_key,
                        "commit_at": commit_at,
                        "detected_at": now,
                    },
                )
                inserted += max(result.rowcount or 0, 0)
            except Exception:
                logger.warning(
                    "security_finding_persist_failed file_path=%s kind=%s",
                    file_path,
                    finding.get("kind"),
                    exc_info=True,
                )
                continue
        return inserted

    async def replace_findings(
        self,
        findings_by_file: dict[str, list[dict]],
        scanned_paths: list[str],
    ) -> None:
        """Replace the findings rows for every scanned file in one pass.

        Deleting all *scanned* paths (not just those with findings) keeps the
        table idempotent: re-indexing never accumulates duplicate rows, and a
        file whose issues were fixed loses its stale rows. Only working-tree
        rows (``commit_sha`` empty) are replaced — history findings from
        ``scan --history`` are left intact. Uses raw SQL to stay independent of
        any ORM session state; silently skips if the table doesn't exist yet
        (pre-migration).

        Re-running must never lose findings: two findings in one batch can
        share a provenance key (e.g. two keyword symbols on the same line), and
        a plain bulk INSERT would abort the whole batch at the first collision
        — after the DELETE above already removed the file's prior rows. Rows
        are therefore deduplicated in Python and inserted with the same
        conflict-tolerant clauses as ``persist``, so a duplicate key is a no-op
        rather than an abort.
        """
        chunk_size = 400  # SQLite parameter-limit headroom, same as the CRUD layer

        try:
            for i in range(0, len(scanned_paths), chunk_size):
                chunk = scanned_paths[i : i + chunk_size]
                placeholders = ", ".join(f":p{j}" for j in range(len(chunk)))
                params: dict[str, object] = {"repo_id": self._repo_id}
                params.update({f"p{j}": p for j, p in enumerate(chunk)})
                await self._session.execute(
                    text(
                        "DELETE FROM security_findings "
                        "WHERE repository_id = :repo_id "
                        f"AND file_path IN ({placeholders}) "
                        "AND COALESCE(commit_sha, '') = ''"
                    ),
                    params,
                )

            now = datetime.now(UTC)
            rows = [
                {
                    "repo_id": self._repo_id,
                    "file_path": file_path,
                    "kind": finding["kind"],
                    "severity": finding["severity"],
                    "snippet": finding.get("snippet", ""),
                    "line": finding.get("line", 0),
                    "commit_sha": "",
                    "commit_at": None,
                    "detected_at": now,
                }
                for file_path, findings in findings_by_file.items()
                for finding in findings
            ]
            # Two findings can collide on the unique provenance key
            # (repository_id, file_path, kind, line_number, commit_sha) — e.g.
            # two keyword symbols on the same line. Keeping only the first per
            # key makes the batch insertable and lossless (the duplicates are
            # redundant signals, not distinct rows).
            seen: set[tuple[str, str, int]] = set()
            deduped: list[dict] = []
            for row in rows:
                key = (row["file_path"], row["kind"], row["line"])
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)

            if deduped:
                uses_sqlite = self._uses_sqlite()
                insert_prefix = (
                    "INSERT OR IGNORE INTO security_findings "
                    if uses_sqlite
                    else "INSERT INTO security_findings "
                )
                conflict_suffix = (
                    ""
                    if uses_sqlite
                    else " ON CONFLICT ON CONSTRAINT uq_security_finding_provenance DO NOTHING"
                )
                await self._session.execute(
                    text(
                        insert_prefix
                        + "(repository_id, file_path, kind, severity, snippet, line_number, "
                        "commit_sha, commit_at, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, "
                        ":commit_sha, :commit_at, :detected_at)" + conflict_suffix
                    ),
                    deduped,
                )
            from repowise.core.persistence.crud.repository import stamp_analysis_ran

            await stamp_analysis_ran(self._session, self._repo_id, "security")
        except Exception as exc:
            # Pre-migration, the table does not exist yet — silently skip (the
            # historical contract for indexing against a not-yet-migrated DB).
            # Any other failure is a real one and must not be swallowed: a
            # silently dropped batch is how findings disappear.
            if _is_missing_table_error(exc):
                return
            logger.exception(
                "security_findings_replace_failed paths=%d rows=%d",
                len(scanned_paths),
                len(rows) if "rows" in locals() else 0,
            )
