"""Pure helper functions for the page generator.

These have no dependency on :class:`PageGenerator` state and are unit-tested
in isolation. ``_extract_summary`` / ``_is_significant_file`` /
``_select_clone_representatives`` keep their original names because external
docs and call sites reference them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY
from repowise.core.ingestion.models import ParsedFile

_INFRA_LANGUAGES = _LANG_REGISTRY.infra_languages()
_INFRA_FILENAMES = frozenset({"Dockerfile", "Makefile", "GNUmakefile"})
_CODE_LANGUAGES = _LANG_REGISTRY.code_languages()


def _now_iso() -> str:
    """Return current UTC time as ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def _extract_summary(content: str, max_chars: int = 320) -> str:
    """Extract a 1-3 sentence purpose blurb from rendered wiki markdown.

    Strategy: walk lines top-to-bottom, skip blanks/headings/list-markers/HTML
    comments, and take the first prose paragraph. Truncate at sentence boundary
    near max_chars. Fully deterministic — no extra LLM call.
    """
    if not content:
        return ""
    para_lines: list[str] = []
    for raw in content.splitlines():
        line = raw.strip()
        if not line:
            if para_lines:
                break
            continue
        if line.startswith(("#", ">", "```", "---", "<!--", "|", "- ", "* ", "1.")):
            if para_lines:
                break
            continue
        para_lines.append(line)
    if not para_lines:
        return ""
    text = " ".join(para_lines)
    if len(text) <= max_chars:
        return text
    # Truncate at the last sentence boundary before max_chars
    cut = text[:max_chars]
    last_period = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    if last_period > max_chars // 2:
        return cut[: last_period + 1]
    return cut.rstrip() + "…"


def overview_summary(content: str) -> str:
    """Return a dependency-context blurb favouring the ## Overview section.

    Distinct from :func:`_extract_summary`: this is the summary fed into
    downstream pages' dependency context and into the vector-store embed
    payload, where the ## Overview lead sentence is the most useful anchor.
    """
    if "## Overview" in content:
        start = content.index("## Overview") + len("## Overview")
        end = content.find("\n##", start)
        return content[start : end if end > 0 else start + 1600].strip()[:400]
    return content[:400]


def _is_infra_file(parsed: ParsedFile) -> bool:
    """Return True if the file is an infrastructure file."""
    lang = parsed.file_info.language
    if lang in _INFRA_LANGUAGES:
        return True
    name = Path(parsed.file_info.path).name
    return name in _INFRA_FILENAMES


def _is_significant_file(
    parsed: ParsedFile,
    pagerank: dict[str, float],
    betweenness: dict[str, float],
    config: Any,  # GenerationConfig
    pr_threshold: float,
) -> bool:
    """Return True if this code file deserves its own file_page.

    A file is significant if it is connected/important in the dependency graph
    (entry point, top PageRank percentile, or bridge file) AND has enough
    content to document.

    The symbol requirement is waived for files with no original definitions
    (state modules, __init__ re-exporters, config files) that are still heavily
    imported — these are architecturally important even without function bodies.
    Package __init__.py files with any symbols are always included since they
    are the public interface of their module.
    """
    path = parsed.file_info.path
    pr = pagerank.get(path, 0.0)
    bet = betweenness.get(path, 0.0)
    is_entry = parsed.file_info.is_entry_point

    # Package __init__.py files are module interfaces — always include them
    # if they have any symbols (re-exports, __getattr__, etc.)
    if path.endswith("__init__.py") and len(parsed.symbols) > 0:
        return True

    # Test files are always significant when present. They have near-zero
    # PageRank because nothing imports them back, but they answer "what
    # tests exercise X" / "where is Y verified" questions that the doc layer
    # is the right place to surface. Users who want to exclude tests
    # entirely can do so via skip_tests in the orchestrator upstream.
    if parsed.file_info.is_test and len(parsed.symbols) > 0:
        return True

    # Must appear significant in the graph
    if not (is_entry or pr >= pr_threshold or bet > 0.0):
        return False

    # Trivial-file gate: small files with almost no symbols (data classes,
    # marker classes, single-message wrappers like Messages/*.cs) produce
    # low-value pages. Entry points and graph hubs (high PageRank) bypass.
    if (
        getattr(config, "skip_trivial_files", True)
        and not is_entry
        and pr < pr_threshold * 2
        and len(parsed.symbols) <= 2
        and parsed.file_info.size_bytes < 1500
    ):
        return False

    # Waive the symbol-count requirement for graph-connected files that have
    # no original definitions of their own (e.g. state/config modules that
    # are imported by many files but mostly re-export or assemble values).
    if len(parsed.symbols) < config.file_page_min_symbols:
        return is_entry or pr >= pr_threshold

    return True


def _select_clone_representatives(
    code_files: list[ParsedFile],
    pagerank: dict[str, float],
    *,
    min_cluster_size: int = 3,
) -> set[str]:
    """Return paths of files to *drop* because they are near-clones.

    Groups files by (parent_directory, signature shape), where the shape is the
    sorted tuple of ``(symbol_kind, symbol_name)`` pairs from the parser. When
    a cluster has at least ``min_cluster_size`` members, the highest-PageRank
    member is kept and the rest are dropped. Entry points are never dropped.

    Language-agnostic: works for any language whose symbols carry a kind+name,
    which the parser guarantees.
    """
    clusters: dict[tuple[str, tuple[tuple[str, str], ...]], list[ParsedFile]] = defaultdict(list)
    for p in code_files:
        if p.file_info.is_entry_point or not p.symbols:
            continue
        parent = str(Path(p.file_info.path).parent.as_posix())
        shape = tuple(sorted((str(s.kind), s.name) for s in p.symbols))
        clusters[(parent, shape)].append(p)

    drop: set[str] = set()
    for members in clusters.values():
        if len(members) < min_cluster_size:
            continue
        # Near-clones usually share a PageRank (often 0.0), so the path breaks
        # the tie. Without it the survivor of each cluster changes between
        # runs, and with it which file gets a page at all.
        members.sort(
            key=lambda p: (-pagerank.get(p.file_info.path, 0.0), p.file_info.path)
        )
        for loser in members[1:]:
            drop.add(loser.file_info.path)
    return drop


def build_dead_code_map(dead_code_report: Any | None) -> dict[str, list[dict]]:
    """Index dead-code findings by file path for per-page lookup."""
    dead_code_by_file: dict[str, list[dict]] = {}
    if dead_code_report is not None and getattr(dead_code_report, "findings", None):
        for f in dead_code_report.findings:
            dead_code_by_file.setdefault(f.file_path, []).append(
                {
                    "symbol_name": f.symbol_name,
                    "symbol_kind": f.symbol_kind,
                    "kind": str(f.kind),
                    "reason": f.reason,
                    "confidence": f.confidence,
                    "safe_to_delete": f.safe_to_delete,
                }
            )
    return dead_code_by_file


def build_decision_maps(
    decision_report: Any | None,
) -> tuple[dict[str, list[dict]], list[dict]]:
    """Index decision records by file path and as a flat list."""
    decisions_by_file: dict[str, list[dict]] = {}
    decisions_all: list[dict] = []
    if decision_report is not None and getattr(decision_report, "decisions", None):
        for d in decision_report.decisions:
            payload = {
                "title": d.title,
                "decision": d.decision,
                "rationale": d.rationale,
                "source": d.source,
                "confidence": d.confidence,
                "evidence_file": d.evidence_file,
            }
            decisions_all.append(payload)
            for fp in d.affected_files or []:
                decisions_by_file.setdefault(fp, []).append(payload)
    return decisions_by_file, decisions_all
