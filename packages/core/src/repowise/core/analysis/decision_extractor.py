"""Architectural Decision Intelligence - extraction from multiple sources.

Capture sources:
    1. Inline markers  (# WHY:, # DECISION:, etc.)       - confidence 0.95
    2. Git archaeology (significant commit messages)       - confidence 0.70-0.85
    3. README / docs mining (implicit decisions in prose)  - confidence 0.60
    4. CLI capture (manual entry)                          - confidence 1.00

All LLM calls are wrapped in try/except - failures never propagate.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

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


@dataclass
class DecisionExtractionReport:
    total_found: int
    decisions: list[ExtractedDecision]
    by_source: dict[str, int]


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
# LLM Prompts
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = (
    "You are an architectural decision extractor. "
    "You extract structured decision records from code context. "
    "Return only valid JSON. Never invent rationale not present in the source."
)

INLINE_MARKER_PROMPT = """\
A developer left architectural decision markers in their code. \
Extract each one as a structured decision record.

Markers found in file: {file_path}

{markers_block}

For each marker, return a JSON object:
{{
  "title": "short title of the decision",
  "context": "what situation forced this",
  "decision": "what was chosen",
  "rationale": "why",
  "alternatives": ["rejected alternatives if mentioned"],
  "consequences": ["tradeoffs if mentioned"],
  "tags": ["relevant tags from: auth, database, api, performance, security, infra, testing"]
}}

Return a JSON array of decision objects. If a marker is not an architectural \
decision, skip it. Return [] if none qualify.
"""

GIT_ARCHAEOLOGY_PROMPT = """\
Analyze these git commits to determine if they represent architectural decisions.

{commits_block}

For each commit that IS an architectural decision (a deliberate choice about \
system structure, patterns, migrations, or technology), return a JSON object:
{{
  "commit_sha": "the sha",
  "title": "short title",
  "context": "what situation forced this",
  "decision": "what was chosen or changed",
  "rationale": "why this approach (infer from message and files only)",
  "alternatives": [],
  "consequences": [],
  "tags": ["relevant tags"]
}}

Return a JSON array. Skip commits that are just bug fixes or minor changes. \
Return [] if none qualify. Do not hallucinate rationale.
"""

README_MINING_PROMPT = """\
Analyze this documentation file and extract any architectural decisions.

File: {file_path}
Content:
{content}

Look for:
- Technology choices and why ("We use X because Y")
- Things replaced or migrated away from
- Explicit architectural constraints
- Design patterns chosen and why

Return a JSON array of decisions:
{{
  "title": "short title",
  "context": "situation that forced it",
  "decision": "what was chosen",
  "rationale": "why",
  "alternatives": [],
  "consequences": [],
  "tags": [],
  "source_quote": "exact quote from the document"
}}

Only extract explicit decisions. Return [] if none found.
"""


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
    ) -> None:
        self._repo_path = Path(repo_path)
        self._provider = provider
        self._graph = graph
        self._git_meta_map = git_meta_map or {}
        self._parsed_files = parsed_files or []

    # ------------------------------------------------------------------
    # Source 1: Inline markers
    # ------------------------------------------------------------------

    async def scan_inline_markers(
        self,
        restrict_to_files: list[str] | None = None,
    ) -> list[ExtractedDecision]:
        """Scan source files for decision markers (WHY:, DECISION:, etc.)."""
        markers_by_file: dict[str, list[dict]] = {}

        if restrict_to_files:
            files_to_scan = [self._repo_path / fp for fp in restrict_to_files]
        else:
            files_to_scan = list(self._iter_source_files())

        total_files = len(files_to_scan)
        logger.info("decision_extractor.scanning_inline_markers", total_files=total_files)
        for idx, file_path in enumerate(files_to_scan):
            if idx > 0 and idx % 1000 == 0:
                logger.info(
                    "decision_extractor.scan_progress",
                    scanned=idx,
                    total=total_files,
                    markers_found=sum(len(v) for v in markers_by_file.values()),
                )
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue

            lines = text.splitlines()
            for line_num, line in enumerate(lines, start=1):
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

                    try:
                        rel_path = str(file_path.relative_to(self._repo_path))
                    except ValueError:
                        rel_path = str(file_path)

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
            title=marker["text"][:100],
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
                signal_count = sum(1 for kw in DECISION_SIGNAL_KEYWORDS if kw in msg.lower())
                if signal_count > 0:
                    commit_map[sha] = {
                        "sha": sha,
                        "message": msg,
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
            for c in batch:
                files = commit_files.get(c["sha"], [])
                commits_block += (
                    f"\n--- Commit {c['sha'][:8]} ---\n"
                    f"Message: {c['message']}\n"
                    f"Author: {c['author']}\n"
                    f"Date: {c['date']}\n"
                    f"Files changed: {', '.join(files[:20])}\n"
                )

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
                decisions.extend(extracted)
            except Exception:
                logger.warning(
                    "decision_extractor.readme_mining_failed",
                    file=rel_path,
                )

        return decisions

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
                _created = decision_created_at
                if isinstance(_created, str):
                    _created = datetime.fromisoformat(_created.replace("Z", "+00:00"))
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

    async def extract_all(self) -> DecisionExtractionReport:
        """Run all capture sources in parallel. LLM failures are caught per-source."""

        async def _safe_inline() -> list[ExtractedDecision]:
            try:
                logger.info("decision_extractor.starting_inline_markers")
                result = await self.scan_inline_markers()
                logger.info("decision_extractor.finished_inline_markers", count=len(result))
                return result
            except Exception as exc:
                logger.warning("decision_extractor.inline_markers_failed", error=str(exc))
                return []

        async def _safe_git() -> list[ExtractedDecision]:
            try:
                logger.info("decision_extractor.starting_git_archaeology")
                result = await self.mine_git_archaeology()
                logger.info("decision_extractor.finished_git_archaeology", count=len(result))
                return result
            except Exception as exc:
                logger.warning("decision_extractor.git_archaeology_failed", error=str(exc))
                return []

        async def _safe_readme() -> list[ExtractedDecision]:
            try:
                logger.info("decision_extractor.starting_readme_mining")
                result = await self.mine_readme_docs()
                logger.info("decision_extractor.finished_readme_mining", count=len(result))
                return result
            except Exception as exc:
                logger.warning("decision_extractor.readme_mining_failed", error=str(exc))
                return []

        logger.info("decision_extractor.extract_all_start")
        inline, git_decisions, readme_decisions = await asyncio.gather(
            _safe_inline(), _safe_git(), _safe_readme()
        )
        logger.info("decision_extractor.extract_all_done")

        decisions = inline + git_decisions + readme_decisions
        by_source = {
            "inline_marker": len(inline),
            "git_archaeology": len(git_decisions),
            "readme_mining": len(readme_decisions),
        }

        return DecisionExtractionReport(
            total_found=len(decisions),
            decisions=decisions,
            by_source=by_source,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _iter_source_files(self):
        """Yield source files under repo_path, skipping irrelevant dirs."""
        for child in self._repo_path.rglob("*"):
            if any(part in _SKIP_DIRS for part in child.parts):
                continue
            if child.is_file() and child.suffix.lower() not in _BINARY_EXTENSIONS:
                yield child

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
                )
            )
        return decisions
