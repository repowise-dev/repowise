"""Architectural decision extraction from every index-time source.

Sources run in :data:`SOURCE_NAMES` order (inline markers, git archaeology,
ADRs, PR / squash bodies, comment archaeology, conventions) and can be disabled
per repo via ``decisions.sources`` in ``.repowise/config.yaml``. ADRs are parsed
structurally before any LLM call.

``code_comment``, ``readme_mining`` and ``changelog`` are retired: they mined
prose that describes a repo rather than evidence of a choice made in it. Their
names stay in ``SOURCE_RANK`` so older rows still rank, and
:data:`RETIRED_SOURCES` drives the purge on the persist path.

Every decision passes the anti-hallucination substring gate before it is
returned, and a source that fails is reported in the result, never raised.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import Collection, Iterator, Sequence
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

from .adr import _ADR_STATUS_MAP, bullets, find_adr_files, read_front_matter, split_headings
from .commit_mining import (
    _BATCH_MAX_TOKENS,
    _MAX_PR_BODIES,
    _attribute_to_commit,
    git_commit_block,
    pr_candidates,
    pr_commit_block,
    signal_commits,
)
from .markers import (  # noqa: F401  (MARKER_RE re-exported)
    MARKER_RE,
    find_markers,
    strip_code_blocks,
)
from .model_answers import (  # noqa: F401  (_coerce_paths, _collect_batches re-exported)
    _coerce_paths,
    _collect_batches,
    _run_batches,
    parse_decisions_json,
)
from .prompts import (
    _SYSTEM_PROMPT,
    COMMENT_ARCHAEOLOGY_PROMPT,
    GIT_ARCHAEOLOGY_PROMPT,
    INLINE_MARKER_PROMPT,
    PR_BODY_MINING_PROMPT,
    README_MINING_PROMPT,
)
from .records import (  # noqa: F401  (the two errors are re-exported)
    DecisionExtractionReport,
    DecisionSourceError,
    EmptyModelResponseError,
    ExtractedDecision,
)
from .source_files import (
    _BINARY_EXTENSIONS,
    extract_leading_prose,
    iter_source_files,
)
from .staleness import _as_aware_utc, compute_staleness, last_code_change  # noqa: F401

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
        # Single over-long word: hard cut, still signal truncation.
        return window.rstrip() + "…"
    return window[:cut].rstrip() + "…"


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


def _adr_title(front_matter_title: str, body: str) -> str:
    """The ADR's title: front matter, else its first H1, less any ``ADR-0007:`` prefix."""
    title = front_matter_title
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
        if m:
            title = m.group(1).strip()
    return re.sub(r"^ADR[-\s]*\d+[:\s-]*", "", title, flags=re.IGNORECASE).strip() or title


def _first_section(sections: dict[str, str], *headings: str) -> str:
    """The body of the first of *headings* the ADR fills in, else ``""``."""
    for heading in headings:
        if sections.get(heading):
            return sections[heading]
    return ""


def _adr_status(declared: str) -> str:
    """The record status for an ADR's declared status.

    An undeclared status stays ``proposed``: a committed ADR is the one
    artifact allowed to accept its own decision, so a draft must not.
    """
    status_key = declared.strip().lower().split()[0] if declared.strip() else ""
    return _ADR_STATUS_MAP.get(status_key, "proposed")


_TAG_KEYWORDS = {
    "auth": ["auth", "jwt", "oauth", "token", "session", "login"],
    "database": ["database", "sql", "postgres", "sqlite", "redis", "mongo", "db"],
    "api": ["api", "rest", "graphql", "endpoint", "route"],
    "performance": ["performance", "cache", "speed", "latency", "optimize"],
    "security": ["security", "encrypt", "hash", "cors", "csrf", "xss"],
    "infra": ["docker", "kubernetes", "deploy", "ci", "cd", "terraform"],
    "testing": ["test", "mock", "fixture", "assert"],
}


def _infer_tags(text: str) -> list[str]:
    """Infer tags from decision text."""
    text_lower = text.lower()
    return [
        tag for tag, keywords in _TAG_KEYWORDS.items() if any(kw in text_lower for kw in keywords)
    ]


def _snippet_for(
    decision: ExtractedDecision, batch: Sequence[tuple[str, str]]
) -> tuple[str, str]:
    """The ``(file, prose)`` a comment decision came from, by file stem in its text.

    Best effort: the batch's first snippet when no stem matches.
    """
    hay = (decision.title + " " + decision.decision).lower()
    for fp, prose in batch:
        stem = Path(fp).stem.lower()
        if stem and stem in hay:
            return fp, prose
    return batch[0]


# Above this node count, rank by degree (O(nodes)) instead of solving PageRank:
# comment archaeology only needs a rough "most depended-on files" ranking.
_PAGERANK_NODE_CEILING = 20_000


def _pagerank(g: Any) -> dict[str, float]:
    """PageRank for a graph small enough to solve, else empty."""
    try:
        node_count = g.number_of_nodes()
    except Exception:
        node_count = 0
    if not 0 < node_count <= _PAGERANK_NODE_CEILING:
        return {}
    try:
        import networkx as nx

        return nx.pagerank(g, max_iter=50, tol=1e-4)
    except Exception:
        return {}


def _centrality_scores(g: Any) -> dict[str, float]:
    """Node centrality: PageRank where affordable, else degree; empty when unreadable."""
    scores = _pagerank(g)
    if scores:
        return scores
    try:
        return {node: float(g.degree(node)) for node in g.nodes}
    except Exception:
        return {}


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
        # Per-source model gate; ``None`` lets every source use the provider.
        self._policy = policy
        self._graph = graph
        self._git_meta_map = git_meta_map or {}
        self._parsed_files = parsed_files or []
        # Ingestion's {rel_path: bytes} for the indexed set. When given, the
        # marker scan reads it instead of walking and re-reading the tree.
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

            found = find_markers(rel_path, text)
            if found:
                markers_by_file.setdefault(rel_path, []).extend(found)

        if not markers_by_file:
            return []

        marker_llm = self._llm("inline_marker")
        decisions: list[ExtractedDecision] = []
        for file_path, markers in markers_by_file.items():
            decisions.extend(await self._decisions_from_markers(file_path, markers, marker_llm))
        return decisions

    async def _decisions_from_markers(
        self, file_path: str, markers: list[dict], marker_llm: Any | None
    ) -> list[ExtractedDecision]:
        """One file's markers as decisions: model-structured when possible, else raw."""
        affected = self._get_neighbors(file_path)
        if not marker_llm:
            return [self._raw_decision_from_marker(file_path, m, affected) for m in markers]
        try:
            llm_decisions = await self._structure_markers_via_llm(file_path, markers)
            self._attribute_to_markers(llm_decisions, file_path, markers, affected)
        except Exception:
            logger.warning(
                "decision_extractor.llm_structuring_failed",
                file=file_path,
            )
            return [self._raw_decision_from_marker(file_path, m, affected) for m in markers]
        return llm_decisions

    def _attribute_to_markers(
        self,
        decisions: list[ExtractedDecision],
        file_path: str,
        markers: list[dict],
        affected: list[str],
    ) -> None:
        """Bind each model-structured decision to the marker it was drawn from.

        The model reports ``marker_line``, parsed into ``evidence_line``. A
        decision that names no known marker gets no source span, so the gate
        leaves it ``unverified`` instead of verifying it against another
        marker's text.
        """
        markers_by_line = {m["line"]: m for m in markers}
        for d in decisions:
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
            # No context: the marker's text is all this lane has, and its
            # location is already in evidence_file / evidence_line.
            source="inline_marker",
            status="active",
            confidence=0.7,
            evidence_file=file_path,
            evidence_line=marker["line"],
            affected_files=list({file_path} | set(affected)),
            affected_modules=self._infer_modules([file_path, *affected]),
            tags=_infer_tags(marker["text"]),
            source_quote=marker["text"],
            source_text=marker.get("context", marker["text"]),
        )

    async def _structure_markers_via_llm(
        self, file_path: str, markers: list[dict]
    ) -> list[ExtractedDecision]:
        """Use LLM to structure inline markers into decision records.

        Every marker is sent, ``_MARKERS_PER_CALL`` per call.
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
            decisions.extend(parse_decisions_json(response.content))
        return decisions

    # ------------------------------------------------------------------
    # Source 2: Git archaeology
    # ------------------------------------------------------------------

    async def mine_git_archaeology(self) -> list[ExtractedDecision]:
        """Extract decisions from significant git commits."""
        provider = self._llm("git_archaeology")
        if not provider or not self._git_meta_map:
            return []

        commit_map, commit_files = signal_commits(self._git_meta_map)
        if not commit_map:
            return []

        # Rank by signal count, take top 20
        ranked = sorted(
            commit_map.values(),
            key=lambda c: c["signal_count"],
            reverse=True,
        )[:20]

        # Batch LLM calls (5 commits per batch)
        async def _process_batch(batch: list[dict]) -> list[ExtractedDecision]:
            commits_block = ""
            source_by_sha: dict[str, str] = {}
            for c in batch:
                body = (c.get("body") or "").strip()
                commits_block += git_commit_block(c, body, commit_files.get(c["sha"], []))
                source_by_sha[c["sha"]] = f"{c['message']}\n{body}".strip()

            prompt = GIT_ARCHAEOLOGY_PROMPT.format(commits_block=commits_block)
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=_BATCH_MAX_TOKENS, temperature=0.2
            )
            extracted = parse_decisions_json(response.content)

            # Enrich with commit metadata
            for d in extracted:
                sha = _attribute_to_commit(d, batch, "message", commit_files, source_by_sha)
                d.source = "git_archaeology"
                d.status = "proposed"
                signal = max(
                    (c["signal_count"] for c in batch if c["sha"] == sha),
                    default=1,
                )
                d.confidence = 0.85 if signal >= 2 else 0.70
                d.affected_modules = self._infer_modules(d.affected_files)

            return extracted

        return await _run_batches("git_archaeology", ranked, 5, _process_batch)

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
        adr_paths = find_adr_files(self._repo_path)
        if not adr_paths:
            return []
        provider = self._llm("adr")

        decisions: list[ExtractedDecision] = []
        for path in adr_paths:
            loaded = self._read_adr(path)
            if loaded is None:
                continue
            rel, content = loaded
            parsed = self._parse_adr(content, rel)
            if parsed is not None:
                decisions.append(parsed)
            elif provider:
                decisions.extend(await self._mine_unstructured_adr(provider, rel, content))

        return decisions

    def _read_adr(self, path: Path) -> tuple[str, str] | None:
        """An ADR's repo-relative path and its first 50k chars, or None when unreadable."""
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            return None
        return self._rel_path(path), content[:50_000]

    async def _mine_unstructured_adr(
        self, provider: Any, rel: str, content: str
    ) -> list[ExtractedDecision]:
        """The LLM fallback for a file named like an ADR that has no ADR structure."""
        decisions: list[ExtractedDecision] = []
        try:
            stripped = strip_code_blocks(content)
            prompt = README_MINING_PROMPT.format(file_path=rel, content=stripped[:15_000])
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2000, temperature=0.2
            )
            for d in parse_decisions_json(response.content):
                d.source = "adr"
                d.status = "proposed"
                d.confidence = 0.80
                d.evidence_file = rel
                d.source_text = stripped
                d.affected_modules = self._infer_modules_from_text(d.title + " " + d.decision)
                decisions.append(d)
        except Exception:
            logger.warning("decision_extractor.adr_llm_failed", file=rel)
        return decisions

    def _parse_adr(self, content: str, rel_path: str) -> ExtractedDecision | None:
        """Deterministically parse a structured ADR. Returns None if unstructured.

        Because every field is lifted verbatim from the document, the resulting
        decision is grounded by construction and passes the substring gate as
        ``exact``.
        """
        status, title, body = read_front_matter(content)
        sections = split_headings(body)
        title = _adr_title(title, body)

        context = _first_section(sections, "context", "context and problem statement")
        decision_txt = _first_section(sections, "decision", "decision outcome")
        rationale = _first_section(sections, "rationale", "decision drivers")

        # Require a Decision or Context section; anything less goes to the LLM
        # fallback.
        if not (decision_txt or context):
            return None

        return ExtractedDecision(
            title=_truncate_title(title or rel_path, 200),
            context=context.strip(),
            decision=decision_txt.strip(),
            rationale=rationale.strip(),
            consequences=bullets(sections.get("consequences", "")),
            source="adr",
            status=_adr_status(status or sections.get("status", "")),
            confidence=0.90,
            evidence_file=rel_path,
            source_quote=(decision_txt or context).strip()[:500],
            source_text=content,
            tags=_infer_tags(f"{title} {decision_txt}"),
            affected_modules=self._infer_modules_from_text(f"{title} {decision_txt}"),
        )

    # ------------------------------------------------------------------
    # Source 4: PR / squash-body mining
    # ------------------------------------------------------------------

    async def mine_pr_bodies(self) -> list[ExtractedDecision]:
        """Extract decisions from PR / squash-merge commit bodies."""
        provider = self._llm("pr")
        if not provider or not self._git_meta_map:
            return []

        candidates, files_by_sha = pr_candidates(self._git_meta_map)
        if not candidates:
            return []

        ranked = list(candidates.values())[:_MAX_PR_BODIES]

        async def _process_batch(batch: list[dict]) -> list[ExtractedDecision]:
            bodies_block = ""
            source_by_sha: dict[str, str] = {}
            for c in batch:
                # The file list is what lets the model answer "affected_files".
                bodies_block += pr_commit_block(c, files_by_sha.get(c["sha"], []))
                source_by_sha[c["sha"]] = f"{c['subject']}\n{c['body']}"
            prompt = PR_BODY_MINING_PROMPT.format(bodies_block=bodies_block)
            # Not caught: _run_batches counts a failed batch, so an outage is
            # reported rather than read as nothing found.
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=_BATCH_MAX_TOKENS, temperature=0.2
            )
            extracted = parse_decisions_json(response.content)
            for d in extracted:
                _attribute_to_commit(d, batch, "subject", files_by_sha, source_by_sha)
                d.source = "pr"
                d.status = "proposed"
                d.confidence = 0.80
                d.affected_modules = self._infer_modules(d.affected_files)
            return extracted

        return await _run_batches("pr", ranked, 5, _process_batch)

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
            prose = extract_leading_prose(self._repo_path, fp)
            if prose and any(cue in prose.lower() for cue in _COMMENT_RATIONALE_CUES):
                snippets.append((fp, prose))
        if not snippets:
            return []

        async def _process_batch(batch: list[tuple[str, str]]) -> list[ExtractedDecision]:
            comments_block = ""
            for fp, prose in batch:
                comments_block += f"\n--- {fp} ---\n{prose[:1500]}\n"
            prompt = COMMENT_ARCHAEOLOGY_PROMPT.format(comments_block=comments_block)
            # Not caught: _run_batches counts a failed batch.
            response = await provider.generate(
                _SYSTEM_PROMPT, prompt, max_tokens=2500, temperature=0.2
            )
            extracted = parse_decisions_json(response.content)
            for d in extracted:
                best_fp, best_prose = _snippet_for(d, batch)
                d.source = "comment"
                d.status = "proposed"
                d.confidence = 0.55
                d.evidence_file = best_fp
                d.source_text = best_prose
                d.affected_files = [best_fp]
                d.affected_modules = self._infer_modules([best_fp])
            return extracted

        return await _run_batches("comment", snippets, 4, _process_batch)

    # ------------------------------------------------------------------
    # Source 6: Conventions (deterministic, graph-counted)
    # ------------------------------------------------------------------

    async def scan_conventions(self) -> list[ExtractedDecision]:
        """Majority import patterns the graph proves, one candidate per wrapper and library."""
        from repowise.core.analysis.decisions.conventions import scan_conventions

        if self._graph is None:
            return []
        return scan_conventions(
            self._graph, self._parsed_files, self._source_map, self._repo_path
        )

    def _top_central_files(self, n: int) -> list[str]:
        """Top-*n* file nodes by centrality (existing on disk).

        Uses PageRank on modest graphs and falls back to cheap degree
        centrality on very large graphs (or if networkx is unavailable) so this
        never becomes an ingestion bottleneck.
        """
        if self._graph is None:
            return []
        scores = _centrality_scores(self._graph)
        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        out: list[str] = []
        for node, _score in ranked:
            if len(out) >= n:
                break
            p = self._repo_path / node
            if p.is_file() and p.suffix.lower() not in _BINARY_EXTENSIONS:
                out.append(node)
        return out
    # ------------------------------------------------------------------
    # Anti-hallucination substring gate
    # ------------------------------------------------------------------

    def _apply_substring_gate(
        self, decisions: list[ExtractedDecision]
    ) -> tuple[list[ExtractedDecision], int]:
        """Run the shared anti-hallucination gate over extracted decisions.

        Thin wrapper around :func:`decision_gate.apply_substring_gate`, which
        the docs harvest path shares so both enforce the same grounding rules.
        """
        return apply_substring_gate(decisions)

    # Kept on the class: callers read staleness as ``DecisionExtractor.compute_staleness``.
    compute_staleness = staticmethod(compute_staleness)
    last_code_change = staticmethod(last_code_change)

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
        substring gate (:meth:`_apply_substring_gate`) before being returned:
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
                # Recorded as well as logged: the CLI pins core logging to
                # ERROR, so the report is where a failed source shows up.
                failures[name] = f"{type(exc).__name__}: {exc}"
                logger.warning("decision_extractor.source_failed", source=name, error=str(exc))
                return []
            finally:
                if on_step:
                    on_step(name)

        # (source name, bound coroutine factory), in progress order.
        all_sources: list[tuple[str, Any]] = [
            ("inline_marker", self.scan_inline_markers),
            ("git_archaeology", self.mine_git_archaeology),
            ("adr", self.discover_adrs),
            ("pr", self.mine_pr_bodies),
            ("comment", self.mine_comment_archaeology),
            ("conventions", self.scan_conventions),
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
          No tree walk and no per-file read. Paths are POSIX (``FileInfo.path``),
          matching the graph node keys the neighbour lookup joins against.
        * the tree walk, for callers that pass no ``source_map``.
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

        yield from self._iter_walked_files()

    def _iter_walked_files(self) -> Iterator[tuple[str, str]]:
        """``(rel_path, text)`` for every readable file the legacy tree walk finds."""
        for file_path in iter_source_files(self._repo_path):
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except (OSError, UnicodeDecodeError):
                continue
            yield self._rel_path(file_path), text

    def _rel_path(self, path: Path) -> str:
        """*path* relative to the repo root, or as given when it lies outside it."""
        try:
            return str(path.relative_to(self._repo_path))
        except ValueError:
            return str(path)

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
