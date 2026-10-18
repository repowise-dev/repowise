"""The unreachable-file pass: files nothing can reach that are not tests, fixtures or config."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..constants import (
    _DEAD_CODE_EXEMPT_LANGUAGES,
    _NAMESPACE_IMPORT_LANGUAGES,
    _is_fixture_path,
)
from ..file_reachability import is_file_reachable
from ..finding_helpers import _LINES_UNKNOWN, _is_synthetic_node
from ..models import DeadCodeFindingData, DeadCodeKind
from ..risk_factors import (
    NO_GIT_SIGNAL_CONFIDENCE,
    RISK_CAP_CONFIDENCE,
    path_risk_factors,
    risk_evidence,
)

if TYPE_CHECKING:
    from ..analyzer import DeadCodeAnalyzer


#: ``(days, confidence)`` rungs for a file with no commit in 90 days, oldest
#: first. ``_is_old`` uses strict >, so days-1 gives >= semantics: a year or
#: more untouched is 1.0, very likely dead.
_GIT_AGE_RUNGS: tuple[tuple[int, float], ...] = ((364, 1.0), (179, 0.9), (89, 0.8))


class UnreachableFilePass:
    """Scores each file the shared reachability predicate cannot reach."""

    def __init__(self, ctx: DeadCodeAnalyzer) -> None:
        self.ctx = ctx
        self.graph = ctx.graph

    def run(
        self,
        whitelist: set[str],
    ) -> list[DeadCodeFindingData]:
        """Detect files nothing can reach that are not tests, fixtures, or config."""
        findings = []

        # The rescues do not vary by candidate: build them once per pass.
        rescues = self.ctx._reachability_rescues(whitelist)

        for node in self.graph.nodes():
            if _is_synthetic_node(str(node)):
                continue

            node_data = self.graph.nodes[node]
            # These skips are scoping, not reachability; every reachability
            # rule lives in :func:`is_file_reachable`, which the overview
            # assembler shares so the two cannot disagree.
            if node_data.get("language", "unknown") in _DEAD_CODE_EXEMPT_LANGUAGES:
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue

            if is_file_reachable(str(node), self.graph, rescues):
                continue

            finding = self._make_unreachable_finding(str(node), node_data)
            if finding:
                findings.append(finding)

        return findings

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
    ) -> DeadCodeFindingData | None:
        """Create an unreachable file finding with confidence scoring."""
        git_meta = self.ctx.git_meta_map.get(node)
        # No row at all is not "no commits in 90 days": that rung is for a
        # file git has data on. Scored below the deletion-ready threshold and
        # said so in the evidence, on every path that builds this map.
        no_git_signal = not git_meta
        git_meta = git_meta or {}
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        confidence = (
            NO_GIT_SIGNAL_CONFIDENCE
            if no_git_signal
            else self._git_age_confidence(commit_90d, last_commit, age_days)
        )

        # The git-age rungs are an evidence scale and stay literal; the caps
        # below are tier boundaries and read the constants.

        # Reduce confidence when dynamic imports exist in the same package.
        if self.ctx._dynamic_import_dirs and str(Path(node).parent) in self.ctx._dynamic_import_dirs:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # Runtime-load risk factors (config, bootstrap, scripts, assets) make
        # "in_degree=0" weak evidence: cap and say so in the evidence.
        risk_factors = path_risk_factors(node)
        if risk_factors:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # A namespace-imported file is reached without an edge (a same-namespace
        # ``new T()``), so no edge says little and its age says nothing more.
        namespace_imported = node_data.get("language") in _NAMESPACE_IMPORT_LANGUAGES
        if namespace_imported:
            confidence = min(confidence, RISK_CAP_CONFIDENCE)

        # A whole file is a review candidate, never deletion-ready: see
        # ``REVIEW_ONLY_KINDS``. Confidence still ranks it.
        safe = False

        evidence = ["in_degree=0 (no files import this)"]
        if no_git_signal:
            evidence.append("No git history recorded for this file")
        elif commit_90d == 0:
            evidence.append("No commits in last 90 days")
        if self.ctx._dynamic_import_files and confidence <= RISK_CAP_CONFIDENCE:
            evidence.append("Package uses dynamic imports or runtime-resolved edges")
        risk_line = risk_evidence(risk_factors)
        if risk_line:
            evidence.append(risk_line)
        if namespace_imported:
            evidence.append(
                "Imported by namespace, not by file, so having no importer is weak evidence"
            )
        lines = self.ctx._file_line_count(node)
        if lines is None:
            evidence.append(_LINES_UNKNOWN)

        return DeadCodeFindingData(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            symbol_kind=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            lines=lines,
            evidence=evidence,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
            risk_factors=list(risk_factors),
        )

    def _git_age_confidence(
        self, commit_90d: int, last_commit: Any, age_days: int | None
    ) -> float:
        """The git-age evidence ladder for a file no edge reaches."""
        if commit_90d != 0:
            return 0.4
        if last_commit:
            for days, confidence in _GIT_AGE_RUNGS:
                if self._is_old(last_commit, days=days):
                    return confidence
        if age_days is not None and age_days < 30:
            return 0.55  # Recently created: may be work in progress
        return 0.7

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
