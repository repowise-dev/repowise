"""Canonical recommendation read model, ranking, validation, and serialization.

Detector suggestions and persisted ORM rows are deliberately small write-side
facts.  This module is the one read-side owner that turns either shape into the
recommendation contract consumed by REST, MCP, and CLI.  Every database read is
batched across the complete plan set; adding plans never adds SQL statements.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.grading import HEALTHY_MIN
from repowise.core.analysis.test_reachability import ReachedBy, tests_reaching_by_tier

from .models import RefactoringSuggestion

RecommendationView = Literal["canonical", "file_spread"]
ValidationBasis = Literal["measured", "inferred", "mixed", "unknown"]
ValidationVia = Literal["coverage", "call-graph", "import-graph", "mixed"]

DEFAULT_TEST_LIMIT = 12
_EFFORT_COST = {"S": 1.0, "M": 2.0, "L": 3.0, "XL": 5.0}
_CONFIDENCE_RISK = {"high": 0.0, "medium": 0.5, "low": 1.25}
_WEAK_PROVENANCE = {"name-fallback", "global_unique", "unknown"}


def _loads_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _attr(row: Any, name: str, default: Any = None) -> Any:
    return row.get(name, default) if isinstance(row, Mapping) else getattr(row, name, default)


def rehydrate_suggestion(row: Any) -> RefactoringSuggestion:
    """Normalize an ORM row, dataclass, or compatible object once."""
    if isinstance(row, RefactoringSuggestion):
        suggestion = row
    else:
        suggestion = RefactoringSuggestion(
            refactoring_type=str(_attr(row, "refactoring_type", "")),
            file_path=str(_attr(row, "file_path", "")),
            target_symbol=str(_attr(row, "target_symbol", "")),
            line_start=_attr(row, "line_start", None),
            line_end=_attr(row, "line_end", None),
            plan=_loads_dict(_attr(row, "plan", None) or _attr(row, "plan_json", None)),
            evidence=_loads_dict(_attr(row, "evidence", None) or _attr(row, "evidence_json", None)),
            impact_delta=float(_attr(row, "impact_delta", 0.0) or 0.0),
            effort_bucket=str(_attr(row, "effort_bucket", "") or ""),
            blast_radius=_loads_dict(
                _attr(row, "blast_radius", None) or _attr(row, "blast_radius_json", None)
            ),
            confidence=str(_attr(row, "confidence", "medium") or "medium"),
            source_biomarker=str(_attr(row, "source_biomarker", "") or ""),
        )
    row_id = _attr(row, "id", None)
    if row_id is not None:
        suggestion.id = str(row_id)  # type: ignore[attr-defined]
    return suggestion


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def _dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _named_strings(row: Mapping[str, Any], keys: Sequence[str]) -> set[str]:
    return {value for key in keys if isinstance((value := row.get(key)), str) and value}


def _location_values(plan: Mapping[str, Any], key: str) -> set[str]:
    return {
        value
        for location in _dict_list(plan.get("affected_locations"))
        if isinstance((value := location.get(key)), str) and value
    }


def _cut_edge_files(plan: Mapping[str, Any]) -> set[str]:
    return set().union(
        *(_named_strings(edge, ("from", "to")) for edge in _dict_list(plan.get("cut_edges"))),
        set(),
    )


def _suggested_group_files(plan: Mapping[str, Any]) -> set[str]:
    return {
        value
        for group in _dict_list(plan.get("groups"))
        if isinstance((value := group.get("suggested_file")), str) and value
    }


def affected_files(suggestion: RefactoringSuggestion) -> list[str]:
    """Files the plan explicitly says it changes or must keep consistent."""
    plan = suggestion.plan or {}
    blast = suggestion.blast_radius or {}
    files = {suggestion.file_path} if suggestion.file_path else set()
    files.update(_string_list(blast.get("files")))
    files.update(_string_list(blast.get("dependent_files")))
    files.update(_location_values(plan, "file_path"))
    files.update(_named_strings(plan, ("to_file", "intervention_file")))
    files.update(_string_list(plan.get("cycle")))
    files.update(_cut_edge_files(plan))
    files.update(_suggested_group_files(plan))
    return sorted(files)


def affected_symbols(suggestion: RefactoringSuggestion) -> list[str]:
    symbols = {suggestion.target_symbol} if suggestion.target_symbol else set()
    plan = suggestion.plan or {}
    symbols.update(
        _named_strings(plan, ("intervention_symbol", "method", "from_class", "to_class"))
    )
    symbols.update(_location_values(plan, "function_name"))
    return sorted(symbols)


def blast_size(suggestion: RefactoringSuggestion) -> int:
    """True change surface from every compatible persisted blast shape."""
    blast = suggestion.blast_radius or {}
    counts = [
        int(value)
        for key in (
            "file_count",
            "dependents_count",
            "dependent_count",
            "callers",
            "call_sites",
        )
        if isinstance((value := blast.get(key)), (int, float)) and value > 0
    ]
    counts.extend(len(_string_list(blast.get(key))) for key in ("files", "dependent_files"))
    return max(counts, default=max(0, len(affected_files(suggestion)) - 1))


def enrich_blast_radius(suggestion: RefactoringSuggestion, centrality: Mapping[str, float]) -> None:
    """Preserve the legacy caller rollup while centrality has one owner."""
    blast = dict(suggestion.blast_radius or {})
    if "callers" not in blast and "dependents_count" not in blast:
        files = _string_list(blast.get("files"))
        if files:
            blast["callers"] = sum(int(centrality.get(path, 0.0) or 0.0) for path in files)
    suggestion.blast_radius = blast


def _performance_benefit(evidence: Mapping[str, Any]) -> float:
    factors = evidence.get("rank_factors")
    if isinstance(factors, Mapping):
        raw = sum(
            float(value)
            for name, value in factors.items()
            if name != "affected_call_sites" and isinstance(value, (int, float))
        )
    else:
        # Older persisted rows may only carry the combined legacy rank. It included
        # blast radius, so use a conservative detector-native floor rather than
        # relabeling that mixed score as benefit.
        raw = 1.0 if evidence.get("rank_score") is not None else 0.0
    return math.log1p(max(0.0, raw))


def _cycle_benefit(evidence: Mapping[str, Any]) -> float:
    return 1.0 + math.log1p(
        max(float(evidence.get("cycle_size") or 0), float(evidence.get("cut_count") or 0))
    )


def _move_benefit(evidence: Mapping[str, Any]) -> float:
    foreign = float(evidence.get("foreign_calls") or 0)
    own = float(evidence.get("own_calls") or 0)
    return 1.0 + math.log1p(max(0.0, foreign - own))


def _split_benefit(evidence: Mapping[str, Any]) -> float:
    groups = float(evidence.get("group_count") or 0)
    return 1.0 + math.log1p(max(0.0, groups - 1.0))


_DETECTOR_BENEFIT: dict[str, Callable[[Mapping[str, Any]], float]] = {
    "performance_fix": _performance_benefit,
    "break_cycle": _cycle_benefit,
    "move_method": _move_benefit,
    "split_file": _split_benefit,
}


def detector_native_benefit(suggestion: RefactoringSuggestion) -> float:
    """Recoverable health or a detector-owned structural/performance gain."""
    impact = max(0.0, float(suggestion.impact_delta or 0.0))
    if impact:
        return impact
    benefit = _DETECTOR_BENEFIT.get(suggestion.refactoring_type)
    if benefit is not None:
        return benefit(suggestion.evidence or {})
    # A detector that emitted a concrete graph-native plan has a real but
    # deliberately small gain even when it is not a defect-health deduction.
    return 1.0 if suggestion.refactoring_type else 0.0


@dataclass(frozen=True, slots=True)
class ValidationTarget:
    file_path: str
    basis: ValidationBasis
    via: ValidationVia | None
    total: int
    tests: list[str]
    truncated: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "basis": self.basis,
            "via": self.via,
            "total": self.total,
            "tests": self.tests,
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class ValidationPlan:
    basis: ValidationBasis
    via: ValidationVia | None
    total: int
    tests: list[str]
    truncated: bool
    affected_files: list[str]
    affected_symbols: list[str]
    commands: list[str]
    targets: list[ValidationTarget] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "basis": self.basis,
            "via": self.via,
            "total": self.total,
            "tests": self.tests,
            "truncated": self.truncated,
            "affected_files": self.affected_files,
            "affected_symbols": self.affected_symbols,
            "commands": self.commands,
            "targets": [target.as_dict() for target in self.targets],
        }


def _commands(tests: list[str], files: list[str], *, total: int | None = None) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.
    """
    if total is not None and total > len(tests):
        tests = sorted({test.split("::", 1)[0] for test in tests})
    python_tests = sorted(test for test in tests if ".py" in test)
    js_tests = sorted(
        test for test in tests if any(ext in test for ext in (".ts", ".tsx", ".js", ".jsx"))
    )
    commands: list[str] = []
    if python_tests:
        commands.append("pytest " + " ".join(python_tests))
    if js_tests:
        commands.append("npm test -- " + " ".join(js_tests))
    if commands:
        return commands
    if any(path.endswith(".py") for path in files):
        return ["pytest"]
    if any(path.endswith((".ts", ".tsx", ".js", ".jsx")) for path in files):
        return ["npm test", "npm run type-check"]
    return ["npm run test"]


def _line_ranges(suggestion: RefactoringSuggestion) -> dict[str, set[int] | None]:
    """Lines each affected file changes, or ``None`` when the plan says only the file.

    Locations accumulate per file rather than replacing one another: a
    ``performance_fix`` emits one ``affected_locations`` entry per call site, and
    an N+1 routinely puts several of them in the same file. Overwriting kept only
    the last site, so a test covering any earlier one stopped intersecting, the
    plan fell back from ``measured`` to ``unknown``, and its rank dropped with it.
    """
    ranges: dict[str, set[int] | None] = {path: None for path in affected_files(suggestion)}
    if suggestion.file_path and suggestion.line_start:
        end = suggestion.line_end or suggestion.line_start
        ranges[suggestion.file_path] = set(range(suggestion.line_start, end + 1))
    for location in _dict_list((suggestion.plan or {}).get("affected_locations")):
        path = location.get("file_path")
        start = location.get("line_start")
        end = location.get("line_end") or start
        if isinstance(path, str) and isinstance(start, int) and isinstance(end, int):
            ranges[path] = (ranges.get(path) or set()) | set(range(start, end + 1))
    return ranges


def _measured_labels(rows: list[dict[str, Any]], lines: set[int] | None) -> set[str]:
    labels: set[str] = set()
    for row in rows:
        covered = set(row.get("covered_lines") or [])
        if lines is not None and not lines.intersection(covered):
            continue
        label = row.get("test_id") or row.get("test_file")
        if isinstance(label, str) and label:
            labels.add(label)
    return labels


def _validation_target(
    file_path: str,
    lines: set[int] | None,
    measured: Mapping[str, list[dict[str, Any]]],
    inferred: Mapping[str, ReachedBy],
    cap: int,
) -> tuple[ValidationTarget, set[str], bool]:
    labels = _measured_labels(measured.get(file_path, []), lines)
    if labels:
        basis: ValidationBasis = "measured"
        via: ValidationVia | None = "coverage"
        total = len(labels)
        identities_complete = True
    else:
        reached = inferred.get(file_path)
        labels = set(reached.all_tests or reached.tests) if reached is not None else set()
        basis = "inferred" if reached is not None and reached.total else "unknown"
        via = reached.via if reached is not None and reached.total else None
        total = reached.total if reached is not None else 0
        identities_complete = (
            reached is None or reached.all_tests is not None or total == len(labels)
        )
    ordered = sorted(labels)
    return (
        ValidationTarget(
            file_path=file_path,
            basis=basis,
            via=via,
            total=total,
            tests=ordered[:cap],
            truncated=total > cap,
        ),
        labels,
        identities_complete,
    )


def build_validation_plan(
    suggestion: RefactoringSuggestion,
    measured: Mapping[str, list[dict[str, Any]]],
    inferred: Mapping[str, ReachedBy],
    *,
    test_limit: int = DEFAULT_TEST_LIMIT,
) -> ValidationPlan:
    """Resolve target evidence in strict measured/call/import precedence."""
    cap = max(0, test_limit)
    target_rows: list[ValidationTarget] = []
    union: set[str] = set()
    identities_complete = True
    for file_path, lines in sorted(_line_ranges(suggestion).items()):
        target, labels, target_complete = _validation_target(
            file_path, lines, measured, inferred, cap
        )
        union.update(labels)
        identities_complete = identities_complete and target_complete
        target_rows.append(target)

    evidence_targets = [target for target in target_rows if target.total]
    bases = {target.basis for target in target_rows}
    vias = {target.via for target in evidence_targets if target.via is not None}
    aggregate_basis: ValidationBasis = (
        "unknown" if not bases else next(iter(bases)) if len(bases) == 1 else "mixed"
    )
    aggregate_via: ValidationVia | None = (
        None if not vias else next(iter(vias)) if len(vias) == 1 else "mixed"
    )
    ordered_tests = sorted(union)
    aggregate_total = (
        len(ordered_tests)
        if identities_complete
        else target_rows[0].total
        if len(target_rows) == 1
        else sum(target.total for target in target_rows)
    )
    files = affected_files(suggestion)
    return ValidationPlan(
        basis=aggregate_basis,
        via=aggregate_via,
        total=aggregate_total,
        tests=ordered_tests[:cap],
        truncated=aggregate_total > len(ordered_tests[:cap]),
        affected_files=files,
        affected_symbols=affected_symbols(suggestion),
        commands=_commands(ordered_tests[:cap], files, total=aggregate_total),
        targets=target_rows,
    )


@dataclass(frozen=True, slots=True)
class Recommendation:
    suggestion: RefactoringSuggestion = field(repr=False, compare=False)
    benefit: float
    leverage: float
    cost: float
    risk: float
    rank_score: float
    dependents: int
    file_nloc: int
    file_weighted_deficit: int
    validation: ValidationPlan

    @property
    def id(self) -> str:
        return str(getattr(self.suggestion, "id", "") or "")

    def as_dict(self) -> dict[str, Any]:
        suggestion = self.suggestion
        return {
            "id": self.id,
            "refactoring_type": suggestion.refactoring_type,
            "file_path": suggestion.file_path,
            "target_symbol": suggestion.target_symbol,
            "line_start": suggestion.line_start,
            "line_end": suggestion.line_end,
            "plan": suggestion.plan or {},
            "evidence": suggestion.evidence or {},
            "impact_delta": round(float(suggestion.impact_delta or 0.0), 3),
            "effort_bucket": suggestion.effort_bucket,
            "blast_radius": suggestion.blast_radius or {},
            "confidence": suggestion.confidence,
            "source_biomarker": suggestion.source_biomarker,
            "benefit": self.benefit,
            "leverage": self.leverage,
            "cost": self.cost,
            "risk": self.risk,
            "rank_score": self.rank_score,
            "dependents": self.dependents,
            "file_nloc": self.file_nloc,
            "file_weighted_deficit": self.file_weighted_deficit,
            "validation": self.validation.as_dict(),
        }


def _priority_components(
    suggestion: RefactoringSuggestion,
    *,
    nloc: int,
    health_score: float,
    dependents: int,
    validation: ValidationPlan,
) -> tuple[float, float, float, float, float, int]:
    weighted_deficit = round(max(HEALTHY_MIN - health_score, 0.0) * max(nloc, 1))
    benefit = detector_native_benefit(suggestion)
    entry_bonus = 0.5 if (suggestion.evidence or {}).get("reliable_entry_reachability") else 0.0
    leverage = 0.5 * math.log1p(weighted_deficit) + math.log1p(max(0, dependents)) + entry_bonus
    surface = max(blast_size(suggestion), max(0, len(affected_files(suggestion)) - 1))
    cost = _EFFORT_COST.get(suggestion.effort_bucket, 3.0) + math.log1p(surface)
    provenance = str((suggestion.evidence or {}).get("provenance") or "")
    weak_graph = 1.0 if provenance in _WEAK_PROVENANCE else 0.0
    validation_risk = {
        "measured": 0.0,
        "mixed": 0.5,
        "inferred": 0.75,
        "unknown": 1.5,
    }[validation.basis]
    risk = (
        math.log1p(surface)
        + _CONFIDENCE_RISK.get(suggestion.confidence, 0.75)
        + weak_graph
        + validation_risk
    )
    priority = (1.0 + benefit) * (1.0 + leverage) / (1.0 + cost + risk)
    return (
        round(benefit, 4),
        round(leverage, 4),
        round(cost, 4),
        round(risk, 4),
        round(priority, 4),
        weighted_deficit,
    )


def build_recommendations(
    rows: Sequence[Any],
    *,
    metric_by_path: Mapping[str, Any] | None = None,
    centrality: Mapping[str, float] | None = None,
    validations: Mapping[int, ValidationPlan] | None = None,
) -> list[Recommendation]:
    """Pure recommendation construction used by every surface and tests."""
    metrics = metric_by_path or {}
    centrality = centrality or {}
    validations = validations or {}
    out: list[Recommendation] = []
    for index, row in enumerate(rows):
        suggestion = rehydrate_suggestion(row)
        enrich_blast_radius(suggestion, centrality)
        metric = metrics.get(suggestion.file_path)
        nloc = int(_attr(metric, "nloc", 0) or 0)
        raw_health_score = _attr(metric, "score", HEALTHY_MIN)
        health_score = float(HEALTHY_MIN if raw_health_score is None else raw_health_score)
        dependents = int(float(centrality.get(suggestion.file_path, 0.0) or 0.0))
        validation = validations.get(index) or build_validation_plan(suggestion, {}, {})
        benefit, leverage, cost, risk, rank_score, deficit = _priority_components(
            suggestion,
            nloc=nloc,
            health_score=health_score,
            dependents=dependents,
            validation=validation,
        )
        suggestion.validation = validation.as_dict()
        out.append(
            Recommendation(
                suggestion=suggestion,
                benefit=benefit,
                leverage=leverage,
                cost=cost,
                risk=risk,
                rank_score=rank_score,
                dependents=dependents,
                file_nloc=nloc,
                file_weighted_deficit=deficit,
                validation=validation,
            )
        )
    return canonical_order(out)


def canonical_order(recommendations: Sequence[Recommendation]) -> list[Recommendation]:
    return sorted(
        recommendations,
        key=lambda recommendation: (
            -recommendation.rank_score,
            recommendation.suggestion.refactoring_type,
            recommendation.suggestion.file_path,
            recommendation.suggestion.target_symbol,
            recommendation.id,
        ),
    )


def apply_view(
    recommendations: Sequence[Recommendation], view: RecommendationView = "canonical"
) -> list[Recommendation]:
    ranked = canonical_order(recommendations)
    if view == "canonical":
        return ranked
    if view != "file_spread":
        raise ValueError(f"unknown recommendation view: {view}")
    by_file: dict[str, list[Recommendation]] = {}
    for recommendation in ranked:
        by_file.setdefault(recommendation.suggestion.file_path, []).append(recommendation)
    spread: list[Recommendation] = []
    while by_file:
        for file_path in list(by_file):
            spread.append(by_file[file_path].pop(0))
            if not by_file[file_path]:
                del by_file[file_path]
    return spread


async def hydrate_recommendations(
    session: AsyncSession,
    repository_id: str,
    rows: Sequence[Any],
    *,
    metric_rows: Sequence[Any] | None = None,
    view: RecommendationView = "canonical",
    test_limit: int = DEFAULT_TEST_LIMIT,
) -> list[Recommendation]:
    """Hydrate, enrich, validate, rank, and serialize-ready all *rows*.

    Query shape is constant in plan/test count: health metrics and graph metrics
    are bulk reads, measured coverage is one ``IN`` query, and inferred walks
    use their existing bounded level queries over the complete unanswered set.
    """
    from repowise.core.persistence import crud
    from repowise.core.persistence.crud.analysis.coverage_map import tests_covering_files

    if not rows:
        return []
    suggestions = [rehydrate_suggestion(row) for row in rows]
    metrics = (
        list(metric_rows)
        if metric_rows is not None
        else await crud.get_health_metrics(session, repository_id)
    )
    graph_metrics = await crud.get_graph_metrics(session, repository_id)
    centrality = {
        node_id: float(metric.get("in_degree") or 0.0) for node_id, metric in graph_metrics.items()
    }
    target_files = sorted(
        {path for suggestion in suggestions for path in affected_files(suggestion)}
    )
    measured = await tests_covering_files(session, repository_id, set(target_files))

    # A measured row only answers a target when it intersects the plan's line
    # range (if one exists).  Seed inference once with every file that remains
    # unanswered for at least one plan.
    unanswered: set[str] = set()
    for suggestion in suggestions:
        for file_path, lines in _line_ranges(suggestion).items():
            if not _measured_labels(measured.get(file_path, []), lines):
                unanswered.add(file_path)
    inferred = (
        await tests_reaching_by_tier(session, repository_id, sorted(unanswered))
        if unanswered
        else {}
    )
    validations = {
        index: build_validation_plan(suggestion, measured, inferred, test_limit=test_limit)
        for index, suggestion in enumerate(suggestions)
    }
    recommendations = build_recommendations(
        suggestions,
        metric_by_path={metric.file_path: metric for metric in metrics},
        centrality=centrality,
        validations=validations,
    )
    return apply_view(recommendations, view)


def serialize_recommendations(
    recommendations: Sequence[Recommendation],
) -> list[dict[str, Any]]:
    return [recommendation.as_dict() for recommendation in recommendations]


__all__ = [
    "DEFAULT_TEST_LIMIT",
    "Recommendation",
    "RecommendationView",
    "ValidationPlan",
    "ValidationTarget",
    "affected_files",
    "affected_symbols",
    "apply_view",
    "blast_size",
    "build_recommendations",
    "build_validation_plan",
    "canonical_order",
    "detector_native_benefit",
    "enrich_blast_radius",
    "hydrate_recommendations",
    "rehydrate_suggestion",
    "serialize_recommendations",
]
