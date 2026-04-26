"""Dead code detection for repowise.

Pure graph traversal + SQL — no LLM calls. Must complete in < 10 seconds.

Detects unreachable files, unused exports, unused internals, and
zombie packages using the dependency graph and git metadata.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import structlog

from repowise.core.ingestion.languages.registry import REGISTRY as _LANG_REGISTRY

logger = structlog.get_logger(__name__)


class DeadCodeKind(StrEnum):
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
# Derived from the centralised LanguageRegistry — passthrough config/infra
# languages plus "unknown".
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    spec.tag
    for spec in _LANG_REGISTRY.all_specs()
    if spec.is_passthrough and (not spec.is_code or spec.is_infra) and spec.tag != "openapi"
) | {"unknown"}

# Patterns that should never be flagged as dead
_NEVER_FLAG_PATTERNS = (
    "*__init__.py",
    "*__main__.py",
    "*conftest.py",
    "*alembic/env.py",
    "*manage.py",
    "*wsgi.py",
    "*asgi.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
    "*setup.py",
    "*setup.cfg",
    "*next.config.*",
    "*vite.config.*",
    "*tailwind.config.*",
    "*postcss.config.*",
    "*jest.config.*",
    "*vitest.config.*",
    # Next.js / Remix / SvelteKit framework route files — loaded by the
    # framework at runtime, never imported via module imports.
    "*/page.tsx",
    "*/page.ts",
    "*/page.jsx",
    "*/page.js",
    "*/layout.tsx",
    "*/layout.ts",
    "*/route.tsx",
    "*/route.ts",
    "*/loading.tsx",
    "*/error.tsx",
    "*/not-found.tsx",
    "*/template.tsx",
    "*/default.tsx",
    # Nuxt route pages
    "*/pages/*.vue",
)

# Decorator patterns that indicate framework usage (route handlers, fixtures, etc.)
_FRAMEWORK_DECORATORS = (
    "pytest.fixture",
    "pytest.mark",
    # Flask
    "app.route",
    "blueprint.route",
    "bp.route",
    # FastAPI
    "router.get",
    "router.post",
    "router.put",
    "router.delete",
    "router.patch",
    "app.get",
    "app.post",
    # Django
    "admin.register",
    "receiver",
)

# Default dynamic patterns (plugins, handlers, etc.)
_DEFAULT_DYNAMIC_PATTERNS = (
    "*Plugin",
    "*Handler",
    "*Adapter",
    "*Middleware",
    "*Mixin",
    "*Command",
    "register_*",
    "on_*",
    # Common route/view patterns
    "*_view",
    "*_endpoint",
    "*_route",
    "*_callback",
    "*_signal",
    "*_task",
)

# Path segments that indicate test fixture / sample data directories.
# Files under these directories are test data, not real code — they should
# never be flagged as dead even if nothing imports them.
_FIXTURE_PATH_SEGMENTS = (
    "fixture",
    "fixtures",
    "testdata",
    "test_data",
    "sample_repo",
    "mock_data",
    "test_assets",
)


def _is_fixture_path(path: str) -> bool:
    """Return True if path is under a test fixture / sample data directory."""
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False


class DeadCodeAnalyzer:
    """Detects unreachable files, unused exports, unused internals, and
    zombie packages using the dependency graph and git metadata.

    All analysis is graph traversal + SQL. No LLM calls.
    """

    # Patterns in source that indicate dynamic/runtime imports, keyed by suffix.
    _DYNAMIC_IMPORT_MARKERS: dict[str, tuple[str, ...]] = {
        ".py": (
            "importlib.import_module",
            "__import__(",
            "importlib.reload",
            "pkgutil.iter_modules",
        ),
        ".js": ("import(", "require(", "require.resolve("),
        ".mjs": ("import(", "require("),
        ".cjs": ("require(", "require.resolve("),
        ".ts": ("import(", "require("),
        ".tsx": ("import(", "require("),
        ".java": ("Class.forName(", "ServiceLoader.load("),
        ".kt": ("Class.forName(", "ServiceLoader.load("),
        ".rb": ("autoload ", "const_get(", "send(:require"),
        ".php": ("class_exists(", "interface_exists("),
        ".go": ("plugin.Open(", "reflect.New("),
        ".cs": (
            # Reflection-driven type loading
            "Type.GetType(",
            "Activator.CreateInstance(",
            "Assembly.Load(",
            "Assembly.LoadFrom(",
            "Assembly.LoadFile(",
            "GetExecutingAssembly().GetTypes(",
            # Cross-assembly visibility — types named in the friend assembly
            # may be used externally even with no static call site.
            "[assembly: InternalsVisibleTo",
            # Trim-safe reflection annotation
            "[DynamicDependency",
            # MEF / VS extensibility composition
            "[Export",
            "[ImportMany",
            # DI registration: types registered here have no static caller
            # but the framework instantiates them at runtime. Three forms.
            "AddScoped<",
            "AddSingleton<",
            "AddTransient<",
            "AddHostedService<",
        ),
    }

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
        parsed_files: dict | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}
        self._dynamic_import_files = self._find_dynamic_import_files(parsed_files or {})

    @classmethod
    def _find_dynamic_import_files(cls, parsed_files: dict) -> set[str]:
        """Return set of file paths that contain dynamic import calls.

        When a repo uses ``importlib.import_module``, ``import()``,
        ``Class.forName()``, etc., unreachable modules in the same package
        may be loaded at runtime.  We use this to lower confidence on those
        findings.
        """
        result: set[str] = set()
        for path, pf in parsed_files.items():
            try:
                abs_path = getattr(pf, "file_info", None)
                if abs_path is None:
                    continue
                src_path = Path(abs_path.abs_path)
                markers = cls._DYNAMIC_IMPORT_MARKERS.get(src_path.suffix)
                if not markers:
                    continue
                source = src_path.read_text(errors="ignore")
                if any(marker in source for marker in markers):
                    result.add(path)
            except Exception:
                continue
        return result

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

        # Apply min_confidence filter
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

        # Confidence rules — differentiate by age and activity.
        # _is_old uses strict >, so pass days-1 to get >= semantics.
        if commit_90d == 0 and last_commit and self._is_old(last_commit, days=364):
            confidence = 1.0  # Untouched for a year+ — very likely dead
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=179):
            confidence = 0.9  # Untouched for 6+ months
        elif commit_90d == 0 and last_commit and self._is_old(last_commit, days=89):
            confidence = 0.8  # Untouched for 3+ months
        elif commit_90d == 0 and age_days is not None and age_days < 30:
            confidence = 0.55  # Recently created but no imports — may be WIP
        elif commit_90d == 0:
            confidence = 0.7  # No recent activity, unknown age
        else:
            confidence = 0.4

        # Reduce confidence when dynamic imports exist in the same package —
        # importlib.import_module / __import__ may load this file at runtime.
        if self._dynamic_import_files:
            node_pkg = str(Path(node).parent)
            for dif in self._dynamic_import_files:
                if str(Path(dif).parent) == node_pkg:
                    confidence = min(confidence, 0.4)
                    break

        # safe_to_delete only if confidence >= 0.7 AND not matching dynamic patterns
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

            # Get symbols defined in this file via DEFINES edges to symbol nodes
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

                # Skip framework decorators (if stored on symbol node)
                decorators = sym.get("decorators", [])
                if any(
                    d.startswith(prefix) for d in decorators for prefix in _FRAMEWORK_DECORATORS
                ):
                    continue

                # Skip dynamic patterns
                if self._name_matches_dynamic(sym_name, dynamic_patterns):
                    continue

                # Skip deprecated-named symbols (lower confidence)
                is_deprecated = any(
                    sym_name.endswith(suffix) for suffix in ("_DEPRECATED", "_LEGACY", "_COMPAT")
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

        Off by default (higher false-positive rate).  Enable with
        ``detect_unused_internals=True`` in the config dict.
        """
        findings: list[DeadCodeFindingData] = []

        for node, node_data in self.graph.nodes(data=True):
            if node_data.get("node_type") != "symbol":
                continue
            if node_data.get("visibility") not in ("private", "internal"):
                continue
            # Skip test files and fixtures
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
            # Skip dunder methods and common patterns
            if sym_name.startswith("__") and sym_name.endswith("__"):
                continue
            if self._name_matches_dynamic(sym_name, dynamic_patterns):
                continue

            # Check for incoming CALL edges
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
                # Aggregate git metadata across package files for enrichment
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
        # Check if it's an __init__.py (re-export barrel)
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
