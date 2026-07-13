"""Lightweight security signal extractor.

Scans indexed symbols and source for keyword/regex patterns that indicate
authentication, secret handling, raw SQL, dangerous deserialization, etc.

Two scan surfaces share the same pattern registry and persistence layer:

* working-tree scans (during indexing) — ``SecurityScanner.scan_file`` +
  ``persist`` with no commit provenance;
* full-history scans (``repowise security scan --history``) — iterate every
  tracked revision of every source file and persist hits tagged with the
  introducing commit's SHA + author date.

Both paths land in the ``security_findings`` table. The
``(repository_id, file_path, kind, line_number, commit_sha)`` unique
constraint (migration 0037) makes re-runs idempotent.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# ---------------------------------------------------------------------------
# Pattern registry: (compiled_pattern, kind_label, severity)
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (re.compile(r"eval\s*\("), "eval_call", "high"),
    (re.compile(r"exec\s*\("), "exec_call", "high"),
    (re.compile(r"pickle\.loads"), "pickle_loads", "high"),
    (re.compile(r"subprocess\..*shell\s*=\s*True"), "subprocess_shell_true", "high"),
    (re.compile(r"os\.system"), "os_system", "high"),
    (re.compile(r"password\s*=\s*['\"]"), "hardcoded_password", "high"),
    (re.compile(r"(?:api_?key|secret)\s*=\s*['\"]"), "hardcoded_secret", "high"),
    (re.compile(r'f[\'"].*SELECT.*\{.*\}'), "fstring_sql", "med"),
    (re.compile(r'\.execute\(\s*[\'\"]\s*SELECT.*\+'), "concat_sql", "med"),
    (re.compile(r"verify\s*=\s*False"), "tls_verify_false", "med"),
    (re.compile(r"\bmd5\b|\bsha1\b"), "weak_hash", "low"),
]

# Symbol names that are informational security hotspots
_SYMBOL_KEYWORDS = re.compile(
    r"\b(auth|token|password|jwt|session|crypto)\b", re.IGNORECASE
)

# Patterns whose matches are genuine leaked credentials (as opposed to the
# broader "code smell" patterns like os.system/eval). Full-history scans
# default to this subset: a historical commit that *once* called eval() is
# mostly noise, whereas a committed secret is actionable and persists in
# history. This positions history mode as complementary to gitleaks /
# trufflehog rather than a noisy replacement.
SECRET_KINDS: frozenset[str] = frozenset({"hardcoded_password", "hardcoded_secret"})


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
        """Scan *source* text and symbol names; return list of finding dicts.

        Parameters
        ----------
        file_path:
            Relative path of the file (for reference only; not used in scan).
        source:
            Full text content of the file.
        symbols:
            List of symbol objects that have a ``name`` attribute (or similar).
        """
        findings: list[dict] = []
        lines = source.splitlines()

        # Line-by-line pattern scan
        for lineno, line in enumerate(lines, start=1):
            for pattern, kind, severity in _PATTERNS:
                if pattern.search(line):
                    # Trim snippet to keep it concise
                    snippet = line.strip()[:120]
                    findings.append(
                        {
                            "kind": kind,
                            "severity": severity,
                            "snippet": snippet,
                            "line": lineno,
                        }
                    )

        # Symbol-name scan (informational / low)
        for sym in symbols:
            name = getattr(sym, "name", "") or getattr(sym, "qualified_name", "") or ""
            if name and _SYMBOL_KEYWORDS.search(name):
                findings.append(
                    {
                        "kind": "security_sensitive_symbol",
                        "severity": "low",
                        "snippet": name,
                        "line": getattr(sym, "start_line", 0) or 0,
                    }
                )

        return findings

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
        them (working-tree scans) to leave the columns NULL/empty. The dedup key
        uses ``""`` (not NULL) for working-tree findings so the constraint keys
        identically across runs.

        A per-row failure is skipped (``continue``) rather than aborting the
        whole batch, so one malformed finding cannot silently drop the rest.
        Returns the number of rows actually inserted, taken from the statement's
        ``rowcount`` (the constraint makes duplicate inserts report 0 affected
        rows on Postgres; SQLite reports the inserted count via ``rowcount`` too).
        """
        if not findings:
            return 0

        now = datetime.now(UTC)
        # The dedup key uses "" (not NULL) for working-tree findings so the
        # unique constraint keys identically across runs.
        sha_key = commit_sha or ""
        uses_sqlite = self._uses_sqlite()
        if uses_sqlite:
            conflict_clause = "INSERT OR IGNORE INTO security_findings "
        else:
            conflict_clause = (
                "INSERT INTO security_findings "
                "ON CONFLICT ON CONSTRAINT uq_security_finding_provenance "
                "DO NOTHING "
            )

        inserted = 0
        for finding in findings:
            try:
                result = await self._session.execute(
                    text(
                        conflict_clause
                        + "(repository_id, file_path, kind, severity, snippet, line_number, "
                        "commit_sha, commit_at, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, "
                        ":commit_sha, :commit_at, :detected_at)"
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
                continue
        return inserted
