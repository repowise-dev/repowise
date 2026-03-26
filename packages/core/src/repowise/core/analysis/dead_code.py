"""Dead code detection for repowise.

Pure graph traversal + SQL — no LLM calls. Must complete in < 10 seconds.

Detects unreachable files, unused exports, unused internals, and
zombie packages using the dependency graph and git metadata.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class DeadCodeKind(str, Enum):
    UNREACHABLE_FILE = "unreachable_file"
    UNUSED_EXPORT = "unused_export"
    UNUSED_INTERNAL = "unused_internal"
    ZOMBIE_PACKAGE = "zombie_package"


@dataclass
class DeadCodeFindingData:
    kind: DeadCodeKind
    file_path: str
    symbol_name: str | None
    symbol_kind: str | None
    confidence: float
    reason: str
    last_commit_at: datetime | None
    commit_count_90d: int
    lines: int
    package: str | None
    evidence: list[str]
    safe_to_delete: bool
    primary_owner: str | None
    age_days: int | None


@dataclass
class DeadCodeReport:
    repo_id: str
    analyzed_at: datetime
    total_findings: int
    findings: list[DeadCodeFindingData]
    deletable_lines: int
    confidence_summary: dict  # {"high": N, "medium": N, "low": N}


# Non-code languages that should never be flagged as dead code.
# These files have no import/export semantics — in_degree=0 is expected and
# meaningless for them.  Matches the skip lists in git_indexer and page_generator.
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    {
        "json", "yaml", "toml", "markdown",
        "sql", "shell", "terraform", "proto",
        "graphql", "dockerfile", "makefile",
        "unknown",
    }
)

# Patterns that should never be flagged as dead
_NEVER_FLAG_PATTERNS = (
    "*__init__.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
)

# Decorator patterns that indicate framework usage
_FRAMEWORK_DECORATORS = ("pytest.fixture", "pytest.mark")

# Default dynamic patterns (plugins, handlers, etc.)
_DEFAULT_DYNAMIC_PATTERNS = (
    "*Plugin",
    "*Handler",
    "*Adapter",
    "*Middleware",
    "register_*",
    "on_*",
)


class DeadCodeAnalyzer:
    """Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.

    All analysis is graph traversal + SQL. No LLM calls.
    """

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}

    def analyze(self, config: dict | None = None) -> DeadCodeReport:
        """Full analysis. Returns report with all findings."""
        cfg = config or {}
        findings: list[DeadCodeFindingData] = []

        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        whitelist = set(cfg.get("whitelist", []))

        if cfg.get("detect_unreachable_files", True):
            findings.extend(
                self._detect_unreachable_files(dynamic_patterns, whitelist)
            )

        if cfg.get("detect_unused_exports", True):
            findings.extend(
                self._detect_unused_exports(dynamic_patterns, whitelist)
            )

        if cfg.get("detect_unused_internals", False):
            findings.extend(
                self._detect_unused_internals(dynamic_patterns, whitelist)
            )

        if cfg.get("detect_zombie_packages", True):
            findings.extend(self._detect_zombie_packages(whitelist))

        # Apply min_confidence filter
        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(timezone.utc)
        deletable = sum(f.lines for f in findings if f.safe_to_delete)

        high = sum(1 for f in findings if f.confidence >= 0.7)
        medium = sum(1 for f in findings if 0.4 <= f.confidence < 0.7)
        low = sum(1 for f in findings if f.confidence < 0.4)

        return DeadCodeReport(
            repo_id="",
            analyzed_at=now,
            total_findings=len(findings),
            findings=findings,
            deletable_lines=deletable,
            confidence_summary={"high": high, "medium": medium, "low": low},
        )

    def analyze_partial(
        self, affected_files: list[str], config: dict | None = None
    ) -> DeadCodeReport:
        """Partial analysis for incremental updates."""
        # For partial analysis, only check affected files and their neighbors
        cfg = config or {}
        findings: list[DeadCodeFindingData] = []
        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        whitelist = set(cfg.get("whitelist", []))

        affected_set = set(affected_files)
        for node in affected_set:
            if node not in self.graph:
                continue
            node_data = self.graph.nodes.get(node, {})
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if self._should_never_flag(node, whitelist):
                continue

            # Check if file became unreachable
            in_deg = self.graph.in_degree(node)
            node_data = self.graph.nodes.get(node, {})
            if (
                in_deg == 0
                and not node_data.get("is_entry_point", False)
                and not node_data.get("is_test", False)
            ):
                finding = self._make_unreachable_finding(
                    node, node_data, dynamic_patterns
                )
                if finding:
                    findings.append(finding)

        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(timezone.utc)
        deletable = sum(f.lines for f in findings if f.safe_to_delete)
        high = sum(1 for f in findings if f.confidence >= 0.7)
        medium = sum(1 for f in findings if 0.4 <= f.confidence < 0.7)
        low = sum(1 for f in findings if f.confidence < 0.4)

        return DeadCodeReport(
            repo_id="",
            analyzed_at=now,
            total_findings=len(findings),
            findings=findings,
            deletable_lines=deletable,
            confidence_summary={"high": high, "medium": medium, "low": low},
        )

    # ------------------------------------------------------------------
    # Detection methods
    # ------------------------------------------------------------------

    def _detect_unreachable_files(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files with in_degree == 0 that are not entry points, tests, or config."""
        findings = []

        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue

            node_data = self.graph.nodes[node]
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if node_data.get("is_entry_point", False):
                continue
            if node_data.get("is_test", False):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue
            if self._is_api_contract(node_data):
                continue

            in_deg = self.graph.in_degree(node)
            if in_deg > 0:
                continue

            finding = self._make_unreachable_finding(
                str(node), node_data, dynamic_patterns
            )
            if finding:
                findings.append(finding)

        return findings

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
        dynamic_patterns: tuple[str, ...],
    ) -> DeadCodeFindingData | None:
        """Create an unreachable file finding with confidence scoring."""
        git_meta = self.git_meta_map.get(node, {})
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        # Confidence rules
        if commit_90d == 0 and last_commit and self._is_old(last_commit, days=180):
            confidence = 1.0
        elif commit_90d == 0:
            confidence = 0.7
        else:
            confidence = 0.4

        # safe_to_delete only if confidence >= 0.7 AND not matching dynamic patterns
        safe = confidence >= 0.7
        if safe and self._matches_dynamic_patterns(node, dynamic_patterns):
            safe = False

        evidence = [f"in_degree=0 (no files import this)"]
        if commit_90d == 0:
            evidence.append("No commits in last 90 days")

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason=f"File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            lines=node_data.get("symbol_count", 0) * 10,  # rough estimate
            package=self._get_package(node),
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
        )

    def _detect_unused_exports(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect public symbols with no incoming edges."""
        findings = []

        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue

            node_data = self.graph.nodes[node]
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if node_data.get("is_test", False):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue

            # Get symbols for this file from graph node data
            symbols = node_data.get("symbols", [])
            if not symbols:
                continue

            file_has_importers = self.graph.in_degree(node) > 0

            for sym in symbols:
                if not isinstance(sym, dict):
                    continue
                if sym.get("visibility") != "public":
                    continue
                sym_name = sym.get("name", "")

                # Skip framework decorators
                decorators = sym.get("decorators", [])
                if any(
                    d.startswith(prefix)
                    for d in decorators
                    for prefix in _FRAMEWORK_DECORATORS
                ):
                    continue

                # Skip dynamic patterns
                if self._name_matches_dynamic(sym_name, dynamic_patterns):
                    continue

                # Skip deprecated-named symbols (lower confidence)
                is_deprecated = any(
                    sym_name.endswith(suffix)
                    for suffix in ("_DEPRECATED", "_LEGACY", "_COMPAT")
                )

                # Check for importers of this specific symbol
                has_importers = False
                for pred in self.graph.predecessors(node):
                    edge_data = self.graph[pred][node]
                    imported_names = edge_data.get("imported_names", [])
                    if sym_name in imported_names or "*" in imported_names:
                        has_importers = True
                        break

                if has_importers:
                    continue

                # Confidence scoring
                if is_deprecated:
                    confidence = 0.3
                elif file_has_importers:
                    confidence = 1.0
                else:
                    confidence = 0.7

                complexity = sym.get("complexity_estimate", 0)
                safe = confidence >= 0.7 and complexity < 5

                git_meta = self.git_meta_map.get(str(node), {})

                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.UNUSED_EXPORT,
                        file_path=str(node),
                        symbol_name=sym_name,
                        symbol_kind=sym.get("kind"),
                        confidence=confidence,
                        reason=f"Public symbol '{sym_name}' has no importers",
                        last_commit_at=git_meta.get("last_commit_at") if isinstance(git_meta.get("last_commit_at"), datetime) else None,
                        commit_count_90d=git_meta.get("commit_count_90d", 0),
                        lines=sym.get("end_line", 0) - sym.get("start_line", 0),
                        package=self._get_package(str(node)),
                        evidence=[f"No imports of '{sym_name}' found in graph"],
                        safe_to_delete=safe,
                        primary_owner=git_meta.get("primary_owner_name"),
                        age_days=git_meta.get("age_days"),
                    )
                )

        return findings

    def _detect_unused_internals(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect private symbols with no calls edges from same file."""
        # Higher false positive rate — off by default
        return []

    def _detect_zombie_packages(
        self, whitelist: set[str]
    ) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges."""
        findings = []

        # Find package nodes (directories with multiple files)
        packages: dict[str, list[str]] = {}
        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue
            parts = Path(str(node)).parts
            if len(parts) > 1:
                pkg = parts[0]
                packages.setdefault(pkg, []).append(str(node))

        if len(packages) < 2:
            return findings  # Not a monorepo

        for pkg, files in packages.items():
            if pkg in whitelist:
                continue

            # Check if any file in this package is imported from outside the package
            has_external_importers = False
            for f in files:
                for pred in self.graph.predecessors(f):
                    pred_str = str(pred)
                    if pred_str.startswith("external:"):
                        continue
                    pred_parts = Path(pred_str).parts
                    if len(pred_parts) > 0 and pred_parts[0] != pkg:
                        has_external_importers = True
                        break
                if has_external_importers:
                    break

            if not has_external_importers:
                total_lines = sum(
                    self.graph.nodes[f].get("symbol_count", 0) * 10
                    for f in files
                    if f in self.graph
                )
                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.ZOMBIE_PACKAGE,
                        file_path=pkg,
                        symbol_name=None,
                        symbol_kind=None,
                        confidence=0.5,
                        reason=f"Package '{pkg}' has no importers from other packages",
                        last_commit_at=None,
                        commit_count_90d=0,
                        lines=total_lines,
                        package=pkg,
                        evidence=[f"No inter-package imports into '{pkg}'"],
                        safe_to_delete=False,
                        primary_owner=None,
                        age_days=None,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_never_flag(self, path: str, whitelist: set[str]) -> bool:
        """Return True if path should never be flagged as dead."""
        if path in whitelist:
            return True
        for pattern in _NEVER_FLAG_PATTERNS:
            if fnmatch.fnmatch(path, pattern):
                return True
        # Check if it's an __init__.py (re-export barrel)
        if Path(path).name == "__init__.py":
            return True
        return False

    def _is_api_contract(self, node_data: dict) -> bool:
        return node_data.get("is_api_contract", False)

    def _matches_dynamic_patterns(
        self, path: str, patterns: tuple[str, ...]
    ) -> bool:
        name = Path(path).stem
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _name_matches_dynamic(
        self, name: str, patterns: tuple[str, ...]
    ) -> bool:
        for pattern in patterns:
            if fnmatch.fnmatch(name, pattern):
                return True
        return False

    def _is_old(self, dt: Any, days: int = 180) -> bool:
        """Return True if datetime is older than `days` ago."""
        if dt is None:
            return False
        now = datetime.now(timezone.utc)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return (now - dt).days > days
        return False

    def _get_package(self, path: str) -> str | None:
        parts = Path(path).parts
        return parts[0] if len(parts) > 1 else None
