"""Architectural Decision Intelligence - extraction from multiple sources.

Capture sources (see ``decision_provenance.SOURCE_RANK`` for the trust ladder):
    1. Inline markers     (# WHY:, # DECISION:, etc.)
    2. Git archaeology    (significant commit messages)
    3. README / docs mining (implicit decisions in prose)
    4. ADR auto-discovery (Nygard/MADR records — deterministic parse first)
    5. CHANGELOG mining   (keep-a-changelog Changed/Removed/Deprecated)
    6. PR / squash-body mining (commit bodies captured in git indexing)
    7. Comment archaeology (LLM rationale prose on high-centrality code)
    + CLI capture (manual entry)

Sources can be disabled per-repo via ``decisions.sources`` in
``.repowise/config.yaml`` (see :data:`SOURCE_NAMES` /
:meth:`DecisionExtractor.extract_all`). The former Source 8 (repo-wide
deterministic rationale-comment harvest, ``code_comment``) was removed: the
query-time live-grep miner (``mcp_server/_code_rationale.py``) serves the same
comments fresh, so persisting them only flooded the proposed queue (#751).

Determinism-first: ADR/CHANGELOG are parsed structurally before any LLM call.
Every extracted decision passes an anti-hallucination substring gate
(:meth:`DecisionExtractor._apply_substring_gate`) — fields not grounded in the
verbatim source span are dropped, and evidence-less decisions are rejected.

All LLM calls are wrapped in try/except - failures never propagate.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.gate import apply_substring_gate

from .prompts import (
    _SYSTEM_PROMPT,
    CHANGELOG_MINING_PROMPT,
    COMMENT_ARCHAEOLOGY_PROMPT,
    GIT_ARCHAEOLOGY_PROMPT,
    INLINE_MARKER_PROMPT,
    PR_BODY_MINING_PROMPT,
    README_MINING_PROMPT,
)

logger = structlog.get_logger(__name__)


def _truncate_title(text: str, limit: int) -> str:
    """Truncate a decision title to ``limit`` chars on a word boundary.

    Avoids splitting a word mid-way: trims back to the last whitespace inside
    the limit and appends an ellipsis. Falls back to a hard cut only when the
    first word already exceeds the limit (no boundary to break on).
    """
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    window = text[:limit]
    cut = window.rfind(" ")
    if cut <= 0:
        # Single over-long word — hard cut, still signal truncation.
        return window.rstrip() + "…"
    return window[:cut].rstrip() + "…"


def _as_aware_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    SQLite drops timezone information from ``DateTime(timezone=True)`` columns,
    but Repowise writes those values as UTC. Treat naive values as UTC so they
    can be compared with git metadata timestamps, which are already aware UTC.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ExtractedDecision:
    title: str
    context: str = ""
    decision: str = ""
    rationale: str = ""
    alternatives: list[str] = field(default_factory=list)
    consequences: list[str] = field(default_factory=list)
    affected_files: list[str] = field(default_factory=list)
    affected_modules: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source: str = "inline_marker"
    evidence_commits: list[str] = field(default_factory=list)
    evidence_file: str | None = None
    evidence_line: int | None = None
    confidence: float = 0.5
    status: str = "proposed"
    # The verbatim claimed quote (LLM/parser output) and the verdict from the
    # anti-hallucination substring gate (Phase 1D).
    source_quote: str = ""
    verification: str = "unverified"  # exact | fuzzy | unverified
    # Transient: the verbatim source span this decision was drawn from. Set by
    # each extractor, consumed by the substring gate, then cleared before
    # persistence (the persistence layer ignores unknown dict keys anyway).
    source_text: str = ""


@dataclass
class DecisionExtractionReport:
    total_found: int
    decisions: list[ExtractedDecision]
    by_source: dict[str, int]


# Every index-time capture source, in progress order. The CLI derives its
# progress-bar step count from this; the config gate validates against it.
SOURCE_NAMES: tuple[str, ...] = (
    "inline_marker",
    "git_archaeology",
    "readme_mining",
    "adr",
    "changelog",
    "pr",
    "comment",
)


def enabled_source_names(repo_config: dict[str, Any] | None) -> tuple[str, ...]:
    """Resolve which capture sources are enabled for a repo.

    Reads the ``decisions.sources`` mapping from a loaded
    ``.repowise/config.yaml`` dict (``{source_name: bool}``). Sources absent
    from the mapping default to enabled; unknown keys are ignored so a stale
    config (e.g. the removed ``code_comment``) never breaks extraction.
    """
    cfg = repo_config or {}
    decisions_cfg = cfg.get("decisions") or {}
    sources_cfg = decisions_cfg.get("sources") if isinstance(decisions_cfg, dict) else {}
    if not isinstance(sources_cfg, dict):
        sources_cfg = {}
    return tuple(name for name in SOURCE_NAMES if sources_cfg.get(name, True) is not False)


# ---------------------------------------------------------------------------
# Comment marker detection
# ---------------------------------------------------------------------------

MARKER_RE = re.compile(
    r"^\s*(?:#|//|--|/\*|\*)\s*"
    r"(?P<keyword>WHY|DECISION|TRADEOFF|ADR|RATIONALE|REJECTED)"
    r"\s*:\s*(?P<text>.+)",
    re.IGNORECASE,
)

_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".repowise",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".pytest_cache",
        "dist",
        "build",
        ".next",
        ".nuxt",
    }
)

# Regex to detect fenced code blocks in markdown files (``` or ~~~).
_CODE_FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})")

_BINARY_EXTENSIONS = frozenset(
    {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".svg",
        ".bmp",
        ".webp",
        ".woff",
        ".woff2",
        ".ttf",
        ".eot",
        ".otf",
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".rar",
        ".7z",
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".pyc",
        ".pyo",
        ".so",
        ".dll",
        ".dylib",
        ".exe",
        ".db",
        ".sqlite",
        ".sqlite3",
        ".lance",
        ".lock",
    }
)

# ---------------------------------------------------------------------------
# Decision signal keywords for git archaeology
# ---------------------------------------------------------------------------

DECISION_SIGNAL_KEYWORDS = [
    "migrate",
    "migration",
    "switch to",
    "replace",
    "replaced",
    "refactor to",
    "move from",
    "adopt",
    "introduce",
    "deprecate",
    "remove",
    "drop",
    "upgrade",
    "rewrite",
    "extract",
    "split",
    "convert",
    "transition",
    "revert",
]

# ---------------------------------------------------------------------------
# ADR / CHANGELOG / PR / comment source configuration (Phase 1B)
# ---------------------------------------------------------------------------

# Directories conventionally holding Architecture Decision Records, plus a
# filename glob for ADRs that live loose. Globbed relative to the repo root.
_ADR_DIR_GLOBS = (
    "adr/*.md",
    "adrs/*.md",
    "docs/adr/*.md",
    "docs/adrs/*.md",
    "docs/decisions/*.md",
    "decisions/*.md",
    "architecture/*.md",
    "doc/adr/*.md",
)
_MAX_ADR_FILES = 60

# Nygard/MADR section headings. Mapped to the ExtractedDecision fields they
# populate during the deterministic (LLM-free) parse.
_ADR_HEADING_RE = re.compile(r"^#{1,6}\s+(.+?)\s*$")
_ADR_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_ADR_STATUS_MAP = {
    "accepted": "active",
    "approved": "active",
    "active": "active",
    "proposed": "proposed",
    "draft": "proposed",
    "rejected": "deprecated",
    "deprecated": "deprecated",
    "superseded": "superseded",
}

# CHANGELOG / HISTORY / NEWS filenames (case-insensitive match on the stem).
_CHANGELOG_NAMES = frozenset(
    {"changelog", "history", "news", "changes", "releasenotes", "release-notes"}
)
# keep-a-changelog section headers that carry decision signal. "Added" is
# excluded — additions are rarely *decisions* about structure, just features.
_CHANGELOG_DECISION_SECTIONS = frozenset({"changed", "removed", "deprecated", "security"})
_MAX_CHANGELOG_VERSIONS = 15

# PR/squash body markers — a body containing any of these reads like a PR
# description worth mining (vs an incidental multi-line commit message).
_PR_BODY_MARKERS = (
    "## why",
    "## motivation",
    "## what",
    "## changes",
    "## context",
    "## summary",
    "closes #",
    "fixes #",
    "resolves #",
    "before:",
    "after:",
)
_MAX_PR_BODIES = 25

# Prose that signals rationale in a block comment / docstring (beyond the
# explicit WHY:/DECISION: markers already covered by inline_marker).
_COMMENT_RATIONALE_CUES = (
    "because",
    "instead of",
    "rather than",
    "trade-off",
    "tradeoff",
    "we chose",
    "we decided",
    "the reason",
    "in order to",
    "this avoids",
    "to avoid",
    "deliberately",
    "intentionally",
)
_MAX_COMMENT_NODES = 30

# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Main extractor
# ---------------------------------------------------------------------------


class DecisionExtractor:
    """Extracts architectural decisions from multiple sources."""

    def __init__(
        self,
        repo_path: Path,
        provider: Any | None = None,
        graph: Any | None = None,
        git_meta_map: dict[str, dict] | None = None,
        parsed_files: list[Any] | None = None,
        source_map: dict[str, bytes] | None = None,
    ) -> None:
        self._repo_path = Path(repo_path)
        self._provider = provider
        self._graph = graph
        self._git_meta_map = git_meta_map or {}
        self._parsed_files = parsed_files or []
        # ``source_map`` is ingestion's already-computed {rel_path: bytes} for
        # the indexed file set. When present, the inline-marker scan reuses it
        # for both discovery and reads instead of re-walking the tree and
        # re-reading every file from disk (redundant with ingestion). ``None``
        # keeps the legacy self-walk fallback for callers that don't thread it.
        self._source_map = source_map

    # ------------------------------------------------------------------
    # Source 1: Inline markers
    # ------------------------------------------------------------------

    async def scan_inline_markers(
        self,
        restrict_to_files: list[str] | None = None,
    ) -> list[ExtractedDecision]:
        """Scan source files for decision markers (WHY:, DECISION:, etc.)."""
        markers_by_file: dict[str, list[dict]] = {}

        scan_targets = list(self._iter_scan_targets(restrict_to_files))
        total_files = len(scan_targets)
        logger.info("decision_extractor.scanning_inline_markers", total_files=total_files)
        for idx, (rel_path, text) in enumerate(scan_targets):
            if idx > 0 and idx % 1000 == 0:
                logger.info(
                    "decision_extractor.scan_progress",
                    scanned=idx,
                    total=total_files,
                    markers_found=sum(len(v) for v in markers_by_file.values()),
                )

            lines = text.splitlines()
            # Track whether we're inside a fenced code block in markdown
            # files so we don't treat example markers as real decisions.
            is_markdown = Path(rel_path).suffix.lower() in (".md", ".mdx", ".rst")
            in_code_fence = False
            for line_num, line in enumerate(lines, start=1):
                if is_markdown:
                    fence_match = _CODE_FENCE_RE.match(line)
                    if fence_match:
                        in_code_fence = not in_code_fence
                        continue
                    if in_code_fence:
                        continue
                m = MARKER_RE.match(line)
                if m:
                    # Collect continuation lines (same comment prefix, no keyword)
                    marker_text = m.group("text").strip()
                    for cont_line in lines[line_num : line_num + 5]:
                        cont = cont_line.strip()
                        if cont.startswith(("#", "//", "--", "*")) and ":" not in cont[:20]:
                            # Strip comment prefix
                            cleaned = re.sub(r"^\s*(?:#|//|--|/\*|\*)\s*", "", cont)
                            if cleaned:
                                marker_text += " " + cleaned
                        else:
                            break

                    # Context window: ±20 lines
                    ctx_start = max(0, line_num - 21)
                    ctx_end = min(len(lines), line_num + 20)
                    context = "\n".join(lines[ctx_start:ctx_end])

                    markers_by_file.setdefault(rel_path, []).append(
                        {
                            "keyword": m.group("keyword"),
                            "text": marker_text,
                            "line": line_num,
                            "context": context,
                        }
                    )

        if not markers_by_file:
            return []

        decisions: list[ExtractedDecision] = []

        for file_path, markers in markers_by_file.items():
            # Get 1-hop graph neighbors for affected_files
            affected = self._get_neighbors(file_path)

            # The concatenated marker contexts are the verbatim source span the
            # substring gate verifies the structured decision against.
            marker_source_text = "\n".join(m.get("context", "") for m in markers)

            if self._provider:
                # Use LLM to structure markers
                try:
                    llm_decisions = await self._structure_markers_via_llm(file_path, markers)
                    for d in llm_decisions:
                        d.evidence_file = file_path
                        d.evidence_line = markers[0]["line"] if markers else None
                        d.affected_files = list({file_path} | set(affected))
                        d.affected_modules = self._infer_modules(d.affected_files)
                        d.source = "inline_marker"
                        d.status = "active"
                        d.confidence = 0.95
                        d.source_text = marker_source_text
                    decisions.extend(llm_decisions)
                except Exception:
                    logger.warning(
                        "decision_extractor.llm_structuring_failed",
                        file=file_path,
                    )
                    # Fall through to raw extraction below
                    for marker in markers:
                        decisions.append(
                            self._raw_decision_from_marker(file_path, marker, affected)
                        )
            else:
                # No LLM — create minimal decisions from raw marker text
                for marker in markers:
                    decisions.append(self._raw_decision_from_marker(file_path, marker, affected))

        return decisions

    def _raw_decision_from_marker(
        self,
        file_path: str,
        marker: dict,
        affected: list[str],
    ) -> ExtractedDecision:
        """Create a minimal decision from a raw marker without LLM."""
        return ExtractedDecision(
            title=_truncate_title(marker["text"], 100),
            decision=marker["text"],
            context=f"Found in {file_path}:{marker['line']}",
            source="inline_marker",
            status="active",
            confidence=0.7,
            evidence_file=file_path,
            evidence_line=marker["line"],
            affected_files=list({file_path} | set(affected)),
            affected_modules=self._infer_modules([file_path, *affected]),
            tags=self._infer_tags(marker["text"]),
            source_quote=marker["text"],
            source_text=marker.get("context", marker["text"]),
        )

    async def _structure_markers_via_llm(
        self, file_path: str, markers: list[dict]
    ) -> list[ExtractedDecision]:
        """Use LLM to structure inline markers into decision records."""
        markers_block = ""
        for m in markers[:5]:  # Batch up to 5 per call
            markers_block += (
                f"\n--- Marker ({m['keyword']}) at line {m['line']} ---\n"
                f"Text: {m['text']}\n"
                f"Surrounding code:\n{m['context'][:1500]}\n"
            )

        prompt = INLINE_MARKER_PROMPT.format(
            file_path=file_path,
            markers_block=markers_block,
        )

        response = await self._provider.generate(
            _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
        )
        return self._parse_decisions_json(response.content)

    # ------------------------------------------------------------------
    # Source 2: Git archaeology
    # ------------------------------------------------------------------

    async def mine_git_archaeology(self) -> list[ExtractedDecision]:
        """Extract decisions from significant git commits."""
        if not self._provider or not self._git_meta_map:
            return []

        # Collect unique significant commits with decision signals
        commit_map: dict[str, dict] = {}  # sha → commit info
        commit_files: dict[str, list[str]] = {}  # sha → files

        for file_path, meta in self._git_meta_map.items():
            commits_json = meta.get("significant_commits_json", "[]")
            if isinstance(commits_json, str):
                try:
                    commits = json.loads(commits_json)
                except (json.JSONDecodeError, TypeError):
                    continue
            else:
                commits = commits_json

            for commit in commits:
                sha = commit.get("sha", "")
                if not sha or sha in commit_map:
                    commit_files.setdefault(sha, []).append(file_path)
                    continue
                msg = commit.get("message", "")
                body = commit.get("body", "")
                # Scan subject + body for signals — squash-merge repos carry the
                # decision rationale in the body, not the one-line subject.
                signal_text = f"{msg}\n{body}".lower()
                signal_count = sum(1 for kw in DECISION_SIGNAL_KEYWORDS if kw in signal_text)
                if signal_count > 0:
                    commit_map[sha] = {
                        "sha": sha,
                        "message": msg,
                        "body": body,
                        "author": commit.get("author", ""),
                        "date": commit.get("date", ""),
                        "signal_count": signal_count,
                    }
                    commit_files.setdefault(sha, []).append(file_path)

        if not commit_map:
            return []

        # Rank by signal count, take top 20
        ranked = sorted(
            commit_map.values(),
            key=lambda c: c["signal_count"],
            reverse=True,
        )[:20]

        # Batch LLM calls (5 commits per batch)
        decisions: list[ExtractedDecision] = []
        batches = [ranked[i : i + 5] for i in range(0, len(ranked), 5)]

        async def _process_batch(batch: list[dict]) -> list[ExtractedDecision]:
            commits_block = ""
            source_by_sha: dict[str, str] = {}
            for c in batch:
                files = commit_files.get(c["sha"], [])
                body = (c.get("body") or "").strip()
                body_block = f"Body: {body[:1500]}\n" if body else ""
                commits_block += (
                    f"\n--- Commit {c['sha'][:8]} ---\n"
                    f"Message: {c['message']}\n"
                    f"{body_block}"
                    f"Author: {c['author']}\n"
                    f"Date: {c['date']}\n"
                    f"Files changed: {', '.join(files[:20])}\n"
                )
                source_by_sha[c["sha"]] = f"{c['message']}\n{body}".strip()

            prompt = GIT_ARCHAEOLOGY_PROMPT.format(commits_block=commits_block)
            response = await self._provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
            )
            extracted = self._parse_decisions_json(response.content)

            # Enrich with commit metadata
            for d in extracted:
                sha = d.evidence_commits[0] if d.evidence_commits else ""
                if not sha:
                    # Try to match back to a commit
                    for c in batch:
                        if c["message"][:40].lower() in d.title.lower():
                            sha = c["sha"]
                            break
                if sha:
                    d.evidence_commits = [sha]
                    d.affected_files = commit_files.get(sha, [])
                    d.source_text = source_by_sha.get(sha, "")
                d.source = "git_archaeology"
                d.status = "proposed"
                signal = max(
                    (c["signal_count"] for c in batch if c["sha"] == sha),
                    default=1,
                )
                d.confidence = 0.85 if signal >= 2 else 0.70
                d.affected_modules = self._infer_modules(d.affected_files)

            return extracted

        results = await asyncio.gather(
            *[_process_batch(b) for b in batches],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                decisions.extend(result)
            else:
                logger.warning(
                    "decision_extractor.git_batch_failed",
                    error=str(result),
                )

        return decisions

    # ------------------------------------------------------------------
    # Source 3: README / docs mining
    # ------------------------------------------------------------------

    async def mine_readme_docs(self) -> list[ExtractedDecision]:
        """Extract decisions from documentation files."""
        if not self._provider:
            return []

        doc_patterns = [
            "README.md",
            "CLAUDE.md",
            "ARCHITECTURE.md",
            "CONTRIBUTING.md",
            "DESIGN.md",
            "DECISIONS.md",
        ]
        doc_files: list[Path] = []

        for pattern in doc_patterns:
            p = self._repo_path / pattern
            if p.is_file():
                doc_files.append(p)

        # Also check docs/ directory
        docs_dir = self._repo_path / "docs"
        if docs_dir.is_dir():
            for md_file in docs_dir.rglob("*.md"):
                if len(doc_files) >= 10:
                    break
                doc_files.append(md_file)

        decisions: list[ExtractedDecision] = []

        for doc_path in doc_files[:10]:
            try:
                content = doc_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            # Skip very large files
            if len(content) > 50_000:
                continue

            # Strip fenced code blocks to avoid treating example markers
            # (e.g. `# WHY: ...` in code examples) as real decisions.
            content = self._strip_code_blocks(content)

            try:
                rel_path = str(doc_path.relative_to(self._repo_path))
            except ValueError:
                rel_path = str(doc_path)

            try:
                prompt = README_MINING_PROMPT.format(
                    file_path=rel_path,
                    content=content[:15_000],  # Limit token usage
                )
                response = await self._provider.generate(
                    _SYSTEM_PROMPT, prompt, max_tokens=3000, temperature=0.2
                )
                extracted = self._parse_decisions_json(response.content)
                for d in extracted:
                    d.source = "readme_mining"
                    d.status = "proposed"
                    d.confidence = 0.60
                    d.evidence_file = rel_path
                    d.affected_modules = self._infer_modules_from_text(d.title + " " + d.decision)
                    d.source_text = content
                decisions.extend(extracted)
            except Exception:
                logger.warning(
                    "decision_extractor.readme_mining_failed",
                    file=rel_path,
                )

        return decisions

    # ------------------------------------------------------------------
    # Source 4: ADR auto-discovery (deterministic-first)
    # ------------------------------------------------------------------

    async def discover_adrs(self) -> list[ExtractedDecision]:
        """Discover and parse Architecture Decision Records.

        Deterministic-first: ADRs follow the Nygard/MADR templates (optional
        YAML front-matter + Status / Context / Decision / Consequences
        headings), so structured files are parsed without the LLM. Files that
        carry an ADR name but no recognizable structure fall back to the LLM
        prose miner when a provider is available. Highest source rank.
        """
        adr_paths = self._find_adr_files()
        if not adr_paths:
            return []

        decisions: list[ExtractedDecision] = []
        for path in adr_paths:
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            if len(content) > 50_000:
                content = content[:50_000]
            try:
                rel = str(path.relative_to(self._repo_path))
            except ValueError:
                rel = str(path)

            parsed = self._parse_adr(content, rel)
            if parsed is not None:
                decisions.append(parsed)
            elif self._provider:
                try:
                    stripped = self._strip_code_blocks(content)
                    prompt = README_MINING_PROMPT.format(file_path=rel, content=stripped[:15_000])
                    response = await self._provider.generate(
                        _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
                    )
                    for d in self._parse_decisions_json(response.content):
                        d.source = "adr"
                        d.status = "proposed"
                        d.confidence = 0.80
                        d.evidence_file = rel
                        d.source_text = stripped
                        d.affected_modules = self._infer_modules_from_text(
                            d.title + " " + d.decision
                        )
                        decisions.append(d)
                except Exception:
                    logger.warning("decision_extractor.adr_llm_failed", file=rel)

        return decisions

    def _find_adr_files(self) -> list[Path]:
        """Collect candidate ADR files from the conventional dirs + name match.

        Performance: the conventional-dir globs are shallow (one directory
        each). The loose ``*adr*.md`` scan uses a pruned ``os.walk`` rather than
        a recursive ``**`` glob so it never descends into ``node_modules`` /
        ``.git`` / ``.venv`` on a large repo, and bails as soon as the cap is
        hit.
        """
        import os

        seen: set[Path] = set()
        files: list[Path] = []
        for pattern in _ADR_DIR_GLOBS:
            for p in self._repo_path.glob(pattern):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    files.append(p)

        if len(files) < _MAX_ADR_FILES:
            for dirpath, dirnames, filenames in os.walk(self._repo_path):
                # Prune skip-listed + nested-git subtrees in place.
                dirnames[:] = [
                    d
                    for d in dirnames
                    if d not in _SKIP_DIRS
                    and not d.endswith((".egg-info", ".dist-info"))
                    and not (Path(dirpath) / d / ".git").exists()
                ]
                for fname in filenames:
                    low = fname.lower()
                    if not low.endswith(".md") or "adr" not in low:
                        continue
                    if low == "readme.md" or "template" in low:
                        continue
                    p = Path(dirpath) / fname
                    if p in seen:
                        continue
                    seen.add(p)
                    files.append(p)
                    if len(files) >= _MAX_ADR_FILES:
                        break
                if len(files) >= _MAX_ADR_FILES:
                    break

        return files[:_MAX_ADR_FILES]

    def _parse_adr(self, content: str, rel_path: str) -> ExtractedDecision | None:
        """Deterministically parse a structured ADR. Returns None if unstructured.

        Because every field is lifted verbatim from the document, the resulting
        decision is grounded by construction and passes the substring gate as
        ``exact``.
        """
        status = ""
        title = ""
        body = content
        fm = _ADR_FRONTMATTER_RE.match(content)
        if fm:
            for line in fm.group(1).splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    key = k.strip().lower()
                    val = v.strip().strip("\"'")
                    if key == "status":
                        status = val
                    elif key == "title":
                        title = val
            body = content[fm.end() :]

        sections = self._split_headings(body)
        if not title:
            m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
            if m:
                title = m.group(1).strip()
        # Strip an "ADR-0007:" style prefix from the title.
        title = re.sub(r"^ADR[-\s]*\d+[:\s-]*", "", title, flags=re.IGNORECASE).strip() or title

        context = sections.get("context") or sections.get("context and problem statement", "")
        decision_txt = sections.get("decision") or sections.get("decision outcome", "")
        rationale = sections.get("rationale") or sections.get("decision drivers", "")
        consequences = sections.get("consequences", "")
        if not status:
            status = sections.get("status", "")

        # Require recognizable ADR structure — at minimum a Decision or Context
        # section — otherwise let the LLM fallback handle it.
        if not (decision_txt or context):
            return None

        status_key = status.strip().lower().split()[0] if status.strip() else ""
        mapped_status = _ADR_STATUS_MAP.get(status_key, "active")

        return ExtractedDecision(
            title=_truncate_title(title or rel_path, 200),
            context=context.strip(),
            decision=decision_txt.strip(),
            rationale=rationale.strip(),
            consequences=self._bullets(consequences),
            source="adr",
            status=mapped_status,
            confidence=0.90,
            evidence_file=rel_path,
            source_quote=(decision_txt or context).strip()[:500],
            source_text=content,
            tags=self._infer_tags(f"{title} {decision_txt}"),
            affected_modules=self._infer_modules_from_text(f"{title} {decision_txt}"),
        )

    # ------------------------------------------------------------------
    # Source 5: CHANGELOG mining
    # ------------------------------------------------------------------

    async def mine_changelog(self) -> list[ExtractedDecision]:
        """Mine keep-a-changelog Changed/Removed/Deprecated sections."""
        path = self._find_changelog()
        if path is None:
            return []
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return []
        try:
            rel = str(path.relative_to(self._repo_path))
        except ValueError:
            rel = str(path)

        entries = self._parse_changelog(content)
        if not entries:
            return []

        if self._provider:
            return await self._structure_changelog_entries(entries, rel, content)

        # No LLM — emit raw decisions straight from the decision-y bullets.
        decisions: list[ExtractedDecision] = []
        for section, bullet in entries[:50]:
            decisions.append(
                ExtractedDecision(
                    title=_truncate_title(bullet, 100),
                    decision=bullet,
                    context=f"CHANGELOG · {section.title()}",
                    source="changelog",
                    status="proposed",
                    confidence=0.50,
                    evidence_file=rel,
                    source_quote=bullet,
                    source_text=content,
                    tags=self._infer_tags(bullet),
                )
            )
        return decisions

    def _find_changelog(self) -> Path | None:
        """Locate a CHANGELOG/HISTORY/NEWS file (any extension).

        Checks the repo root first, then the conventional documentation
        subdirectories (``docs/``, ``doc/``, ``.github/``). Many projects keep
        their changelog under ``docs/`` rather than at the root, so a root-only
        scan silently skips this source.
        """
        search_dirs = [
            self._repo_path,
            self._repo_path / "docs",
            self._repo_path / "doc",
            self._repo_path / ".github",
        ]
        for directory in search_dirs:
            if not directory.is_dir():
                continue
            for p in sorted(directory.glob("*")):
                if not p.is_file():
                    continue
                stem = re.sub(r"[^a-z]", "", p.stem.lower())
                if stem in _CHANGELOG_NAMES:
                    return p
        return None

    def _parse_changelog(self, content: str) -> list[tuple[str, str]]:
        """Return ``(section, bullet)`` pairs from decision-relevant sections."""
        entries: list[tuple[str, str]] = []
        version_count = 0
        current_section: str | None = None
        in_version = True
        for line in content.splitlines():
            h2 = re.match(r"^##\s+(.+)$", line)
            if h2:
                name = h2.group(1).strip().lower().strip("[]")
                base = name.split()[0] if name else ""
                if base in _CHANGELOG_DECISION_SECTIONS:
                    # Some changelogs use H2 section headers directly.
                    current_section = base
                else:
                    version_count += 1
                    in_version = version_count <= _MAX_CHANGELOG_VERSIONS
                    current_section = None
                continue
            h3 = re.match(r"^###\s+(.+)$", line)
            if h3:
                current_section = h3.group(1).strip().lower()
                continue
            if not in_version or current_section not in _CHANGELOG_DECISION_SECTIONS:
                continue
            s = line.strip()
            if s.startswith(("-", "*", "+")):
                bullet = s[1:].strip()
                if bullet:
                    entries.append((current_section, bullet))
        return entries

    async def _structure_changelog_entries(
        self, entries: list[tuple[str, str]], rel: str, content: str
    ) -> list[ExtractedDecision]:
        """LLM-structure the highest-signal changelog bullets into decisions."""
        signal = [
            (s, b)
            for (s, b) in entries
            if s in ("removed", "deprecated")
            or any(k in b.lower() for k in DECISION_SIGNAL_KEYWORDS)
        ]
        chosen = (signal or entries)[:40]
        block = "\n".join(f"- [{s.title()}] {b}" for s, b in chosen)
        prompt = CHANGELOG_MINING_PROMPT.format(entries_block=block)
        try:
            response = await self._provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
            )
        except Exception:
            logger.warning("decision_extractor.changelog_mining_failed", file=rel)
            return []
        extracted = self._parse_decisions_json(response.content)
        for d in extracted:
            d.source = "changelog"
            d.status = "proposed"
            d.confidence = 0.60
            d.evidence_file = rel
            d.source_text = content
            d.affected_modules = self._infer_modules_from_text(d.title + " " + d.decision)
        return extracted

    # ------------------------------------------------------------------
    # Source 6: PR / squash-body mining (consumes commit bodies from 1A)
    # ------------------------------------------------------------------

    async def mine_pr_bodies(self) -> list[ExtractedDecision]:
        """Extract decisions from PR / squash-merge commit bodies."""
        if not self._provider or not self._git_meta_map:
            return []

        candidates: dict[str, dict] = {}
        files_by_sha: dict[str, list[str]] = {}
        for fp, meta in self._git_meta_map.items():
            for c in self._loads_commits(meta.get("significant_commits_json")):
                sha = c.get("sha", "")
                if not sha:
                    continue
                files_by_sha.setdefault(sha, []).append(fp)
                body = (c.get("body") or "").strip()
                if sha in candidates or not body:
                    continue
                low = body.lower()
                is_prish = c.get("pr_number") is not None or any(m in low for m in _PR_BODY_MARKERS)
                has_signal = any(k in low for k in DECISION_SIGNAL_KEYWORDS)
                if is_prish and has_signal:
                    candidates[sha] = {
                        "sha": sha,
                        "subject": c.get("message", ""),
                        "body": body,
                        "pr": c.get("pr_number"),
                    }

        if not candidates:
            return []

        ranked = list(candidates.values())[:_MAX_PR_BODIES]
        batches = [ranked[i : i + 5] for i in range(0, len(ranked), 5)]

        async def _process_batch(batch: list[dict]) -> list[ExtractedDecision]:
            bodies_block = ""
            source_by_sha: dict[str, str] = {}
            for c in batch:
                pr_label = f" (PR #{c['pr']})" if c.get("pr") else ""
                bodies_block += (
                    f"\n--- Commit {c['sha'][:8]}{pr_label} ---\n"
                    f"Subject: {c['subject']}\n"
                    f"Body:\n{c['body'][:2000]}\n"
                )
                source_by_sha[c["sha"]] = f"{c['subject']}\n{c['body']}"
            prompt = PR_BODY_MINING_PROMPT.format(bodies_block=bodies_block)
            try:
                response = await self._provider.generate(
                    _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
                )
            except Exception:
                return []
            extracted = self._parse_decisions_json(response.content)
            for d in extracted:
                sha = d.evidence_commits[0] if d.evidence_commits else ""
                if not sha:
                    for c in batch:
                        if c["subject"][:40].lower() in d.title.lower():
                            sha = c["sha"]
                            break
                if sha:
                    d.evidence_commits = [sha]
                    d.affected_files = files_by_sha.get(sha, [])
                    d.source_text = source_by_sha.get(sha, "")
                d.source = "pr"
                d.status = "proposed"
                d.confidence = 0.80
                d.affected_modules = self._infer_modules(d.affected_files)
            return extracted

        decisions: list[ExtractedDecision] = []
        results = await asyncio.gather(
            *[_process_batch(b) for b in batches], return_exceptions=True
        )
        for result in results:
            if isinstance(result, list):
                decisions.extend(result)
            else:
                logger.warning("decision_extractor.pr_batch_failed", error=str(result))
        return decisions

    # ------------------------------------------------------------------
    # Source 7: Comment archaeology (centrality-bounded)
    # ------------------------------------------------------------------

    async def mine_comment_archaeology(self) -> list[ExtractedDecision]:
        """Mine rationale prose from comments on the most central code.

        Bounded to the top-N nodes by PageRank (degree fallback) so it never
        scans the whole tree, and looks for *reasoning* prose (``because``,
        ``instead of`` …) rather than the explicit markers already covered by
        ``scan_inline_markers``.
        """
        if not self._provider or self._graph is None:
            return []

        top_files = self._top_central_files(_MAX_COMMENT_NODES)
        if not top_files:
            return []

        snippets: list[tuple[str, str]] = []
        for fp in top_files:
            prose = self._extract_leading_prose(fp)
            if prose and any(cue in prose.lower() for cue in _COMMENT_RATIONALE_CUES):
                snippets.append((fp, prose))
        if not snippets:
            return []

        decisions: list[ExtractedDecision] = []
        batches = [snippets[i : i + 4] for i in range(0, len(snippets), 4)]

        async def _process_batch(batch: list[tuple[str, str]]) -> list[ExtractedDecision]:
            comments_block = ""
            for fp, prose in batch:
                comments_block += f"\n--- {fp} ---\n{prose[:1500]}\n"
            prompt = COMMENT_ARCHAEOLOGY_PROMPT.format(comments_block=comments_block)
            try:
                response = await self._provider.generate(
                    _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
                )
            except Exception:
                return []
            extracted = self._parse_decisions_json(response.content)
            # Best-effort attribution to the originating file by token overlap.
            for d in extracted:
                best_fp, best_prose = batch[0]
                hay = (d.title + " " + d.decision).lower()
                for fp, prose in batch:
                    stem = Path(fp).stem.lower()
                    if stem and stem in hay:
                        best_fp, best_prose = fp, prose
                        break
                d.source = "comment"
                d.status = "proposed"
                d.confidence = 0.55
                d.evidence_file = best_fp
                d.source_text = best_prose
                d.affected_files = [best_fp]
                d.affected_modules = self._infer_modules([best_fp])
            return extracted

        results = await asyncio.gather(
            *[_process_batch(b) for b in batches], return_exceptions=True
        )
        for result in results:
            if isinstance(result, list):
                decisions.extend(result)
        return decisions

    # Above this node count, skip the iterative PageRank solve and use degree
    # centrality (O(nodes)) instead — comment archaeology only needs a rough
    # "most depended-on files" ranking, not exact PageRank, and the iterative
    # solve would otherwise add seconds on very large graphs.
    _PAGERANK_NODE_CEILING = 20_000

    def _top_central_files(self, n: int) -> list[str]:
        """Top-*n* file nodes by centrality (existing on disk).

        Uses PageRank on modest graphs and falls back to cheap degree
        centrality on very large graphs (or if networkx is unavailable) so this
        never becomes an ingestion bottleneck.
        """
        g = self._graph
        if g is None:
            return []
        scores: dict[str, float] = {}
        try:
            node_count = g.number_of_nodes()
        except Exception:
            node_count = 0
        if 0 < node_count <= self._PAGERANK_NODE_CEILING:
            try:
                import networkx as nx

                scores = nx.pagerank(g, max_iter=50, tol=1e-4)
            except Exception:
                scores = {}
        if not scores:
            try:
                scores = {node: float(g.degree(node)) for node in g.nodes}
            except Exception:
                return []
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[str] = []
        for node, _score in ranked:
            if len(out) >= n:
                break
            p = self._repo_path / node
            if p.is_file() and p.suffix.lower() not in _BINARY_EXTENSIONS:
                out.append(node)
        return out

    def _extract_leading_prose(self, file_path: str) -> str:
        """Return the leading module docstring / header comment block of a file."""
        p = self._repo_path / file_path
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return ""
        prose: list[str] = []
        in_doc = False
        for line in text.splitlines()[:120]:
            s = line.strip()
            if not in_doc and (s.startswith('"""') or s.startswith("'''")):
                quote = s[:3]
                inner = s.strip("\"' ")
                if inner:
                    prose.append(inner)
                # Single-line docstring closes on the same line.
                if s.count(quote) < 2:
                    in_doc = True
                continue
            if in_doc:
                if s.endswith('"""') or s.endswith("'''"):
                    in_doc = False
                    inner = s.strip("\"' ")
                    if inner:
                        prose.append(inner)
                else:
                    prose.append(s)
                continue
            if s.startswith(("#", "//", "*", "/*", "--")):
                cleaned = re.sub(r"^\s*(?:#|//|--|/\*|\*/|\*)\s?", "", s)
                if cleaned:
                    prose.append(cleaned)
        return "\n".join(prose).strip()[:2000]

    @staticmethod
    def _loads_commits(value: Any) -> list[dict]:
        """Parse a ``significant_commits_json`` blob into a list of dicts."""
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                data = json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return []
            return data if isinstance(data, list) else []
        return []

    @staticmethod
    def _split_headings(text: str) -> dict[str, str]:
        """Map lowercased markdown headings to their section bodies."""
        sections: dict[str, str] = {}
        current: str | None = None
        buf: list[str] = []
        for line in text.splitlines():
            m = _ADR_HEADING_RE.match(line)
            if m:
                if current is not None:
                    sections[current] = "\n".join(buf).strip()
                current = m.group(1).strip().lower()
                buf = []
            elif current is not None:
                buf.append(line)
        if current is not None:
            sections[current] = "\n".join(buf).strip()
        return sections

    @staticmethod
    def _bullets(text: str) -> list[str]:
        """Extract markdown bullet items from a section body."""
        out: list[str] = []
        for line in text.splitlines():
            s = line.strip()
            if s.startswith(("-", "*", "+")):
                item = s[1:].strip()
                if item:
                    out.append(item)
        return out

    # ------------------------------------------------------------------
    # Anti-hallucination substring gate (Phase 1D)
    # ------------------------------------------------------------------

    def _apply_substring_gate(
        self, decisions: list[ExtractedDecision]
    ) -> tuple[list[ExtractedDecision], int]:
        """Run the shared anti-hallucination gate over extracted decisions.

        Thin wrapper around :func:`decision_gate.apply_substring_gate` — the
        gate orchestration is factored out so the Phase-2 LLM-docs harvest path
        enforces the *same* grounding rules. See that module for the contract.
        """
        return apply_substring_gate(decisions)

    # ------------------------------------------------------------------
    # Staleness computation (static method)
    # ------------------------------------------------------------------

    # Keywords that signal a decision may have been contradicted or superseded.
    _CONFLICT_SIGNALS = frozenset(
        {
            "replace",
            "remove",
            "deprecate",
            "switch from",
            "migrate away",
            "drop",
            "revert",
            "undo",
            "disable",
            "eliminate",
        }
    )

    @staticmethod
    def compute_staleness(
        decision_created_at: datetime,
        affected_files: list[str],
        git_meta_map: dict[str, dict],
        decision_text: str = "",
    ) -> float:
        """Compute staleness score for a decision. Returns 0.0-1.0.

        In addition to commit volume and age, checks whether recent commit
        messages contain keywords that conflict with the decision text
        (e.g. decision says "use Redis" but a recent commit says "migrate
        away from Redis").  This boosts staleness when the underlying code
        may have diverged from the decision's intent.
        """
        if not affected_files:
            return 0.0

        now = datetime.now(UTC)
        scores: list[float] = []
        decision_lower = decision_text.lower()

        for fp in affected_files:
            meta = git_meta_map.get(fp)
            if meta is None:
                scores.append(1.0)  # File missing / not tracked
                continue

            last_commit = meta.get("last_commit_at")
            if last_commit and decision_created_at:
                if isinstance(last_commit, str):
                    last_commit = datetime.fromisoformat(last_commit.replace("Z", "+00:00"))
                last_commit = _as_aware_utc(last_commit)
                _created = decision_created_at
                if isinstance(_created, str):
                    _created = datetime.fromisoformat(_created.replace("Z", "+00:00"))
                _created = _as_aware_utc(_created)
                if last_commit > _created:
                    age_days = (now - _created).days
                    commit_count = meta.get("commit_count_90d", 0)
                    base_score = min(
                        1.0,
                        commit_count / 15 * 0.7 + age_days / 365 * 0.3,
                    )

                    # Keyword conflict boost: check if recent commits
                    # contradict the decision's content.
                    conflict_boost = 0.0
                    if decision_lower:
                        sig_json = meta.get("significant_commits_json", "[]")
                        try:
                            sig_commits = (
                                json.loads(sig_json) if isinstance(sig_json, str) else sig_json
                            )
                        except (json.JSONDecodeError, TypeError):
                            sig_commits = []
                        for sc in sig_commits:
                            sc_date = sc.get("date", "")
                            # Only consider commits after the decision was created
                            if sc_date and sc_date > _created.isoformat():
                                msg_lower = sc.get("message", "").lower()
                                for signal in DecisionExtractor._CONFLICT_SIGNALS:
                                    if signal in msg_lower:
                                        # Check if the commit message shares meaningful
                                        # words with the decision text (context overlap)
                                        msg_words = set(msg_lower.split())
                                        dec_words = set(decision_lower.split())
                                        overlap = msg_words & dec_words - {
                                            "the",
                                            "a",
                                            "an",
                                            "to",
                                            "in",
                                            "for",
                                            "and",
                                            "or",
                                            "of",
                                            "is",
                                            "was",
                                            "with",
                                        }
                                        if len(overlap) >= 2:
                                            conflict_boost = max(conflict_boost, 0.3)
                                            break

                    score = min(1.0, base_score + conflict_boost)
                    scores.append(score)
                else:
                    scores.append(0.0)
            else:
                scores.append(0.0)

        return round(sum(scores) / len(scores), 3) if scores else 0.0

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def extract_all(
        self,
        *,
        on_step: Any | None = None,
        enabled_sources: Collection[str] | None = None,
    ) -> DecisionExtractionReport:
        """Run all capture sources in parallel. LLM failures are caught per-source.

        *on_step* is an optional callable invoked with the source name as each
        sub-extractor finishes (the names in :data:`SOURCE_NAMES`). Used by the
        CLI to surface per-source progress.

        *enabled_sources* restricts the run to the named sources (``None`` runs
        everything). Callers derive it from ``decisions.sources`` in
        ``.repowise/config.yaml`` via :func:`enabled_source_names`.

        Every extracted decision is then put through the anti-hallucination
        substring gate (:meth:`_apply_substring_gate`) before being returned —
        ungrounded LLM fields are dropped and evidence-less decisions rejected.
        """

        async def _safe_source(name: str, coro_fn: Any) -> list[ExtractedDecision]:
            try:
                logger.info("decision_extractor.starting", source=name)
                result = await coro_fn()
                logger.info("decision_extractor.finished", source=name, count=len(result))
                return result
            except Exception as exc:
                logger.warning("decision_extractor.source_failed", source=name, error=str(exc))
                return []
            finally:
                if on_step:
                    on_step(name)

        # (source name, bound coroutine factory) — order is the progress order.
        all_sources: list[tuple[str, Any]] = [
            ("inline_marker", self.scan_inline_markers),
            ("git_archaeology", self.mine_git_archaeology),
            ("readme_mining", self.mine_readme_docs),
            ("adr", self.discover_adrs),
            ("changelog", self.mine_changelog),
            ("pr", self.mine_pr_bodies),
            ("comment", self.mine_comment_archaeology),
        ]
        if enabled_sources is None:
            sources = all_sources
        else:
            enabled = set(enabled_sources)
            sources = [(name, fn) for name, fn in all_sources if name in enabled]
            disabled = [name for name, _fn in all_sources if name not in enabled]
            if disabled:
                logger.info("decision_extractor.sources_disabled", sources=disabled)

        logger.info("decision_extractor.extract_all_start")
        results = await asyncio.gather(*[_safe_source(name, fn) for name, fn in sources])
        logger.info("decision_extractor.extract_all_done")

        # Raw per-source pool, then the anti-hallucination gate.
        raw: list[ExtractedDecision] = []
        by_source: dict[str, int] = {}
        for (name, _fn), source_decisions in zip(sources, results, strict=True):
            by_source[name] = len(source_decisions)
            raw.extend(source_decisions)

        decisions, rejected = self._apply_substring_gate(raw)
        logger.info(
            "decision_extractor.substring_gate",
            kept=len(decisions),
            rejected=rejected,
        )

        return DecisionExtractionReport(
            total_found=len(decisions),
            decisions=decisions,
            by_source=by_source,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _iter_scan_targets(
        self, restrict_to_files: list[str] | None
    ) -> Iterator[tuple[str, str]]:
        """Yield ``(rel_path, text)`` for every file the marker scan covers.

        Three sources, in priority order:

        * ``restrict_to_files`` (update path): the caller's explicit change set.
          Text comes from ``source_map`` when the file was just ingested, else a
          targeted disk read; deleted / unreadable paths are skipped.
        * ``source_map`` (init path): ingestion's already-decoded indexed set.
          Discovery AND reads are free — no tree walk, no per-file ``read_text``.
          Paths are POSIX (``FileInfo.path``), matching the graph node keys the
          neighbour lookup joins against.
        * legacy self-walk (``source_map is None``): the original ``os.walk`` +
          git-tracked filter, kept so callers that don't thread ``source_map``
          behave exactly as before.
        """
        if restrict_to_files:
            for rel_path in restrict_to_files:
                text = self._read_source_text(rel_path)
                if text is not None:
                    yield rel_path, text
            return

        if self._source_map is not None:
            for rel_path, source in self._source_map.items():
                yield rel_path, source.decode("utf-8", errors="replace")
            return

        for file_path in self._iter_source_files():
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            try:
                rel_path = str(file_path.relative_to(self._repo_path))
            except ValueError:
                rel_path = str(file_path)
            yield rel_path, text

    def _read_source_text(self, rel_path: str) -> str | None:
        """Decode one file's text, preferring ingestion's in-memory bytes.

        Falls back to a disk read (deleted / unreadable → ``None``) so the
        update path stays correct for files that aren't in ``source_map``.
        """
        if self._source_map is not None:
            source = self._source_map.get(rel_path)
            if source is not None:
                return source.decode("utf-8", errors="replace")
        abs_path = self._repo_path / rel_path
        if not abs_path.is_file():
            return None
        try:
            return abs_path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return None

    def _tracked_files(self) -> set[Path] | None:
        """Resolved paths git tracks under ``repo_path``, or ``None``.

        ``None`` means "no git scoping available" (not a git repo, git missing,
        or the command failed) — callers then fall back to walking the tree.
        Restricting to tracked files keeps untracked / gitignored / git-excluded
        working directories (``local-stash/``, vendored dumps, scratch folders)
        out of the harvest: their comments are not part of the indexed codebase
        and must not become decision records.
        """
        import subprocess

        try:
            proc = subprocess.run(
                ["git", "-C", str(self._repo_path), "ls-files", "-z"],
                capture_output=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0 or not proc.stdout:
            return None
        out = proc.stdout.decode("utf-8", errors="replace")
        tracked: set[Path] = set()
        for rel in out.split("\0"):
            if not rel:
                continue
            try:
                tracked.add((self._repo_path / rel).resolve())
            except OSError:
                continue
        return tracked or None

    def _iter_source_files(self):
        """Yield source files under repo_path, skipping irrelevant dirs.

        Uses os.walk so we can prune entire subtrees (nested git repos,
        node_modules, etc.) without descending into them. When the repo is a git
        checkout, the walk is further restricted to git-tracked files so
        untracked / excluded working directories never contribute decisions;
        gitless indexes fall back to the full walk.
        """
        import os

        tracked = self._tracked_files()

        for dirpath, dirnames, filenames in os.walk(self._repo_path):
            # Prune skip-listed directories in-place so os.walk won't descend
            dirnames[:] = [
                d
                for d in dirnames
                if d not in _SKIP_DIRS
                # Skip setuptools build metadata: PKG-INFO embeds the README
                # verbatim, so example marker lines in docs become spurious
                # decisions. Same risk for *.dist-info from wheels.
                and not d.endswith(".egg-info")
                and not d.endswith(".dist-info")
                # Skip nested git repositories — they are separate codebases
                # and should not contribute decisions to the parent repo.
                and not (Path(dirpath) / d / ".git").exists()
            ]

            for fname in filenames:
                fpath = Path(dirpath) / fname
                if fpath.suffix.lower() in _BINARY_EXTENSIONS:
                    continue
                if tracked is not None:
                    try:
                        if fpath.resolve() not in tracked:
                            continue
                    except OSError:
                        continue
                yield fpath

    def _get_neighbors(self, file_path: str) -> list[str]:
        """Get 1-hop graph neighbors for a file."""
        if self._graph is None:
            return []
        neighbors: set[str] = set()
        if file_path in self._graph:
            neighbors.update(self._graph.successors(file_path))
            neighbors.update(self._graph.predecessors(file_path))
        neighbors.discard(file_path)
        return list(neighbors)[:20]  # Cap at 20

    @staticmethod
    def _strip_code_blocks(text: str) -> str:
        """Remove fenced code blocks from markdown to avoid parsing examples."""
        lines = text.splitlines()
        out: list[str] = []
        in_fence = False
        for line in lines:
            if _CODE_FENCE_RE.match(line):
                in_fence = not in_fence
                continue
            if not in_fence:
                out.append(line)
        return "\n".join(out)

    def _infer_modules(self, file_paths: list[str]) -> list[str]:
        """Infer top-level module paths from file paths."""
        modules: set[str] = set()
        for fp in file_paths:
            parts = fp.replace("\\", "/").split("/")
            if len(parts) > 1:
                modules.add(parts[0])
        return sorted(modules)

    def _infer_modules_from_text(self, text: str) -> list[str]:
        """Infer module names by matching text against graph nodes."""
        if not self._graph:
            return []
        modules: set[str] = set()
        text_lower = text.lower()
        for node in self._graph.nodes:
            parts = node.replace("\\", "/").split("/")
            if len(parts) > 1 and parts[0].lower() in text_lower:
                modules.add(parts[0])
        return sorted(modules)[:5]

    def _infer_tags(self, text: str) -> list[str]:
        """Infer tags from decision text."""
        tag_keywords = {
            "auth": ["auth", "jwt", "oauth", "token", "session", "login"],
            "database": ["database", "sql", "postgres", "sqlite", "redis", "mongo", "db"],
            "api": ["api", "rest", "graphql", "endpoint", "route"],
            "performance": ["performance", "cache", "speed", "latency", "optimize"],
            "security": ["security", "encrypt", "hash", "cors", "csrf", "xss"],
            "infra": ["docker", "kubernetes", "deploy", "ci", "cd", "terraform"],
            "testing": ["test", "mock", "fixture", "assert"],
        }
        text_lower = text.lower()
        tags = []
        for tag, keywords in tag_keywords.items():
            if any(kw in text_lower for kw in keywords):
                tags.append(tag)
        return tags

    def _parse_decisions_json(self, content: str) -> list[ExtractedDecision]:
        """Parse LLM response as JSON array of decisions."""
        # Extract JSON from response (may be wrapped in markdown code blocks)
        content = content.strip()
        if content.startswith("```"):
            # Remove markdown code fences
            lines = content.split("\n")
            content = "\n".join(line for line in lines if not line.strip().startswith("```"))

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            # Try to find JSON array in the response
            match = re.search(r"\[.*\]", content, re.DOTALL)
            if match:
                try:
                    data = json.loads(match.group())
                except json.JSONDecodeError:
                    return []
            else:
                return []

        if isinstance(data, dict):
            data = [data]
        if not isinstance(data, list):
            return []

        decisions = []
        for item in data:
            if not isinstance(item, dict):
                continue
            title = item.get("title", "")
            if not title:
                continue
            decisions.append(
                ExtractedDecision(
                    title=title,
                    context=item.get("context", ""),
                    decision=item.get("decision", ""),
                    rationale=item.get("rationale", ""),
                    alternatives=item.get("alternatives", []),
                    consequences=item.get("consequences", []),
                    tags=item.get("tags", []),
                    evidence_commits=[item["commit_sha"]] if "commit_sha" in item else [],
                    source_quote=item.get("source_quote", ""),
                )
            )
        return decisions
