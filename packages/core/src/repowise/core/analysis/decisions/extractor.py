"""Architectural Decision Intelligence - extraction from multiple sources.

Capture sources (see ``decision_provenance.SOURCE_RANK`` for the trust ladder):
    1. Inline markers     (# WHY:, # DECISION:, etc.)
    2. Git archaeology    (significant commit messages)
    3. ADR auto-discovery (Nygard/MADR records — deterministic parse first)
    4. PR / squash-body mining (commit bodies captured in git indexing)
    5. Comment archaeology (LLM rationale prose on high-centrality code)
    + CLI capture (manual entry)

Sources can be disabled per-repo via ``decisions.sources`` in
``.repowise/config.yaml`` (see :data:`SOURCE_NAMES` /
:meth:`DecisionExtractor.extract_all`).

Three sources have been retired, all for the same reason: they mined prose that
describes a repo rather than evidence of a choice made in it, and the records
they produced were never acted on. ``code_comment`` went first (#751) — the
query-time live-grep miner serves the same comments fresh, so persisting them
only flooded the proposed queue. ``readme_mining`` and ``changelog`` follow:
between them they produced 153 records in this project's own store and **zero**
that ever became active, while accounting for most of a 214-deep review queue.
Retired source names are kept in ``SOURCE_RANK`` so rows written before the
removal still rank, and :data:`RETIRED_SOURCES` drives the one-shot purge on the
persist path. The ADR miner still borrows ``README_MINING_PROMPT`` for its
unstructured-file fallback; that is the prompt, not the source.

Determinism-first: ADRs are parsed structurally before any LLM call.
Every extracted decision passes an anti-hallucination substring gate
(:meth:`DecisionExtractor._apply_substring_gate`) — fields not grounded in the
verbatim source span are dropped, and evidence-less decisions are rejected.

All LLM calls are wrapped in try/except - failures never propagate.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.decisions.gate import apply_substring_gate
from repowise.core.analysis.decisions.policy import (
    INDEX_SOURCE_KEYS,
    DecisionPolicy,
    resolve_policy,
)
from repowise.core.analysis.decisions.scope import resolve_module_nodes
from repowise.core.fs_walk import PRUNED_DIRS, walk_repo
from repowise.core.ingestion.traverser import load_gitignore_spec

from .prompts import (
    _SYSTEM_PROMPT,
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


def _coerce_line(value: object) -> int | None:
    """Read an LLM-reported line number, or ``None`` if it isn't one.

    Models answer ``12``, ``"12"`` and ``"line 12"`` interchangeably; anything
    that isn't a positive integer is no attribution at all and must not be
    guessed at.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, str):
        digits = re.search(r"\d+", value)
        if digits:
            line = int(digits.group())
            return line if line > 0 else None
    return None


def _as_aware_utc(value: datetime) -> datetime:
    """Return ``value`` as a timezone-aware UTC datetime.

    SQLite drops timezone information from ``DateTime(timezone=True)`` columns,
    but Repowise writes those values as UTC. Treat naive values as UTC so they
    can be compared with git metadata timestamps, which are already aware UTC.
    """
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _coerce_dt(value: datetime | str) -> datetime:
    """Parse an ISO string into a datetime, passing datetimes through.

    Both shapes reach staleness: the ORM hands back datetimes, while the git
    metadata map carries whatever was persisted, which for SQLite is a string.
    """
    if isinstance(value, str):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value


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
    # ``source`` alone cannot separate the two session lanes. Blank and False
    # mean the lane said nothing, not that it said no.
    lane: str = ""
    needs_split: bool = False


class DecisionSourceError(RuntimeError):
    """Every batch of a decision source failed.

    Raised so :meth:`DecisionExtractor.extract_all` records the source as
    failed rather than empty. A source that loses *some* batches still
    returns what it has and only logs, because partial supply beats none.
    """


def _collect_batches(
    source: str,
    results: list[Any],
) -> list[ExtractedDecision]:
    """Flatten ``asyncio.gather(..., return_exceptions=True)`` output.

    Exceptions are logged per batch. If nothing survived and there was work
    to do, the source failed outright and says so instead of returning a
    zero that reads like an empty repository.
    """
    decisions: list[ExtractedDecision] = []
    errors: list[str] = []
    for result in results:
        if isinstance(result, list):
            decisions.extend(result)
        else:
            errors.append(f"{type(result).__name__}: {result}")
            logger.warning(
                "decision_extractor.batch_failed",
                source=source,
                error=str(result),
            )
    if errors and len(errors) == len(results):
        raise DecisionSourceError(f"all {len(errors)} batch(es) failed — {errors[0]}")
    return decisions


@dataclass
class DecisionExtractionReport:
    total_found: int
    decisions: list[ExtractedDecision]
    by_source: dict[str, int]
    # Sources that raised, {source name: error text}. A source that fails
    # returns an empty list, so without this a total outage and an honestly
    # empty repo are the same number on screen. Callers render it; nothing
    # else can tell the two apart.
    failures: dict[str, str] = field(default_factory=dict)


# Every index-time capture source, in progress order. The CLI derives its
# progress-bar step count from this. Re-exported from the source registry so
# capabilities and run order cannot drift apart.
SOURCE_NAMES: tuple[str, ...] = INDEX_SOURCE_KEYS


def enabled_source_names(repo_config: dict[str, Any] | None) -> tuple[str, ...]:
    """Resolve which index-time capture sources are enabled for a repo.

    Thin wrapper over :func:`resolve_policy`; kept because callers pass a
    loaded config dict and want only the names.
    """
    return resolve_policy(repo_config).policy.enabled_index_sources()


# ---------------------------------------------------------------------------
# Comment marker detection
# ---------------------------------------------------------------------------

# The keyword is deliberately case-SENSITIVE. These are annotation
# conventions, written in caps like TODO:/FIXME:/HACK:, and matching them
# case-insensitively turns ordinary prose into architectural decisions. Two
# real examples from this repo, both of which reached the store as `active`
# records: a wrapped sentence whose continuation line began "# decision:
# namespace, batched like the pages", and a test's "# Rejected: nothing to
# extract." Across 3,860 tracked files those were the ONLY two matches — a
# 100% false-positive rate — because no genuine marker was written in lower
# case. A missed marker costs one record; a false positive publishes a
# sentence fragment as a decision governing every file it touches.
MARKER_RE = re.compile(
    r"^\s*(?:#|//|--|/\*|\*)\s*"
    r"(?P<keyword>WHY|DECISION|TRADEOFF|ADR|RATIONALE|REJECTED)"
    r"\s*:\s*(?P<text>.+)",
)

# fs_walk's never-source set plus the two derived-output names the decision
# walks have always skipped: a bundled ``dist``/``build`` copy of a source file
# would double every marker it contains.
_SKIP_DIRS = PRUNED_DIRS | {"dist", "build"}

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
# ADR / PR / comment source configuration
# ---------------------------------------------------------------------------

# Conventional ADR homes, as repo-root-relative posix directories. Every ``.md``
# directly inside one is a candidate regardless of filename; matching is on the
# whole relative dir, so a stray ``vendor/x/adr/`` does not qualify.
_ADR_DIRS = frozenset(
    {
        "adr",
        "adrs",
        "docs/adr",
        "docs/adrs",
        "docs/decisions",
        "decisions",
        "architecture",
        "doc/adr",
    }
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

# Inline markers sent to the model per call. Caps prompt length only; a file
# with more markers is sent in several calls rather than truncated.
_MARKERS_PER_CALL = 5

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
        policy: DecisionPolicy | None = None,
    ) -> None:
        self._repo_path = Path(repo_path)
        self._provider = provider
        # Per-source model gate. ``None`` means "no policy supplied", which
        # keeps the provider available to every source, as before this existed.
        self._policy = policy
        self._graph = graph
        self._git_meta_map = git_meta_map or {}
        self._parsed_files = parsed_files or []
        # ``source_map`` is ingestion's already-computed {rel_path: bytes} for
        # the indexed file set. When present, the inline-marker scan reuses it
        # for both discovery and reads instead of re-walking the tree and
        # re-reading every file from disk (redundant with ingestion). ``None``
        # keeps the legacy self-walk fallback for callers that don't thread it.
        self._source_map = source_map

    def _llm(self, source: str) -> Any | None:
        """The provider *source* may use, or None when its model stage is off.

        One gate for all five sources: a hybrid source (inline_marker, adr)
        falls back to its deterministic parse, and an LLM-only source returns
        nothing, which is the same shape as having no provider at all.
        """
        if self._policy is not None and not self._policy.llm_allowed(source):
            return None
        return self._provider

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

        marker_llm = self._llm("inline_marker")
        decisions: list[ExtractedDecision] = []

        for file_path, markers in markers_by_file.items():
            # Get 1-hop graph neighbors for affected_files
            affected = self._get_neighbors(file_path)

            markers_by_line = {m["line"]: m for m in markers}

            if marker_llm:
                # Use LLM to structure markers
                try:
                    llm_decisions = await self._structure_markers_via_llm(file_path, markers)
                    for d in llm_decisions:
                        # Attribute the decision to the one marker it was drawn
                        # from (the prompt asks for `marker_line`, which the
                        # parser lands in `evidence_line`). Joining every
                        # marker's context into one span and handing it to all
                        # of them let the substring gate stamp a decision
                        # `exact` against a *different* marker's text, and put
                        # marker 1's line number on marker 3's decision. A
                        # decision we cannot attribute gets no source span at
                        # all: the gate then leaves it `unverified`, which is
                        # the honest verdict, rather than verifying it against
                        # a neighbour.
                        marker = markers_by_line.get(d.evidence_line)
                        if marker is None and len(markers) == 1:
                            marker = markers[0]  # unambiguous without the hint
                        d.evidence_file = file_path
                        d.evidence_line = marker["line"] if marker else None
                        d.affected_files = list({file_path} | set(affected))
                        d.affected_modules = self._infer_modules(d.affected_files)
                        d.source = "inline_marker"
                        d.status = "active"
                        d.confidence = 0.95
                        d.source_text = marker.get("context", "") if marker else ""
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
        """Use LLM to structure inline markers into decision records.

        Markers are sent five per call. The batch size caps prompt length;
        it is not a cap on how many markers a file may have. ``markers[:5]``
        alone dropped every marker past the fifth: the caller invokes this
        once per file, and the raw-marker fallback below only runs on an
        exception, so those markers were never structured and never fell
        back — they left no record and no log line.
        """
        provider = self._llm("inline_marker")
        decisions: list[ExtractedDecision] = []
        for start in range(0, len(markers), _MARKERS_PER_CALL):
            batch = markers[start : start + _MARKERS_PER_CALL]
            markers_block = ""
            for m in batch:
                markers_block += (
                    f"\n--- Marker ({m['keyword']}) at line {m['line']} ---\n"
                    f"Text: {m['text']}\n"
                    f"Surrounding code:\n{m['context'][:1500]}\n"
                )

            prompt = INLINE_MARKER_PROMPT.format(
                file_path=file_path,
                markers_block=markers_block,
            )

            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
            )
            decisions.extend(self._parse_decisions_json(response.content))
        return decisions

    # ------------------------------------------------------------------
    # Source 2: Git archaeology
    # ------------------------------------------------------------------

    async def mine_git_archaeology(self) -> list[ExtractedDecision]:
        """Extract decisions from significant git commits."""
        provider = self._llm("git_archaeology")
        if not provider or not self._git_meta_map:
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
            response = await provider.generate(
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
        decisions.extend(_collect_batches("git_archaeology", list(results)))

        return decisions

    # ------------------------------------------------------------------
    # Source 3: ADR auto-discovery (deterministic-first)
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
        provider = self._llm("adr")

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
            elif provider:
                try:
                    stripped = self._strip_code_blocks(content)
                    prompt = README_MINING_PROMPT.format(file_path=rel, content=stripped[:15_000])
                    response = await provider.generate(
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

        One :func:`walk_repo` pass answers both halves: files directly under a
        conventional ADR directory (root-anchored, as the old shallow globs
        were) rank ahead of loose ``*adr*.md`` name matches, and the cap is
        applied to the two buckets in that order.

        Ignored paths are excluded. :mod:`repowise.core.fs_walk` prunes junk
        dirs and nested repos but deliberately reads no ignore files, so
        gitignore/``info/exclude`` handling is this function's job: without it
        a scratch dir that ``git status`` cannot see contributes ``active``
        decision records citing files no clone of the repo contains.
        """
        ignore = load_gitignore_spec(self._repo_path)
        conventional: list[Path] = []
        loose: list[Path] = []
        for dirpath, dirnames, filenames in walk_repo(self._repo_path, prune_dirs=_SKIP_DIRS):
            rel_dir = dirpath.relative_to(self._repo_path).as_posix()
            rel_dir = "" if rel_dir == "." else rel_dir
            # Prune ignored and packaging-metadata subtrees in place. Ignored
            # DIRECTORIES match with a trailing slash, as git matches them.
            dirnames[:] = [
                d
                for d in dirnames
                if not d.endswith((".egg-info", ".dist-info"))
                and not ignore.match_file(f"{rel_dir}/{d}/" if rel_dir else f"{d}/")
            ]

            in_adr_dir = rel_dir in _ADR_DIRS
            for fname in filenames:
                low = fname.lower()
                if not low.endswith(".md"):
                    continue
                if in_adr_dir:
                    bucket = conventional
                elif "adr" in low and low != "readme.md" and "template" not in low:
                    bucket = loose
                else:
                    continue
                if ignore.match_file(f"{rel_dir}/{fname}" if rel_dir else fname):
                    continue
                bucket.append(dirpath / fname)

            if len(conventional) + len(loose) >= _MAX_ADR_FILES:
                break

        # No dedup pass: walk_repo yields each directory once, and each file
        # lands in exactly one bucket.
        return [*conventional, *loose][:_MAX_ADR_FILES]

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

        # A document with no Status section has not said it is accepted, and a
        # committed ADR is the one artifact allowed to accept its own decision.
        # Defaulting to ``active`` therefore let any draft under docs/adr/ grant
        # itself authority.
        status_key = status.strip().lower().split()[0] if status.strip() else ""
        mapped_status = _ADR_STATUS_MAP.get(status_key, "proposed")

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
    # Source 4: PR / squash-body mining (consumes commit bodies from 1A)
    # ------------------------------------------------------------------

    async def mine_pr_bodies(self) -> list[ExtractedDecision]:
        """Extract decisions from PR / squash-merge commit bodies."""
        provider = self._llm("pr")
        if not provider or not self._git_meta_map:
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
            # Let this propagate to the gather below. Swallowed here, a total
            # provider outage returned five empty lists, so ``_collect_batches``
            # saw no errors and the run reported "Nothing found in: pull
            # requests" — the exact zero-that-means-failure this change exists
            # to remove.
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
            )
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

        results = await asyncio.gather(
            *[_process_batch(b) for b in batches], return_exceptions=True
        )
        return _collect_batches("pr", list(results))

    # ------------------------------------------------------------------
    # Source 5: Comment archaeology (centrality-bounded)
    # ------------------------------------------------------------------

    async def mine_comment_archaeology(self) -> list[ExtractedDecision]:
        """Mine rationale prose from comments on the most central code.

        Bounded to the top-N nodes by PageRank (degree fallback) so it never
        scans the whole tree, and looks for *reasoning* prose (``because``,
        ``instead of`` …) rather than the explicit markers already covered by
        ``scan_inline_markers``.
        """
        provider = self._llm("comment")
        if not provider or self._graph is None:
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
            # Was a bare ``except Exception: return []`` here with no log at
            # all — the quietest of the three swallows, and the one that made
            # "comment: 0" unfalsifiable. Let it propagate to the gather
            # below, which counts it.
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
            )
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
        decisions.extend(_collect_batches("comment", list(results)))
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

    @staticmethod
    def compute_staleness(
        decision_created_at: datetime,
        affected_files: list[str],
        git_meta_map: dict[str, dict],
        decision_text: str = "",
    ) -> float:
        """Fraction of *affected_files* that have changed since the record's birth.

        A fact about the code, not a judgement about the record: 0.0 means
        nothing it governs has moved, 1.0 means all of it has. There is no
        tuned constant left in it, which is the point — the previous formula
        was ``commit_count / 15 * 0.7 + age_days / 365 * 0.3``, whose divisors
        were fitted to one repository's history and produced ~0 for almost
        every record here regardless of whether the code had moved.

        Also gone: a keyword boost that read recent commit *messages* for words
        like "migrate away". That inferred intent from English prose, which
        does not travel, and it mixed a guess into a value other surfaces
        store and compare.

        *decision_text* is accepted and unused, so the two call sites keep
        working; it goes when they do.

        A file with no git metadata **after** the caller's gap fill counts as
        changed: the record names something the repository does not track, so
        it cannot be shown to still hold.
        """
        if not affected_files:
            # No scope, so the question cannot be asked. Callers distinguish
            # this from a genuine 0.0 by the empty file list, and
            # `decision health` reports it as unscoped rather than fresh.
            return 0.0

        created = _as_aware_utc(_coerce_dt(decision_created_at)) if decision_created_at else None
        changed = 0
        for fp in affected_files:
            meta = git_meta_map.get(fp)
            if meta is None:
                changed += 1  # named but not tracked — cannot be shown to hold
                continue
            last_commit = meta.get("last_commit_at")
            if not last_commit or created is None:
                continue
            if _as_aware_utc(_coerce_dt(last_commit)) > created:
                changed += 1

        return round(changed / len(affected_files), 3)

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def _cost_operation(self, operation: str) -> Iterator[None]:
        """Label this extractor's LLM spend as *operation* on the Costs page.

        No-op when the provider has no cost tracker attached (server-side index
        or cost tracking disabled), so extraction is never affected.
        """
        tracker = getattr(self._provider, "_cost_tracker", None)
        if tracker is not None and hasattr(tracker, "record_as"):
            with tracker.record_as(operation):
                yield
        else:
            yield

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

        *enabled_sources* restricts the run to the named sources. It defaults
        to the policy passed at construction, and to everything when there is
        none.

        Every extracted decision is then put through the anti-hallucination
        substring gate (:meth:`_apply_substring_gate`) before being returned —
        ungrounded LLM fields are dropped and evidence-less decisions rejected.
        """

        failures: dict[str, str] = {}

        async def _safe_source(name: str, coro_fn: Any) -> list[ExtractedDecision]:
            try:
                logger.info("decision_extractor.starting", source=name)
                result = await coro_fn()
                logger.info("decision_extractor.finished", source=name, count=len(result))
                return result
            except Exception as exc:
                # Recorded as well as logged: the CLI pins core to ERROR, so
                # a source that dies here used to reach the user as a plain
                # zero indistinguishable from "this repo has no ADRs".
                failures[name] = f"{type(exc).__name__}: {exc}"
                logger.warning("decision_extractor.source_failed", source=name, error=str(exc))
                return []
            finally:
                if on_step:
                    on_step(name)

        # (source name, bound coroutine factory) — order is the progress order.
        all_sources: list[tuple[str, Any]] = [
            ("inline_marker", self.scan_inline_markers),
            ("git_archaeology", self.mine_git_archaeology),
            ("adr", self.discover_adrs),
            ("pr", self.mine_pr_bodies),
            ("comment", self.mine_comment_archaeology),
        ]
        if enabled_sources is None and self._policy is not None:
            enabled_sources = self._policy.enabled_index_sources()
        if enabled_sources is None:
            sources = all_sources
        else:
            enabled = set(enabled_sources)
            sources = [(name, fn) for name, fn in all_sources if name in enabled]
            disabled = [name for name, _fn in all_sources if name not in enabled]
            if disabled:
                logger.info("decision_extractor.sources_disabled", sources=disabled)

        logger.info("decision_extractor.extract_all_start")
        with self._cost_operation("decision_extraction"):
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
            failures=failures,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _iter_scan_targets(self, restrict_to_files: list[str] | None) -> Iterator[tuple[str, str]]:
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

        Walks via :func:`walk_repo`, which prunes junk subtrees and nested git
        repos (separate codebases that must not contribute decisions to the
        parent) without descending into them. When the repo is a git checkout,
        the walk is further restricted to git-tracked files so untracked /
        excluded working directories never contribute decisions; gitless
        indexes fall back to the full walk.
        """
        tracked = self._tracked_files()

        for dirpath, dirnames, filenames in walk_repo(self._repo_path, prune_dirs=_SKIP_DIRS):
            # Skip setuptools build metadata: PKG-INFO embeds the README
            # verbatim, so example marker lines in docs become spurious
            # decisions. Same risk for *.dist-info from wheels.
            dirnames[:] = [d for d in dirnames if not d.endswith((".egg-info", ".dist-info"))]

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
        """Infer the module paths a record governs from the files it names."""
        return resolve_module_nodes(file_paths)

    def _infer_modules_from_text(self, text: str) -> list[str]:
        """Infer module paths by matching *text* against graph directories.

        Used only by the sources that name no files (git archaeology, PRs), so
        the text is all the linkage there is. Matching is on the *deepest*
        directory mentioned rather than its first segment: in a packages/
        layout every node starts with ``packages``, so a first-segment match
        fires on any text that happens to say the word.
        """
        if not self._graph:
            return []
        text_lower = text.lower()
        candidates: set[str] = set()
        for node in self._graph.nodes:
            parent, sep, _ = str(node).replace("\\", "/").strip("/").rpartition("/")
            if sep and parent:
                candidates.add(parent)

        matched = {d for d in candidates if d.lower() in text_lower}
        # Keep only the deepest match on each branch: a text naming
        # ``packages/core/.../decisions`` should not also claim every ancestor.
        deepest = {d for d in matched if not any(o != d and o.startswith(d + "/") for o in matched)}
        return sorted(deepest, key=lambda d: (-d.count("/"), d))[:5]

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
                    # Which marker this came from, for the inline-marker miner's
                    # per-marker attribution. Absent (and left None) for every
                    # other prompt — they scope by sha or by file instead.
                    evidence_line=_coerce_line(item.get("marker_line")),
                    source_quote=item.get("source_quote", ""),
                )
            )
        return decisions
