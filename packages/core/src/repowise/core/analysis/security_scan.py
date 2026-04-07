"""Lightweight security signal extractor.

Scans indexed symbols and source for keyword/regex patterns that indicate
authentication, secret handling, raw SQL, dangerous deserialization, etc.

Stores findings in the security_findings table (see migration 0011).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

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

    async def persist(self, file_path: str, findings: list[dict]) -> None:
        """Insert security findings into the security_findings table.

        Uses raw INSERT to stay independent of any ORM session state.
        Silently skips if the table doesn't exist yet (pre-migration).
        """
        from sqlalchemy import text

        if not findings:
            return

        now = datetime.now(UTC)
        for finding in findings:
            try:
                await self._session.execute(
                    text(
                        "INSERT INTO security_findings "
                        "(repository_id, file_path, kind, severity, snippet, line_number, detected_at) "
                        "VALUES (:repo_id, :file_path, :kind, :severity, :snippet, :line, :detected_at)"
                    ),
                    {
                        "repo_id": self._repo_id,
                        "file_path": file_path,
                        "kind": finding["kind"],
                        "severity": finding["severity"],
                        "snippet": finding.get("snippet", ""),
                        "line": finding.get("line", 0),
                        "detected_at": now,
                    },
                )
            except Exception:  # noqa: BLE001 — table may not exist pre-migration
                break
