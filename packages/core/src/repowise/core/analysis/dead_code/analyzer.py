"""DeadCodeAnalyzer — pure graph + git-metadata dead-code detection.

All analysis is graph traversal + SQL. No LLM calls. Must complete in
< 10 seconds.

The four detection passes (unreachable files, unused exports, unused
internals, zombie packages) live as methods on this class. Constants,
data models, and dynamic-import markers live in sibling modules under
this package.
"""

from __future__ import annotations

import fnmatch
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog

from .constants import (
    _DEFAULT_DYNAMIC_PATTERNS,
    _FRAMEWORK_DECORATORS,
    _NEVER_FLAG_PATTERNS,
    _NON_CODE_LANGUAGES,
    _is_fixture_path,
)
from .dynamic_markers import find_dynamic_import_files
from .models import DeadCodeFindingData, DeadCodeKind, DeadCodeReport

logger = structlog.get_logger(__name__)


class DeadCodeAnalyzer:
    """Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.
    """

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
        parsed_files: dict | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self._dynamic_import_files = find_dynamic_import_files(parsed_files or {})

    def analyze(self, config: dict | None = None) -> DeadCodeReport:
        """Full analysis. Returns report with all findings."""
        cfg = config or {}
        findings: list[DeadCodeFindingData] = []

        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        whitelist = set(cfg.get("whitelist", []))

        if cfg.get("detect_unreachable_files", True):
            findings.extend(self._detect_unreachable_files(dynamic_patterns, whitelist))

        if cfg.get("detect_unused_exports", True):
            findings.extend(self._detect_unused_exports(dynamic_patterns, whitelist))

        if cfg.get("detect_unused_internals", False):
            findings.extend(self._detect_unused_internals(dynamic_patterns, whitelist))

        if cfg.get("detect_zombie_packages", True):
            findings.extend(self._detect_zombie_packages(whitelist))

        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(UTC)
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

            in_deg = self.graph.in_degree(node)
            node_data = self.graph.nodes.get(node, {})
            if (
                in_deg == 0
                and not node_data.get("is_entry_point", False)
                and not node_data.get("is_test", False)
            ):
                finding = self._make_unreachable_finding(node, node_data, dynamic_patterns)
                if finding:
                    findings.append(finding)

        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        now = datetime.now(UTC)
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
            if _is_fixture_path(str(node)):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue
            if self._is_api_contract(node_data):
                continue

            in_deg = self.graph.in_degree(node)
            if in_deg > 0:
                continue

            finding = self._make_unreachable_finding(str(node), node_data, dynamic_patterns)
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

        # _is_old uses strict >, so pass days-1 to get >= semantics.
        if commit_90d == 0 and last_commit and self._is_old(last_commit, days=364):
            confidence = 1.0  # Untouched for a year+ — very likely dead
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=179):
            confidence = 0.9
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=89):
            confidence = 0.8
        elif commit_90d == 0 and age_days is not None and age_days < 30:
            confidence = 0.55  # Recently created — may be WIP
        elif commit_90d == 0:
            confidence = 0.7
        else:
            confidence = 0.4

        # Reduce confidence when dynamic imports exist in the same package.
        if self._dynamic_import_files:
            node_pkg = str(Path(node).parent)
            for dif in self._dynamic_import_files:
                if str(Path(dif).parent) == node_pkg:
                    confidence = min(confidence, 0.4)
                    break

        safe = confidence >= 0.7
        if safe and self._matches_dynamic_patterns(node, dynamic_patterns):
            safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self._dynamic_import_files and confidence <= 0.4:
            evidence.append("Package uses dynamic imports (importlib/__import__)")

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
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
            if _is_fixture_path(str(node)):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue

            symbols = [
                self.graph.nodes[succ]
                for succ in self.graph.successors(node)
                if self.graph.nodes[succ].get("node_type") == "symbol"
                and self.graph.get_edge_data(node, succ, {}).get("edge_type") == "defines"
            ]
            if not symbols:
                continue

            file_has_importers = self.graph.in_degree(node) > 0

            for sym in symbols:
                if sym.get("visibility") != "public":
                    continue
                sym_name = sym.get("name", "")

                decorators = sym.get("decorators", [])
                if any(
                    d.startswith(prefix) for d in decorators for prefix in _FRAMEWORK_DECORATORS
                ):
                    continue

                if self._name_matches_dynamic(sym_name, dynamic_patterns):
                    continue

                is_deprecated = any(
                    sym_name.endswith(suffix) for suffix in ("_DEPRECATED", "_LEGACY", "_COMPAT")
                )

                has_importers = False
                for pred in self.graph.predecessors(node):
                    edge_data = self.graph[pred][node]
                    imported_names = edge_data.get("imported_names", [])
                    if sym_name in imported_names or "*" in imported_names:
                        has_importers = True
                        break

                if has_importers:
                    continue

                if is_deprecated:
                    confidence = 0.3
                elif file_has_importers:
                    confidence = 1.0
                else:
                    confidence = 0.7

                safe = confidence >= 0.7

                git_meta = self.git_meta_map.get(str(node), {})

                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.UNUSED_EXPORT,
                        file_path=str(node),
                        symbol_name=sym_name,
                        symbol_kind=sym.get("kind"),
                        confidence=confidence,
                        reason=f"Public symbol '{sym_name}' has no importers",
                        last_commit_at=git_meta.get("last_commit_at")
                        if isinstance(git_meta.get("last_commit_at"), datetime)
                        else None,
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
        """Detect private/internal symbols with zero incoming call edges.

        Off by default (higher false-positive rate). Enable with
        ``detect_unused_internals=True`` in the config dict.
        """
        findings: list[DeadCodeFindingData] = []

        for node, node_data in self.graph.nodes(data=True):
            if node_data.get("node_type") != "symbol":
                continue
            if node_data.get("visibility") not in ("private", "internal"):
                continue
            file_path = node_data.get("file_path", "")
            if not file_path:
                continue
            file_data = self.graph.nodes.get(file_path, {})
            if file_data.get("is_test", False):
                continue
            if _is_fixture_path(file_path):
                continue
            if self._should_never_flag(file_path, whitelist):
                continue

            sym_name = node_data.get("name", "")
            if sym_name.startswith("__") and sym_name.endswith("__"):
                continue
            if self._name_matches_dynamic(sym_name, dynamic_patterns):
                continue

            has_callers = any(
                self.graph.get_edge_data(pred, node, {}).get("edge_type") == "calls"
                for pred in self.graph.predecessors(node)
            )
            if has_callers:
                continue

            git_meta = self.git_meta_map.get(file_path, {})
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.UNUSED_INTERNAL,
                    file_path=file_path,
                    symbol_name=sym_name,
                    symbol_kind=node_data.get("kind"),
                    confidence=0.65,
                    reason=f"Private symbol '{sym_name}' has no callers",
                    last_commit_at=git_meta.get("last_commit_at")
                    if isinstance(git_meta.get("last_commit_at"), datetime)
                    else None,
                    commit_count_90d=git_meta.get("commit_count_90d", 0),
                    lines=node_data.get("end_line", 0) - node_data.get("start_line", 0),
                    package=self._get_package(file_path),
                    evidence=[f"No CALL edges to '{sym_name}'"],
                    safe_to_delete=False,
                    primary_owner=git_meta.get("primary_owner_name"),
                    age_days=git_meta.get("age_days"),
                )
            )

        return findings

    def _detect_zombie_packages(self, whitelist: set[str]) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges."""
        findings = []

        packages: dict[str, list[str]] = {}
        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue
            parts = Path(str(node)).parts
            if len(parts) > 1:
                pkg = parts[0]
                packages.setdefault(pkg, []).append(str(node))

        if len(packages) < 2:
            return findings

        for pkg, files in packages.items():
            if pkg in whitelist:
                continue

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
                pkg_last_commit: datetime | None = None
                pkg_total_commits_90d = 0
                pkg_owner: str | None = None
                owner_counts: dict[str, int] = {}
                for f in files:
                    gm = self.git_meta_map.get(f)
                    if gm is None:
                        continue
                    f_last = getattr(gm, "last_commit_at", None)
                    if f_last and (pkg_last_commit is None or f_last > pkg_last_commit):
                        pkg_last_commit = f_last
                    pkg_total_commits_90d += getattr(gm, "commit_count_90d", 0) or 0
                    f_owner = getattr(gm, "primary_owner_name", None)
                    if f_owner:
                        owner_counts[f_owner] = owner_counts.get(f_owner, 0) + 1
                if owner_counts:
                    pkg_owner = max(owner_counts, key=lambda k: owner_counts[k])
                pkg_age_days: int | None = None
                if pkg_last_commit:
                    pkg_age_days = (datetime.now(UTC) - pkg_last_commit).days

                findings.append(
                    DeadCodeFindingData(
                        kind=DeadCodeKind.ZOMBIE_PACKAGE,
                        file_path=pkg,
                        symbol_name=None,
                        symbol_kind=None,
                        confidence=0.5,
                        reason=f"Package '{pkg}' has no importers from other packages",
                        last_commit_at=pkg_last_commit,
                        commit_count_90d=pkg_total_commits_90d,
                        lines=total_lines,
                        package=pkg,
                        evidence=[f"No inter-package imports into '{pkg}'"],
                        safe_to_delete=False,
                        primary_owner=pkg_owner,
                        age_days=pkg_age_days,
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
        # __init__.py is a re-export barrel
        return Path(path).name == "__init__.py"

    def _is_api_contract(self, node_data: dict) -> bool:
        return node_data.get("is_api_contract", False)

    def _matches_dynamic_patterns(self, path: str, patterns: tuple[str, ...]) -> bool:
        name = Path(path).stem
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def _name_matches_dynamic(self, name: str, patterns: tuple[str, ...]) -> bool:
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def _is_old(self, dt: Any, days: int = 180) -> bool:
        """Return True if datetime is older than `days` ago."""
        if dt is None:
            return False
        now = datetime.now(UTC)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (now - dt).days > days
        return False

    def _get_package(self, path: str) -> str | None:
        parts = Path(path).parts
        return parts[0] if len(parts) > 1 else None
