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
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

from ...support_paths import TEMPLATE_EXTENSIONS, is_doc_or_config_path
from ...test_paths import is_test_related_path
from ..finding_registry import excluded_types, gate_for, is_served, language_label
from ..health import HEALTH_ANALYZER_VERSION, HealthFindingData
from ..health.biomarker_labels import biomarker_label
from ..health.perf.causal import PERFORMANCE_MODEL_VERSION
from ..health.scoring import ADVISORY_DIMENSION, is_advisory
from .analyzer import RevisionAnalysis, RevisionHealthAnalyzer, language_for, language_of
from .attribution import FindingAttributor, changed_symbols_for
from .file_scores import file_score_deltas
from .identity import change_finding_id, finding_key, severity_rank
from .matcher import FindingMatcher, MatchedFinding, MatchResult
from .models import (
    NO_CODE_GAP,
    NOT_ANALYZABLE_DATA,
    NOT_CODE,
    AnalysisFingerprint,
    ChangeFinding,
    ChangeHealthDelta,
    RevisionId,
    ScopeCounts,
)
from .moves import bodies_for, build_move_map
from .perf_delta import PerfOpportunityView, index_by_finding, opportunities_for
from .sources import (
    FileChange,
    GitRevisionSource,
    RevisionPair,
    RevisionSource,
    filter_changes,
)

#: Changed files above this count are refused rather than analysed twice.
MAX_CHANGED_FILES = 300

#: Documentation and configuration languages. Anything else with no health
#: dialect (HTML, schemas, shell) may carry logic, so it stays a gap.
_NOT_CODE_LANGUAGES = frozenset({"json", "yaml", "toml", "markdown"})

#: Tree-sitter query files: patterns no health detector reads. ``.scm`` is also
#: Scheme source, so only the tree-sitter layout (a ``queries`` directory)
#: counts; any other ``.scm`` stays a gap.
_QUERY_SUFFIX = ".scm"
_QUERY_DIR = "queries"

#: Comparisons kept in the process cache.
_CACHE_CAPACITY = 32

#: How long a caller waits on an identical in-flight comparison before doing
#: the work itself. Short enough not to hold a pool thread for minutes.
_WAIT_TIMEOUT_SECONDS = 30

#: Wait-then-retry rounds before a caller stops queueing and computes.
_CLAIM_ATTEMPTS = 2

_DIMENSION_ORDER = {"defect": 0, "maintainability": 1, "performance": 2, ADVISORY_DIMENSION: 3}

_BASIS = "both_sides_analyzed"

#: Bases that place a finding away from the lines the change wrote.
_INCIDENTAL_BASES = {"file_change", "context_change", "unknown"}


@dataclass(frozen=True, slots=True)
class DeltaRequest:
    """What to compare. ``repo_path`` is unused by the comparison; ``None`` is fine."""

    repo_path: str | None
    revspec: str | None
    extensions: tuple[str, ...] = ()
    exclude_patterns: tuple[str, ...] = ()
    #: Gitignore-style paths to keep; empty keeps every path.
    include_paths: tuple[str, ...] = ()
    #: ``{path: dependents}`` from the index, scored on both sides when given.
    dependents: Mapping[str, int] | None = None


class ChangeHealthDeltaService:
    """Compare the health of two revisions of the same repository.

    Without a checkout, pass a ``MappingRevisionSource`` (diff hunks plus raw
    bytes at both SHAs). Both sides are re-scored from those bytes, so custom
    health rules need ``RevisionHealthAnalyzer(config=...)`` to match the index.
    """

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
        self._custom_analyzer = analyzer is not None
        self.analyzer = analyzer or RevisionHealthAnalyzer(repo_root=repo_path)
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
            # Only the compared files: an excluded untracked blob is not read.
            paths = sorted(c.head_path for c in _filter(pair.changes, request) if c.head_path)
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
            # repr, not a join: a comma is legal inside a pattern.
            repr(tuple(request.extensions)),
            repr(tuple(request.exclude_patterns)),
            repr(tuple(request.include_paths)),
            _dependents_digest(request.dependents),
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

    def _compare_uncached(self, request: DeltaRequest, pair: RevisionPair) -> ChangeHealthDelta:
        started = time.perf_counter()
        changes = _filter(pair.changes, request)
        eligible, departed, skipped = _partition(changes)
        scope = ScopeCounts(changed=len(changes), eligible=len(eligible) + len(departed))
        base_id, head_id = _revision_ids(pair)

        if not scope.eligible:
            delta = ChangeHealthDelta(
                # Nothing was compared either way, so neither case is a clean bill.
                status="unavailable",
                explanation=_nothing_compared(changes, skipped),
                base=base_id,
                head=head_id,
                comparison_basis=_BASIS,
                fingerprint=self.fingerprint,
                scope=scope,
                skipped=skipped,
            )
            delta.timing_ms = (time.perf_counter() - started) * 1000
            return delta

        if scope.eligible > MAX_CHANGED_FILES:
            return _unavailable(
                "too_large",
                f"{scope.eligible} changed files exceeds the {MAX_CHANGED_FILES}-file "
                "ceiling for a two-sided analysis.",
                base=base_id,
                head=head_id,
                scope=scope,
                fingerprint=self.fingerprint,
            )

        index = _index_dependents(request.dependents, pair.rename_map())
        sides = self._analyze(pair, eligible, departed, index)
        skipped.update(sides.head_run.skipped)
        skipped.update(_held_origins(sides.head_sources, self.repo_path))
        skipped.update(_base_failures(eligible + departed, sides.base_run, skipped))
        subject = {
            p for p in sides.head_run.analyzed if p in set(sides.head_paths) and p not in skipped
        }
        moves = build_move_map(
            bodies_for(sides.base_sources, sides.base_run.symbols),
            bodies_for(sides.head_sources, sides.head_run.symbols),
            rename_map=sides.rename,
        )
        gone = {c.base_path for c in departed if c.base_path and c.base_path not in skipped}

        scope.analyzed = len(subject) + len(gone)
        scope.skipped = len(skipped)
        scope.failed = sum(
            1 for r in skipped.values() if r.removeprefix("base_") in {"parse_failed", "unreadable"}
        )
        match = _match(sides, subject, gone, FindingMatcher(sides.rename, _hunks(eligible), moves))
        findings = self._surface(match, eligible, sides.head_run.findings_for(subject), pair)

        status, explanation = _status_for(scope, skipped, findings, deleted=len(gone))
        delta = ChangeHealthDelta(
            status=status,
            explanation=explanation,
            base=base_id,
            head=head_id,
            comparison_basis=_BASIS,
            fingerprint=self.fingerprint,
            scope=scope,
            findings=findings,
            resolved_total=len(match.resolved),
            unchanged_total=match.unchanged_total,
            improved_total=match.improved_total,
            moved_total=match.moved_total,
            skipped=skipped,
            moved_to={old: new for old, new in moves.destinations().items() if old in gone},
            limits=_limits(sides.head_run.findings_for(subject)),
            context="index_dependents" if index is not None else "changed_files",
            files=file_score_deltas(
                sides.base_run.metrics,
                sides.head_run.metrics,
                subject,
                sides.rename,
                departed={old: moves.destinations().get(old, []) for old in gone},
            ),
        )
        delta.timing_ms = (time.perf_counter() - started) * 1000
        return delta

    def _analyze(
        self,
        pair: RevisionPair,
        eligible: list[FileChange],
        departed: list[FileChange],
        index: dict[str, int] | None,
    ) -> _Sides:
        """Read and analyse both sides; *departed* (deleted) files go on the base side only.

        Code split out of a deleted file then pairs with its new home instead of
        reading as introduced.
        """
        head_paths = [c.head_path for c in eligible if c.head_path]
        base_paths = [c.base_path for c in eligible + departed if c.base_path]
        head_sources = (
            self.source.read_working_tree(head_paths)
            if pair.working_tree
            else self.source.read(pair.head_sha, head_paths)
        )
        base_sources = self.source.read(pair.base_sha, base_paths)
        rename = pair.rename_map()
        analyzer = self._analyzer_for([*head_paths, *base_paths])
        head_run = analyzer.analyze(head_sources, subject_paths=set(head_paths), dependents=index)
        base_run = analyzer.analyze(
            base_sources,
            subject_paths=set(base_paths),
            dependents=index if index is not None else _on_base(head_run, rename),
        )
        return _Sides(head_paths, head_sources, base_sources, head_run, base_run, rename)

    def _analyzer_for(self, paths: list[str]) -> RevisionHealthAnalyzer:
        if self._custom_analyzer or self.repo_path is None:
            return self.analyzer
        config = _analyzer_config(self.repo_path, paths)
        if config is None:
            return self.analyzer
        return RevisionHealthAnalyzer(repo_root=self.repo_path, config=config)

    def _surface(
        self,
        match: MatchResult,
        eligible: list[FileChange],
        head_findings: list[HealthFindingData],
        pair: RevisionPair,
    ) -> list[ChangeFinding]:
        """The introduced and worsened findings, attributed and in priority order."""
        by_file: dict[str, list[HealthFindingData]] = {}
        for finding in head_findings:
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
        return findings

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
        moved_from = matched.moved_from
        attribution = attributor.attribute(
            head, changed_symbols=changed_symbols, changed_call_edge=edge, moved_from=moved_from
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
            reason=head.reason or biomarker_label(str(head.biomarker_type)),
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
            moved_from={"path": moved_from[0], "symbol": moved_from[1]} if moved_from else None,
        )


# -- helpers ----------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Sides:
    """Both sides' bytes and analyses, as one comparison reads them."""

    head_paths: list[str]
    head_sources: dict[str, bytes]
    base_sources: dict[str, bytes]
    head_run: RevisionAnalysis
    base_run: RevisionAnalysis
    rename: dict[str, str]


def _partition(
    changes: list[FileChange],
) -> tuple[list[FileChange], list[FileChange], dict[str, str]]:
    """``(eligible, departed, skipped)``: analysable on head, deleted but analysable, the rest."""
    eligible: list[FileChange] = []
    departed: list[FileChange] = []
    skipped: dict[str, str] = {}
    for change in changes:
        if change.head_path and language_for(change.head_path):
            eligible.append(change)
        elif change.head_path is None and language_for(change.base_path or ""):
            departed.append(change)
        elif path := change.head_path or change.base_path:
            skipped[path] = _skip_reason(change)
    return eligible, departed, skipped


def _revision_ids(pair: RevisionPair) -> tuple[RevisionId, RevisionId]:
    base = RevisionId(pair.base_ref, pair.base_sha, "commit")
    head = RevisionId(
        pair.head_ref, pair.head_sha or "", "working_tree" if pair.working_tree else "commit"
    )
    return base, head


def _base_failures(
    changes: list[FileChange], base_run: RevisionAnalysis, skipped: dict[str, str]
) -> dict[str, str]:
    """``{path: "base_<reason>"}`` for the files whose base side failed.

    Without a baseline every head finding in the file would read as introduced,
    so the file leaves the subject and the run says so.
    """
    out: dict[str, str] = {}
    for change in changes:
        path = change.head_path or change.base_path
        reason = base_run.skipped.get(change.base_path or "")
        if path and path not in skipped and reason is not None:
            out[path] = f"base_{reason}"
    return out


def _match(
    sides: _Sides, subject: set[str], gone: set[str], matcher: FindingMatcher
) -> MatchResult:
    """Match the comparable findings of both sides over the same file set.

    Advisory markers deduct nothing, so adding one is not a regression and
    removing one is not a fix; registry-withheld types and gated languages go
    the same way. Dropped from both sides before matching, so every counter
    derived from the match agrees.
    """
    withheld = excluded_types()
    base_findings = [
        f
        for f in sides.base_run.findings
        if (sides.rename.get(f.file_path, f.file_path) in subject or f.file_path in gone)
        and _comparable(f, withheld)
    ]
    head_findings = [f for f in sides.head_run.findings_for(subject) if _comparable(f, withheld)]
    match = matcher.match(base_findings, head_findings)
    # A deleted file's unpaired findings may have moved somewhere this run did
    # not read (an excluded or unparsed file), so they never count as resolved.
    match.resolved = [f for f in match.resolved if f.file_path not in gone]
    return match


def _filter(changes: list[FileChange], request: DeltaRequest) -> list[FileChange]:
    """Apply the caller's extension, inclusion and exclusion filters to the change set."""
    return filter_changes(
        changes,
        extensions=request.extensions,
        exclude_patterns=request.exclude_patterns,
        include_paths=request.include_paths,
    )


def _nothing_compared(changes: list[FileChange], skipped: dict[str, str]) -> str:
    """Why no changed file reached the analyzer, for the explanation."""
    if not changes:
        return "This change touches no files."
    if all(r == NOT_CODE for r in skipped.values()):
        return (
            "Only documentation or configuration files changed; "
            "health analysis does not cover them."
        )
    if all(r in NO_CODE_GAP for r in skipped.values()):
        return (
            "Only query, template or other data files changed; health analysis does not cover them."
        )
    return "No changed file is health-analyzable, so nothing was compared."


def _held_origins(sources: dict[str, bytes], repo_root: str | None) -> dict[str, str]:
    """``{path: "vendored" | "generated"}`` for the changed files every health
    surface holds back by origin. Read from the head bytes with the classifier
    the index stores ``code_origin`` from, so a file the index has not seen yet
    (new in this change) is classed the same way it will be once indexed."""
    from ...code_origin import code_origin
    from ..finding_registry import HELD_ORIGINS

    out: dict[str, str] = {}
    for path, raw in sources.items():
        origin = code_origin(path, raw, repo_root=repo_root)
        if origin in HELD_ORIGINS:
            out[path] = origin
    return out


def _skip_reason(change: FileChange) -> str:
    """Why a changed path never reached the analyzer."""
    if change.head_path is None:
        return "deleted"
    language = language_of(change.head_path)
    if language in _NOT_CODE_LANGUAGES or is_doc_or_config_path(change.head_path):
        return NOT_CODE
    if language is None:
        path = PurePosixPath(change.head_path)
        suffix = path.suffix.lower()
        if suffix in TEMPLATE_EXTENSIONS or (
            suffix == _QUERY_SUFFIX and _QUERY_DIR in path.parts[:-1]
        ):
            return NOT_ANALYZABLE_DATA
        return "unsupported_language"
    return "not_health_analyzable"


def _status_for(
    scope: ScopeCounts, skipped: dict[str, str], findings: list[ChangeFinding], *, deleted: int = 0
) -> tuple[str, str]:
    if scope.analyzed == 0:
        return "unavailable", (
            f"None of the {scope.changed} changed "
            f"{'file' if scope.changed == 1 else 'files'} could be analysed."
        )
    # Docs, config and data hold no code, so skipping them leaves nothing unexamined.
    code_skipped = sum(1 for r in skipped.values() if r not in NO_CODE_GAP)
    if code_skipped or scope.failed:
        return (
            "partial",
            f"Compared {scope.analyzed} of {scope.changed} changed files; "
            f"{code_skipped} that may hold code were not analysed.",
        )
    if not findings:
        return (
            "available",
            "No supported new findings surfaced in the analyzed scope.",
        )
    both = f"Compared {scope.analyzed - deleted} changed files on both sides"
    return "available", f"{both} and {deleted} deleted on the base side." if deleted else f"{both}."


def _limits(head_findings: list[HealthFindingData]) -> list[str]:
    limits = [
        "Both sides are analysed over the changed files only, so cross-file "
        "detectors (duplication, cross-function performance) see no wider context."
    ]
    gated = sorted(
        {
            gate.language
            for f in head_findings
            if (gate := gate_for("health", f.file_path, f.biomarker_type)) is not None
        }
    )
    if gated:
        limits.append(
            f"Health findings on {', '.join(language_label(g) for g in gated)} files "
            "are not compared: they are held back pending verification there."
        )
    return limits


def _on_base(head_run: RevisionAnalysis, rename: dict[str, str]) -> dict[str, int]:
    """Head's dependents counts under base-side paths, to score base against.

    See :meth:`RevisionHealthAnalyzer.analyze`: one centrality context for both
    sides, so only the code can move a centrality-gated finding.
    """
    to_base = {head: base for base, head in rename.items()}
    return {to_base.get(path, path): count for path, count in head_run.dependents.items()}


def _index_dependents(
    dependents: Mapping[str, int] | None, rename: dict[str, str]
) -> dict[str, int] | None:
    """The index's counts with each renamed file under both of its paths."""
    if dependents is None:
        return None
    merged = dict(dependents)
    for old, new in rename.items():
        count = dependents.get(old, dependents.get(new))
        if count is not None:
            merged.setdefault(old, count)
            merged.setdefault(new, count)
    return merged


def _dependents_digest(dependents: Mapping[str, int] | None) -> str:
    if dependents is None:
        return ""
    digest = hashlib.sha256()
    for path, count in sorted(dependents.items()):
        digest.update(f"{path}\t{count}\n".encode())
    return digest.hexdigest()


def _hunks(changes: list[FileChange]) -> dict[str, list[tuple[int, int, int, int]]]:
    """Each changed file's diff hunks, by head path, for the matcher's line map."""
    return {c.head_path: c.diff.hunks for c in changes if c.head_path and c.diff}


def _comparable(finding: HealthFindingData, withheld: frozenset[str]) -> bool:
    """A finding both sides of a comparison count: not advisory, not a test's
    performance finding, and one the registry serves."""
    return (
        not is_advisory(finding.biomarker_type)
        and not _is_test_perf(finding)
        and is_served("health", finding.biomarker_type, finding.file_path, withheld)
    )


def _is_test_perf(finding: HealthFindingData) -> bool:
    """A performance finding on test code.

    Performance findings are about code that serves requests, which a test file
    is not, so these are dropped from both sides rather than ranked. Other
    dimensions still report on tests; only their ordering is demoted.
    """
    return finding.dimension == "performance" and is_test_related_path(finding.file_path)


def _authorship(finding: ChangeFinding) -> tuple[bool, bool, bool]:
    """How much of this finding the change actually wrote.

    Ranked ahead of severity because a reader asked "what did I just do" and a
    capped list answered with the worst thing in a file they only brushed. An
    incidental basis means the finding sits away from the changed lines; test
    scaffolding sorts last because it is the change's own noise.
    """
    return (
        finding.attribution_basis in _INCIDENTAL_BASES,
        is_test_related_path(finding.path),
        finding.change_kind != "introduced",
    )


def _priority(finding: ChangeFinding) -> tuple:
    """Authorship first, then severity, then dimension and reach."""
    if finding.dimension == "performance":
        return (
            *_authorship(finding),
            -severity_rank(finding.severity),
            -(finding.opportunity_rank or 0),
            _DIMENSION_ORDER["performance"],
            finding.path,
        )
    return (
        *_authorship(finding),
        -severity_rank(finding.severity),
        -int((finding.health_impact or 0.0) * 1000),
        _DIMENSION_ORDER.get(finding.dimension, 9),
        finding.path,
    )


#: What an ``expected`` cause says, by its actionability reason.
_INHERENT = "Nothing to change: the repetition is inherent or already batched."
_EXPECTED_TEXT: dict[str | None, str] = {
    "inherent_to_boundary": _INHERENT,
    "loop_already_chunked": _INHERENT,
    "loop_not_batchable": "Nothing to batch: each call needs the one before, or the loop "
    "stops at the first that answers.",
    "gated_off": "Nothing to change while a constant-False flag switches this function off.",
    "cold_path": "Runs once per deploy, boot or incident; not worth scheduling.",
    "bounded_loop": "Nothing to change: the loop runs a fixed number of times.",
    "small_table": "Nothing to change: the loop walks a table that does not grow with the "
    "repository.",
    None: _INHERENT,
}


def _suggestion(finding: HealthFindingData, perf: PerfOpportunityView | None) -> str:
    if perf is not None:
        if perf.actionability_state == "plan_ready" and perf.intervention_symbol:
            return f"Hoist or batch the repeated call in {perf.intervention_symbol}."
        if perf.actionability_state == "expected":
            return _EXPECTED_TEXT.get(perf.actionability_reason, _EXPECTED_TEXT[None])
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


def _analyzer_config(repo_path: str, paths: list[str]) -> dict[str, object] | None:
    """The repo's `.repowise/health-rules.json` as analyzer config, or `None`."""
    from ..health.config import HealthConfig

    cfg = HealthConfig.load(repo_path)
    return cfg.to_analyzer_config(paths) if cfg.has_overrides() else None
