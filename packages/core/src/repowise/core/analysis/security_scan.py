"""Lightweight security signal extractor.

Scans indexed symbols and source for keyword/regex patterns that indicate
authentication, secret handling, raw SQL, dangerous deserialization, etc.

Two scan surfaces share the same pattern registry and persistence layer:

* working-tree scans (during indexing) — ``SecurityScanner.scan_file`` +
  ``replace_findings`` with no commit provenance;
* full-history scans (``repowise security scan --history``) — iterate every
  tracked revision of every source file and persist hits tagged with the
  introducing commit's SHA + author date.

Both paths land in the ``security_findings`` table. The
``(repository_id, file_path, kind, line_number, commit_sha)`` unique
constraint (migration 0041) makes re-runs idempotent.
"""

from __future__ import annotations

import ast
import logging
import re
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Pattern registry: (compiled_pattern, kind_label, severity)
# ---------------------------------------------------------------------------
_CALL_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)*eval\s*\("),
        "eval_call",
        "high",
    ),
    (
        re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)*exec\s*\("),
        "exec_call",
        "high",
    ),
]
_CALL_KINDS = frozenset(kind for _, kind, _ in _CALL_PATTERNS)

# ``exec`` is not a global in JavaScript; the name belongs to
# ``RegExp.prototype.exec``. The receiver-chain prefix above therefore matches
# ``re.exec(str)``, ``/x/.exec(s)`` and ``pattern.exec(xml)`` — idiomatic parsing
# code, reported at ``high``. Measured on a 17-repo TypeScript corpus, every
# ``exec_call`` hit outside Python was a regex match and none was a process
# spawn, so the kind was pure noise on those languages.
#
# The dangerous call in JavaScript comes from ``child_process``, so that is what
# gates it: a file that never names the module cannot spawn one. ``eval`` needs
# no such gate — it is a genuine global there.
_CHILD_PROCESS_IMPORT = re.compile(r"child_process|node:child_process")
_JS_EXEC_CALL = re.compile(r"(?<![\w$])(?:[A-Za-z_$][\w$]*\s*\.\s*)*exec(?:File)?(?:Sync)?\s*\(")

_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    *_CALL_PATTERNS,
    (re.compile(r"pickle\.loads"), "pickle_loads", "high"),
    (re.compile(r"subprocess\..*shell\s*=\s*True"), "subprocess_shell_true", "high"),
    (re.compile(r"os\.system"), "os_system", "high"),
    # Case-insensitive: a credential pinned in source is usually written as a
    # SCREAMING_CASE constant assigned a quoted literal, and the lowercase-only
    # patterns walked straight past that form. Found by scanning a corpus in
    # which a live n8n key sat unreported under exactly that spelling.
    #
    # The wording above avoids spelling the matched form out literally: this
    # loop runs on raw source, comments included, so an example written out here
    # would make the file report itself.
    #
    # Case-insensitivity is written as a scoped inline group rather than the
    # ``re.IGNORECASE`` flag on purpose: ``_ANY_PATTERN`` below is built by
    # concatenating these patterns' *source text*, which drops per-pattern
    # flags. A flag here would leave the prefilter case-sensitive and it would
    # reject the line before the pattern ever ran.
    (re.compile(r"(?i:password)\s*=\s*['\"]"), "hardcoded_password", "high"),
    (re.compile(r"(?i:api_?key|secret)\s*=\s*['\"]"), "hardcoded_secret", "high"),
    (re.compile(r'f[\'"].*SELECT.*\{.*\}'), "fstring_sql", "med"),
    (re.compile(r"\.execute\(\s*[\'\"]\s*SELECT.*\+"), "concat_sql", "med"),
    (re.compile(r"verify\s*=\s*False"), "tls_verify_false", "med"),
    (re.compile(r"\bmd5\b|\bsha1\b"), "weak_hash", "low"),
]

# Combined prefilter: one search per line rejects the (overwhelmingly common)
# clean lines before the per-pattern loop runs. Matches iff some pattern in
# _PATTERNS matches, so findings are unchanged.
_ANY_PATTERN = re.compile("|".join(f"(?:{p.pattern})" for p, _, _ in _PATTERNS))

# Patterns whose calls legitimately span multiple physical lines: the opening
# ``subprocess.<call>(`` lands on one line and ``shell=True`` on another. The
# per-line loop can never see such a call (``.*`` stops at the newline), so it
# gets an extra whole-source pass.
#
# Continuation is restricted to the same physical line or a newline that is
# followed by indentation (``(?:[^\n]|\n(?=[ \t]))``), so a closed
# ``subprocess.run(...)`` cannot jump to a later ``os.popen(..., shell=True)``
# on a column-0 line. The span is also capped (~200 chars) as a second bound.
_SPANNING_PATTERNS: list[tuple[re.Pattern, str, str]] = [
    (
        re.compile(r"subprocess\.[A-Za-z]+\((?:[^\n]|\n(?=[ \t])){0,200}?shell\s*=\s*True"),
        "subprocess_shell_true",
        "high",
    ),
]

# Symbol names that are informational security hotspots
_SYMBOL_KEYWORDS = re.compile(r"\b(auth|token|password|jwt|session|crypto)\b", re.IGNORECASE)

# Patterns whose matches are genuine leaked credentials (as opposed to the
# broader "code smell" patterns like os.system/eval). Full-history scans
# default to this subset: a historical commit that *once* called eval() is
# mostly noise, whereas a committed secret is actionable and persists in
# history. This positions history mode as complementary to gitleaks /
# trufflehog rather than a noisy replacement.
SECRET_KINDS: frozenset[str] = frozenset({"hardcoded_password", "hardcoded_secret"})

# Kinds whose ``snippet`` is a symbol *name* rather than the text of the line
# it sits on (see the symbol-name scan below). Serve-time line verification
# must not treat these like pattern snippets: a bare identifier recurs all over
# a file, so relocating on it lands somewhere arbitrary and failing to find it
# does not mean the code is gone.
SYMBOL_NAME_KINDS: frozenset[str] = frozenset({"security_sensitive_symbol"})


def _mask_comments_and_strings(source: str) -> str:
    """Blank common comments/strings while preserving offsets and newlines."""
    chars = list(source)
    i = 0
    state = "code"
    quote = ""
    triple = False
    template_depth = 0
    while i < len(source):
        if state == "line_comment":
            if source[i] == "\n":
                state = "code"
            else:
                chars[i] = " "
            i += 1
            continue
        if state == "block_comment":
            if source.startswith("*/", i):
                chars[i : i + 2] = [" ", " "]
                i += 2
                state = "code"
            else:
                if source[i] != "\n":
                    chars[i] = " "
                i += 1
            continue
        if state == "string":
            marker = quote * (3 if triple else 1)
            if source.startswith(marker, i):
                chars[i : i + len(marker)] = [" "] * len(marker)
                i += len(marker)
                state = "code"
            elif source[i] == "\\":
                chars[i] = " "
                if i + 1 < len(source):
                    if source[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 2
                else:
                    i += 1
            else:
                if source[i] != "\n":
                    chars[i] = " "
                i += 1
            continue
        if state == "template":
            if source.startswith("${", i):
                chars[i : i + 2] = [" ", " "]
                i += 2
                template_depth = 1
                state = "code"
            elif source[i] == "`":
                chars[i] = " "
                i += 1
                state = "code"
            elif source[i] == "\\":
                chars[i] = " "
                if i + 1 < len(source):
                    if source[i + 1] != "\n":
                        chars[i + 1] = " "
                    i += 2
                else:
                    i += 1
            else:
                if source[i] != "\n":
                    chars[i] = " "
                i += 1
            continue

        if template_depth and source[i] == "{":
            template_depth += 1
            i += 1
        elif template_depth and source[i] == "}":
            chars[i] = " "
            template_depth -= 1
            i += 1
            if template_depth == 0:
                state = "template"
        elif source.startswith("//", i) or source[i] == "#":
            width = 2 if source.startswith("//", i) else 1
            chars[i : i + width] = [" "] * width
            i += width
            state = "line_comment"
        elif source.startswith("/*", i):
            chars[i : i + 2] = [" ", " "]
            i += 2
            state = "block_comment"
        elif source[i] == "`":
            chars[i] = " "
            i += 1
            state = "template"
        elif source[i] in {"'", '"'}:
            quote = source[i]
            triple = source.startswith(quote * 3, i)
            width = 3 if triple else 1
            chars[i : i + width] = [" "] * width
            i += width
            state = "string"
        else:
            i += 1
    return "".join(chars)


def _call_findings(file_path: str, source: str) -> list[dict]:
    """Find executable eval/exec calls with AST or bounded lexical fallback."""
    if file_path.lower().endswith((".py", ".pyi")):
        try:
            tree = ast.parse(source)
        except SyntaxError:
            pass
        else:
            findings = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if isinstance(node.func, ast.Name):
                    call_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    call_name = node.func.attr
                else:
                    continue
                if call_name not in {"eval", "exec"}:
                    continue
                line = source.splitlines()[node.lineno - 1].strip()[:120]
                findings.append(
                    {
                        "kind": f"{call_name}_call",
                        "severity": "high",
                        "snippet": line,
                        "line": node.lineno,
                    }
                )
            return findings

    masked = _mask_comments_and_strings(source)
    lines = source.splitlines()
    findings = []

    def add(kind: str, severity: str, offset: int) -> None:
        lineno = source.count("\n", 0, offset) + 1
        snippet = lines[lineno - 1].strip()[:120] if lineno <= len(lines) else ""
        findings.append({"kind": kind, "severity": severity, "snippet": snippet, "line": lineno})

    is_python = file_path.lower().endswith((".py", ".pyi"))
    for pattern, kind, severity in _CALL_PATTERNS:
        if kind == "exec_call" and not is_python:
            continue  # handled below, gated on child_process
        for match in pattern.finditer(masked):
            add(kind, severity, match.start())

    # The module name is searched in raw source on purpose: it arrives as a
    # string literal (``require("child_process")``), which masking blanks.
    if not is_python and _CHILD_PROCESS_IMPORT.search(source):
        for match in _JS_EXEC_CALL.finditer(masked):
            add("exec_call", "high", match.start())

    return findings


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

        findings.extend(_call_findings(file_path, source))

        # Line-by-line pattern scan
        for lineno, line in enumerate(lines, start=1):
            if not _ANY_PATTERN.search(line):
                continue
            for pattern, kind, severity in _PATTERNS:
                if kind in _CALL_KINDS:
                    continue
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

        # Whole-source pass for patterns that span physical lines
        # (``subprocess.run(\n    ...,\n    shell=True,\n)``). The per-line
        # loop above can never see the sink when the call opens on one line
        # and ``shell=`` lands on another, so scan the full source once. The
        # finding is reported on the line where the call starts, and a match
        # the per-line pass already caught on that line is not duplicated.
        for pattern, kind, severity in _SPANNING_PATTERNS:
            for match in pattern.finditer(source):
                start_line = source.count("\n", 0, match.start()) + 1
                if any(f["kind"] == kind and f["line"] == start_line for f in findings):
                    continue
                line_start = source.rfind("\n", 0, match.start()) + 1
                line_end = source.find("\n", match.start())
                if line_end == -1:
                    line_end = len(source)
                snippet = source[line_start:line_end].strip()[:120]
                findings.append(
                    {
                        "kind": kind,
                        "severity": severity,
                        "snippet": snippet,
                        "line": start_line,
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
