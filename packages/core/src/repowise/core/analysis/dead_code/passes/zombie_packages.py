"""The zombie-package pass: top-level packages nothing outside them depends on."""

from __future__ import annotations

import contextlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ....code_origin import is_build_file
from ....entry_candidacy import is_reachability_root
from ....ingestion.package_roots import package_manifest_names
from ..constants import (
    _DEAD_CODE_EXEMPT_LANGUAGES,
    _NEVER_PACKAGE_DIRS,
    _PROJECT_FILE_SUFFIXES,
    _RUN_NOT_IMPORTED_LANGUAGES,
)
from ..entry_shape import defines_main_function, is_program
from ..file_reachability import _NON_DEPENDENCY_EDGES
from ..finding_helpers import _LINES_UNKNOWN, _is_synthetic_node
from ..jvm_reachability import defines_main
from ..models import DeadCodeFindingData, DeadCodeKind
from ..risk_factors import path_risk_factors

if TYPE_CHECKING:
    from ..analyzer import DeadCodeAnalyzer


class ZombiePackagePass:
    """Groups files by top-level directory and judges each package."""

    def __init__(self, ctx: DeadCodeAnalyzer) -> None:
        self.ctx = ctx
        self.graph = ctx.graph

    def run(self, whitelist: set[str]) -> list[DeadCodeFindingData]:
        """Detect monorepo packages with no incoming inter_package edges.

        A ``framework:`` anchor counts as a cross-package importer (framework
        wiring is a real dependency); an ``external:`` third-party one does not.
        """
        findings = []

        packages: dict[str, list[str]] = {}
        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue
            parts = Path(str(node)).parts
            if len(parts) > 1:
                pkg = parts[0]
                packages.setdefault(pkg, []).append(str(node))

        if len(packages) < 2:
            return findings

        for pkg, files in packages.items():
            if not self._is_package_candidate(pkg, files, whitelist):
                continue
            if self._has_cross_package_importer(pkg, files):
                continue

            # A package total is only as known as its least-known file.
            counts = [
                self.ctx._file_line_count(f)
                for f in files
                if self.graph.nodes[f].get("node_type") != "symbol"
            ]
            known = [c for c in counts if c is not None]
            total_lines = sum(known) if len(known) == len(counts) else None
            evidence = [f"No inter-package imports into '{pkg}'"]
            if total_lines is None:
                evidence.append(_LINES_UNKNOWN)
            activity = self._package_git_activity(files)
            findings.append(
                DeadCodeFindingData(
                    kind=DeadCodeKind.ZOMBIE_PACKAGE,
                    file_path=pkg,
                    symbol_name=None,
                    symbol_kind=None,
                    confidence=0.5,
                    reason=f"Package '{pkg}' has no importers from other packages",
                    lines=total_lines,
                    evidence=evidence,
                    safe_to_delete=False,
                    risk_factors=list(path_risk_factors(pkg)),
                    **activity,
                )
            )

        return findings

    def _is_package_candidate(self, pkg: str, files: list[str], whitelist: set[str]) -> bool:
        """Whether a top-level directory is a code package the zombie pass may judge."""
        if pkg in whitelist:
            return False
        # Skip known non-package dirs (.github, .vscode, docs, ...)
        # and any other dotfile directory at the repo root.
        if pkg in _NEVER_PACKAGE_DIRS or pkg.startswith("."):
            return False
        # A folder is a package only when it declares itself one: a Next.js
        # ``app/``, a ``benchmarks/`` folder or a Go ``cmd/`` has no manifest
        # and nothing is meant to import it.
        if not self._has_package_manifest(pkg, files):
            return False
        # A package that ships a program (shebang CLI, JVM ``main``, a root
        # with a top-level ``main``) is run, never imported; one such file is
        # enough. A root alone is not (a Rust ``lib.rs`` is the library surface).
        # Test files, build-run source sets and symbol nodes are left out first.
        files = [
            f
            for f in files
            if (nd := self.graph.nodes.get(f, {})).get("node_type") != "symbol"
            and not nd.get("is_test")
            and not nd.get("is_never_flag")
        ]
        if any(
            is_program(f, self.ctx._source_map.get(f, b""))
            or defines_main(self.graph, f)
            or (
                is_reachability_root(self.graph.nodes[f])
                and defines_main_function(self.graph, f)
            )
            for f in files
        ):
            return False
        # A real package contains at least one source file something could
        # import. Config and data (YAML, JSON, MD, TOML) is metadata, and a
        # folder of Dockerfiles, shell scripts and build files is run, never
        # imported.
        return any(
            self.graph.nodes.get(f, {}).get("language", "unknown")
            not in _DEAD_CODE_EXEMPT_LANGUAGES | _RUN_NOT_IMPORTED_LANGUAGES
            and not is_build_file(f)
            for f in files
        )

    def _has_package_manifest(self, pkg: str, files: list[str]) -> bool:
        """Whether *pkg* holds a package manifest of its own, on disk or indexed.

        A top-level folder whose manifest sits at the repository root (a
        Python package built by the root ``pyproject.toml``) is the repository
        itself, not a package of it, so it is never a candidate.
        """
        names = {f.partition("/")[2] for f in files if f.count("/") == 1}
        if self.ctx._repo_root is not None:
            with contextlib.suppress(OSError):
                names.update(os.listdir(Path(self.ctx._repo_root) / pkg))
        manifests = package_manifest_names()
        return any(n in manifests or n.endswith(_PROJECT_FILE_SUFFIXES) for n in names)

    def _has_cross_package_importer(self, pkg: str, files: list[str]) -> bool:
        """Whether anything outside *pkg* depends on a file inside it."""
        for f in files:
            for pred, _f, edge_data in self.graph.in_edges(f, data=True):
                # Committed together is history, not a dependency.
                if edge_data.get("edge_type") in _NON_DEPENDENCY_EDGES:
                    continue
                pred_str = str(pred)
                if pred_str.startswith("external:"):
                    # Third-party imports don't count as cross-package
                    # importers; framework: synthetic anchors do.
                    continue
                pred_parts = Path(pred_str).parts
                if len(pred_parts) > 0 and pred_parts[0] != pkg:
                    return True
        return False

    def _package_git_activity(self, files: list[str]) -> dict[str, Any]:
        """Latest commit, 90-day commits, most common owner and age across *files*."""
        pkg_last_commit: datetime | None = None
        pkg_total_commits_90d = 0
        pkg_owner: str | None = None
        owner_counts: dict[str, int] = {}
        for f in files:
            # git_meta_map values are plain dicts: read them with .get().
            gm = self.ctx.git_meta_map.get(f)
            if gm is None:
                continue
            f_last = gm.get("last_commit_at")
            if f_last and (pkg_last_commit is None or f_last > pkg_last_commit):
                pkg_last_commit = f_last
            pkg_total_commits_90d += gm.get("commit_count_90d", 0) or 0
            f_owner = gm.get("primary_owner_name")
            if f_owner:
                owner_counts[f_owner] = owner_counts.get(f_owner, 0) + 1
        if owner_counts:
            pkg_owner = max(owner_counts, key=lambda k: owner_counts[k])
        pkg_age_days: int | None = None
        if pkg_last_commit:
            pkg_age_days = (datetime.now(UTC) - pkg_last_commit).days
        return {
            "last_commit_at": pkg_last_commit,
            "commit_count_90d": pkg_total_commits_90d,
            "primary_owner": pkg_owner,
            "age_days": pkg_age_days,
        }
