"""Orchestrate a base-versus-head health comparison.

The one entry point every surface calls. It owns the order of the work, the
honesty of the status, and the cache; it owns none of the analysis, matching,
or attribution policy, which live in the neighbouring modules.
"""

from __future__ import annotations

import hashlib
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass

from ..health import HEALTH_ANALYZER_VERSION, HealthFindingData
from ..health.perf.causal import PERFORMANCE_MODEL_VERSION
from .analyzer import RevisionHealthAnalyzer, language_for, language_of
from .attribution import FindingAttributor, changed_symbols_for
from .identity import change_finding_id, finding_key, severity_rank
from .matcher import FindingMatcher, MatchedFinding
from .models import (
    AnalysisFingerprint,
    ChangeFinding,
    ChangeHealthDelta,
    RevisionId,
    ScopeCounts,
)
from .perf_delta import PerfOpportunityView, index_by_finding, opportunities_for
from .sources import FileChange, GitRevisionSource, RevisionPair, RevisionSource

#: Changed files above this count are refused rather than analysed twice.
MAX_CHANGED_FILES = 300

#: Comparisons kept in the process cache.
_CACHE_CAPACITY = 32

#: How long a caller waits on an identical in-flight comparison before doing
#: the work itself. Short enough not to hold a pool thread for minutes.
_WAIT_TIMEOUT_SECONDS = 30

#: Wait-then-retry rounds before a caller stops queueing and computes.
_CLAIM_ATTEMPTS = 2

_DIMENSION_ORDER = {"defect": 0, "maintainability": 1, "performance": 2}


@dataclass(frozen=True, slots=True)
class DeltaRequest:
    repo_path: str
    revspec: str | None
    extensions: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()


class ChangeHealthDeltaService:
    """Compare the health of two revisions of the same repository."""

    def __init__(
        self,
        source: RevisionSource | None = None,
        *,
        repo_path: str | None = None,
        analyzer: RevisionHealthAnalyzer | None = None,
        rules_fingerprint: str = "",
    ) -> None:
        if source is None:
            if repo_path is None:
                raise ValueError("a RevisionSource or a repo_path is required")
            source = GitRevisionSource(repo_path)
        self.source = source
        self.repo_path = repo_path
        self.analyzer = analyzer or RevisionHealthAnalyzer()
        self.rules_fingerprint = rules_fingerprint
        self._cache: OrderedDict[str, ChangeHealthDelta] = OrderedDict()
        self._lock = threading.Lock()
        self._inflight: dict[str, threading.Event] = {}

    @property
    def fingerprint(self) -> AnalysisFingerprint:
        return AnalysisFingerprint(
            analyzer_version=HEALTH_ANALYZER_VERSION,
            rules_fingerprint=self.rules_fingerprint,
            performance_model_version=PERFORMANCE_MODEL_VERSION,
        )

    # -- entry point --------------------------------------------------------

    def compare(self, request: DeltaRequest) -> ChangeHealthDelta:
        """Compare *request*, serving an identical in-flight call one result.

        The revision is resolved once and threaded through, so neither the
        cache key nor a cache hit re-reads the change shape from the source.
        """
        try:
            pair = self.source.resolve(request.revspec)
        except ValueError as exc:
            return _unavailable("unsupported_range", str(exc))
        key = self._cache_key(request, pair)
        # One wait, then one retry at becoming the leader. A caller that still
        # finds work in flight does it rather than blocking a thread again.
        for _ in range(_CLAIM_ATTEMPTS):
            cached, waiter = self._claim(key)
            if cached is not None:
                return cached
            if waiter is None:
                try:
                    result = self._compare_uncached(request, pair)
                except BaseException:
                    self._release(key, None)
                    raise
                self._release(key, result)
                return result
            waiter.wait(timeout=_WAIT_TIMEOUT_SECONDS)
        return self._compare_uncached(request, pair)

    # -- cache --------------------------------------------------------------

    def _cache_key(self, request: DeltaRequest, pair: RevisionPair) -> str:
        """Identity of a comparison: the two sides plus how they were analysed."""
        if pair.working_tree:
            # Content identity: HEAD alone does not describe an edited tree.
            paths = sorted(p for p in pair.head_paths)
            blobs = self.source.read_working_tree(paths)
            digest = hashlib.sha256()
            for path in paths:
                digest.update(path.encode("utf-8"))
                digest.update(hashlib.sha256(blobs.get(path, b"")).digest())
            head_identity = f"wt:{digest.hexdigest()[:24]}"
        else:
            head_identity = pair.head_sha
        parts = (
            str(self.repo_path or ""),
            pair.base_sha,
            head_identity,
            str(HEALTH_ANALYZER_VERSION),
            self.rules_fingerprint,
            str(PERFORMANCE_MODEL_VERSION),
            ",".join(request.extensions),
            ",".join(request.exclude_patterns),
        )
        return hashlib.sha256("\x00".join(parts).encode("utf-8")).hexdigest()

    def _claim(self, key: str) -> tuple[ChangeHealthDelta | None, threading.Event | None]:
        with self._lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                hit.cache_hit = True
                return hit, None
            waiting = self._inflight.get(key)
            if waiting is not None:
                return None, waiting
            self._inflight[key] = threading.Event()
            return None, None

    def _release(self, key: str, result: ChangeHealthDelta | None) -> None:
        with self._lock:
            if result is not None:
                self._cache[key] = result
                while len(self._cache) > _CACHE_CAPACITY:
                    self._cache.popitem(last=False)
            event = self._inflight.pop(key, None)
        if event is not None:
            event.set()

    # -- comparison ---------------------------------------------------------

    def _compare_uncached(
        self, request: DeltaRequest, pair: RevisionPair
    ) -> ChangeHealthDelta:
        started = time.perf_counter()
        changes = _filter(pair.changes, request)
        scope = ScopeCounts(changed=len(changes))
        eligible = [c for c in changes if c.head_path and language_for(c.head_path)]
        scope.eligible = len(eligible)
        skipped = {
            c.head_path or c.base_path or "": _skip_reason(c) for c in changes if c not in eligible
        }
        skipped.pop("", None)

        base_id = RevisionId(pair.base_ref, pair.base_sha, "commit")
        head_id = RevisionId(
            pair.head_ref,
            pair.head_sha or "",
            "working_tree" if pair.working_tree else "commit",
        )
        basis = "both_sides_analyzed"

        if not eligible:
            delta = ChangeHealthDelta(
                # Nothing was compared either way, so neither case is a clean bill.
                status="unavailable",
                explanation=(
                    "No changed file is health-analyzable, so nothing was compared."
                    if changes
                    else "This change touches no files."
                ),
                base=base_id,
                head=head_id,
                comparison_basis=basis,
                fingerprint=self.fingerprint,
                scope=scope,
                skipped=skipped,
            )
            delta.timing_ms = (time.perf_counter() - started) * 1000
            return delta

        if len(eligible) > MAX_CHANGED_FILES:
            return _unavailable(
                "too_large",
                f"{len(eligible)} changed files exceeds the {MAX_CHANGED_FILES}-file "
                "ceiling for a two-sided analysis.",
                base=base_id,
                head=head_id,
                scope=scope,
                fingerprint=self.fingerprint,
            )

        head_paths = [c.head_path for c in eligible if c.head_path]
        base_paths = [c.base_path for c in eligible if c.base_path]
        head_sources = (
            self.source.read_working_tree(head_paths)
            if pair.working_tree
            else self.source.read(pair.head_sha, head_paths)
        )
        base_sources = self.source.read(pair.base_sha, base_paths)

        head_run = self.analyzer.analyze(head_sources, subject_paths=set(head_paths))
        base_run = self.analyzer.analyze(base_sources, subject_paths=set(base_paths))

        skipped.update(head_run.skipped)
        # A file whose BASE side failed has no baseline, so every finding at its
        # head would read as introduced. Drop it from the subject and say so:
        # silently keeping it is how a partial run fabricates new findings.
        for change in eligible:
            head_path, base_path = change.head_path, change.base_path
            if head_path is None or base_path is None or head_path in skipped:
                continue
            reason = base_run.skipped.get(base_path)
            if reason is not None:
                skipped[head_path] = f"base_{reason}"
        subject = {p for p in head_run.analyzed if p in set(head_paths) and p not in skipped}

        scope.analyzed = len(subject)
        scope.skipped = len(skipped)
        scope.failed = sum(
            1 for r in skipped.values() if r.removeprefix("base_") in {"parse_failed", "unreadable"}
        )

        rename = pair.rename_map()
        matcher = FindingMatcher(rename)
        # Both sides are restricted to the same comparable set, so an excluded
        # file cannot contribute a one-sided "introduced" or "resolved".
        base_findings = [
            f for f in base_run.findings if rename.get(f.file_path, f.file_path) in subject
        ]
        match = matcher.match(base_findings, head_run.findings_for(subject))

        by_file: dict[str, list[HealthFindingData]] = {}
        for finding in head_run.findings_for(subject):
            by_file.setdefault(finding.file_path, []).append(finding)
        change_map = {c.head_path: c for c in eligible if c.head_path}
        attributor = FindingAttributor(change_map)
        changed_symbols = changed_symbols_for(change_map, by_file)

        surfaced = match.of_kind("introduced", "worsened")
        perf_views = opportunities_for([m.head for m in surfaced])
        perf_index = index_by_finding(perf_views, [m.head for m in surfaced])

        comparison = f"{pair.base_sha}:{pair.head_sha or 'worktree'}"
        findings = [
            self._to_change_finding(
                m, attributor, changed_symbols, perf_index.get(id(m.head)), comparison
            )
            for m in surfaced
        ]
        findings.sort(key=_priority)

        status, explanation = _status_for(scope, skipped, findings)
        delta = ChangeHealthDelta(
            status=status,
            explanation=explanation,
            base=base_id,
            head=head_id,
            comparison_basis=basis,
            fingerprint=self.fingerprint,
            scope=scope,
            findings=findings,
            resolved_total=len(match.resolved),
            unchanged_total=match.unchanged_total,
            skipped=skipped,
            limits=_limits(),
        )
        delta.timing_ms = (time.perf_counter() - started) * 1000
        return delta

    def _to_change_finding(
        self,
        matched: MatchedFinding,
        attributor: FindingAttributor,
        changed_symbols: set[tuple[str, str]],
        perf: PerfOpportunityView | None,
        comparison: str,
    ) -> ChangeFinding:
        head = matched.head
        edge = perf.intervention_symbol if perf and perf.is_cross_function else None
        attribution = attributor.attribute(
            head, changed_symbols=changed_symbols, changed_call_edge=edge
        )
        key = finding_key(head)
        return ChangeFinding(
            change_finding_id=change_finding_id(key, matched.ordinal, comparison=comparison),
            change_kind=matched.kind,
            dimension=str(head.dimension),
            biomarker_type=str(head.biomarker_type),
            severity=str(head.severity),
            path=head.file_path,
            symbol=head.function_name,
            line_start=head.line_start,
            line_end=head.line_end,
            reason=head.reason or str(head.biomarker_type).replace("_", " "),
            attribution_basis=attribution.basis,
            attribution_confidence=attribution.confidence,
            attribution_detail=attribution.detail,
            suggestion=_suggestion(head, perf),
            follow_up="",  # the surface owns its own call syntax
            severity_before=matched.severity_before,
            health_impact=float(head.health_impact or 0.0),
            opportunity_id=perf.opportunity_id if perf else None,
            opportunity_rank=perf.rank_score if perf else None,
            evidence=_evidence(head, perf),
        )


# -- helpers ----------------------------------------------------------------


def _filter(changes: list[FileChange], request: DeltaRequest) -> list[FileChange]:
    """Apply the caller's extension and exclusion filters to the change set."""
    import pathspec

    spec = (
        pathspec.PathSpec.from_lines("gitwildmatch", request.exclude_patterns)
        if request.exclude_patterns
        else None
    )
    exts = {e if e.startswith(".") else f".{e}" for e in request.extensions}
    out = []
    for change in changes:
        path = change.head_path or change.base_path or ""
        if not path:
            continue
        if spec is not None and spec.match_file(path):
            continue
        if exts and not any(path.endswith(e) for e in exts):
            continue
        out.append(change)
    return out


def _skip_reason(change: FileChange) -> str:
    """Why a changed path never reached the analyzer."""
    if change.head_path is None:
        return "deleted"
    if language_of(change.head_path) is None:
        return "unsupported_language"
    return "not_health_analyzable"


def _status_for(
    scope: ScopeCounts, skipped: dict[str, str], findings: list[ChangeFinding]
) -> tuple[str, str]:
    if scope.analyzed == 0:
        return "unavailable", (
            f"None of the {scope.changed} changed "
            f"{'file' if scope.changed == 1 else 'files'} could be analysed."
        )
    if skipped or scope.failed:
        return (
            "partial",
            f"Compared {scope.analyzed} of {scope.changed} changed files; "
            f"{len(skipped)} were not analysed.",
        )
    if not findings:
        return (
            "available",
            "No supported new findings surfaced in the analyzed scope.",
        )
    return "available", f"Compared {scope.analyzed} changed files on both sides."


def _limits() -> list[str]:
    return [
        "Both sides are analysed over the changed files only, so cross-file "
        "detectors (duplication, cross-function performance) see no wider context."
    ]


def _priority(finding: ChangeFinding) -> tuple:
    """Severity first, then dimension, then reach — never defect impact for perf."""
    if finding.dimension == "performance":
        return (
            0,
            -severity_rank(finding.severity),
            -(finding.opportunity_rank or 0),
            _DIMENSION_ORDER["performance"],
            finding.path,
        )
    return (
        0,
        -severity_rank(finding.severity),
        -int((finding.health_impact or 0.0) * 1000),
        _DIMENSION_ORDER.get(finding.dimension, 9),
        finding.path,
    )


def _suggestion(finding: HealthFindingData, perf: PerfOpportunityView | None) -> str:
    if perf is not None:
        if perf.actionability_state == "plan_ready" and perf.intervention_symbol:
            return f"Hoist or batch the repeated call in {perf.intervention_symbol}."
        return perf.actionability_reason or "Confirm the cost before changing it."
    return finding.reason or f"Review the {finding.biomarker_type.replace('_', ' ')}."


def _evidence(finding: HealthFindingData, perf: PerfOpportunityView | None) -> dict:
    evidence: dict = {}
    if perf is not None:
        evidence["opportunity"] = perf.summary()
        evidence["amplification"] = perf.amplification
        evidence["exposure"] = perf.exposure
        evidence["actionability"] = perf.actionability_state
        if perf.observations_total > 1:
            evidence["observations"] = perf.observations_total
    details = finding.details or {}
    for field in ("ccn", "nloc", "nesting", "params", "lines"):
        if field in details:
            evidence[field] = details[field]
    return evidence


def _unavailable(
    status: str,
    explanation: str,
    *,
    base: RevisionId | None = None,
    head: RevisionId | None = None,
    scope: ScopeCounts | None = None,
    fingerprint: AnalysisFingerprint | None = None,
) -> ChangeHealthDelta:
    return ChangeHealthDelta(
        status=status,  # type: ignore[arg-type]
        explanation=explanation,
        base=base,
        head=head,
        comparison_basis="not_compared",
        fingerprint=fingerprint,
        scope=scope or ScopeCounts(),
    )
