"""Canonical recommendation read model, ranking, validation, and serialization.

Detector suggestions and persisted ORM rows are deliberately small write-side
facts.  This module is the one read-side owner that turns either shape into the
recommendation contract consumed by REST, MCP, and CLI.  Every database read is
batched across the complete plan set; adding plans never adds SQL statements.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import math
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.effort import EFFORT_ORDER
from repowise.core.analysis.health.grading import TARGET_SCORE
from repowise.core.analysis.health.perf.ranking import _percentile_threshold
from repowise.core.analysis.health.queue_rules import FilterRule, SortKeys
from repowise.core.analysis.health.refactoring_summary import STRUCTURAL_TYPES
from repowise.core.analysis.pr_blast import rank_tests_by_reach
from repowise.core.analysis.test_collection import narrow_scopes
from repowise.core.analysis.test_reachability import (
    DEFAULT_CALL_DEPTH,
    MAX_TESTS_PER_TARGET,
    ReachDistance,
    ReachedBy,
    cached_test_files,
    imported_names_by_test,
    rank_tests,
    reach_into_symbols,
    tests_matching_by_name,
    tests_reaching_by_tier,
)
from repowise.core.code_origin import ship_rank
from repowise.core.test_paths import is_test_support_path, names_test_for, paired_test_names

from .annotations import PlanAnnotations, partner_rows
from .models import RefactoringSuggestion

RecommendationView = Literal["canonical", "file_spread"]
ValidationBasis = Literal["measured", "inferred", "mixed", "unknown"]
ValidationVia = Literal["coverage", "call-graph", "import-graph", "name-match", "mixed"]
VerifyCoverage = Literal["measured", "inferred", "none"]

DEFAULT_TEST_LIMIT = 12
# Public because the opportunity rank charges the same work and the same
# uncertainty over a whole step set. Two tables would be two policies.
EFFORT_COST = {"S": 1.0, "M": 2.0, "L": 3.0, "XL": 5.0}
CONFIDENCE_RISK = {"high": 0.0, "medium": 0.5, "low": 1.25}
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


def surface_confidence_risk(surface: int, confidence: str) -> float:
    """Risk from change surface and detector certainty, charged once.

    Both the plan rank and the opportunity rank ask this, over their own
    surface: one plan's blast radius, or the union across a step set. Two
    copies would be two policies about the same two inputs.
    """
    return math.log1p(max(0, surface)) + CONFIDENCE_RISK.get(confidence, 0.75)


def priority_score(*, benefit: float, uplift: float, cost: float, risk: float) -> float:
    """Benefit scaled by what makes it attractive, over what it costs.

    Benefit multiplies rather than offsets, so something with no evidence of a
    gain scores zero and cannot be lifted above something that recovers health
    by being popular, cheap or easy. What counts as benefit and what counts as
    uplift differ between a plan and a composed opportunity, and are decided by
    their own owners; this shape is the part they share.
    """
    return benefit * (1.0 + uplift) / (1.0 + cost + risk)


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
    # No health deduction and no detector-native measure: there is no
    # evidence of a gain, and a flat placeholder is what let zero-impact clones
    # in popular files lead the board. Report the absence.
    return 0.0


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
    # Why each shown test is listed where it is, keyed by test id.
    reasons: dict[str, str] = field(default_factory=dict)
    # What to do before the edit when no test reaches the change.
    prerequisite: str | None = None
    # Per ``plan["steps"]`` entry of a multi-step plan, its ``verify`` where it
    # differs from the plan's, else ``None``; empty when none differs. Kept out
    # of :meth:`as_dict`, which every list serves; plan detail reads it.
    step_verify: list[dict[str, Any] | None] = field(default_factory=list)

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
            "reasons": self.reasons,
            "prerequisite": self.prerequisite,
        }


SymbolSpan = tuple[str, int, int]


@dataclass(frozen=True, slots=True)
class ValidationEvidence:
    """Graph facts that order a plan's tests, read once for the whole plan set.

    ``symbols`` is each file's symbol ids with their line spans, ``symbol_reach``
    how close each test gets to a symbol, and ``imports`` the names each test
    imports from a file. Empty, the order falls back to name and directory.
    ``hubs`` are the files no reach walk passes through (:func:`hub_files`), and
    ``siblings`` each directory's files, which decide whose test a name is.
    """

    symbols: Mapping[str, Sequence[SymbolSpan]] = field(default_factory=dict)
    symbol_reach: Mapping[str, Mapping[str, ReachDistance]] = field(default_factory=dict)
    imports: Mapping[str, Mapping[str, frozenset[str]]] = field(default_factory=dict)
    hubs: frozenset[str] = frozenset()
    siblings: Mapping[str, tuple[str, ...]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ValidationInputs:
    """The coverage rows and reachability walk one hydration read.

    ``evidence`` holds the hubs and the symbol facts for hub files only: every
    pass narrows a hub target the same way, detailed or not.
    """

    measured: Mapping[str, list[dict[str, Any]]]
    inferred: Mapping[str, ReachedBy]
    test_files: set[str]
    evidence: ValidationEvidence = field(default_factory=ValidationEvidence)


# A hub is a module most of the code imports: fan-in above the absolute bar, or
# in the top 1% of the repository's files and at least the floor. Far stricter
# than ``PerfRanker``'s top-quintile "central": that marks a function worth a
# performance claim, while a hub's reach is dropped from validation, so it must
# be a module most tests would pass through. Fan-in counts every importer,
# tests included, as ``graph_metrics.in_degree`` stores it.
HUB_MIN_FAN_IN = 50
HUB_TOP_PERCENTILE = 0.99
HUB_FAN_IN_FLOOR = 10


def hub_files(in_degree: Mapping[str, float], test_files: Collection[str]) -> frozenset[str]:
    """The repository's own non-test files that are hubs by stored fan-in."""
    own = {
        path: int(fan_in or 0)
        for path, fan_in in in_degree.items()
        if not path.startswith("external:") and path not in test_files
    }
    bar = max(HUB_FAN_IN_FLOOR, _percentile_threshold(list(own.values()), HUB_TOP_PERCENTILE))
    return frozenset(
        path for path, fan_in in own.items() if fan_in > HUB_MIN_FAN_IN or fan_in >= bar
    )


def _siblings(paths: Collection[str]) -> dict[str, tuple[str, ...]]:
    by_dir: dict[str, list[str]] = {}
    for path in paths:
        if not path.startswith("external:"):
            by_dir.setdefault(path.rsplit("/", 1)[0] if "/" in path else "", []).append(path)
    return {directory: tuple(sorted(files)) for directory, files in by_dir.items()}


def target_symbol_ids(
    suggestion: RefactoringSuggestion,
    file_path: str,
    lines: set[int] | None,
    spans: Sequence[SymbolSpan],
) -> list[str]:
    """Graph ids of the symbols in *file_path* the plan changes.

    Named symbols first: a full id, a bare name, or ``Class.method``. When none
    name a symbol here, the innermost symbol enclosing the plan's lines, which
    is how a line-range target (``extract_helper``) finds its function.
    """
    ids = {span[0] for span in spans}
    found: set[str] = set()
    for name in affected_symbols(suggestion):
        candidates = (
            (name,)
            if "::" in name
            else (f"{file_path}::{name}", f"{file_path}::{name.replace('.', '::')}")
        )
        found.update(candidate for candidate in candidates if candidate in ids)
    if found or not lines:
        return sorted(found)
    first, last = min(lines), max(lines)
    enclosing = [
        (end - start, symbol)
        for symbol, start, end in spans
        if start <= first and last <= end and not symbol.endswith("::__module__")
    ]
    return [min(enclosing)[1]] if enclosing else []


def _symbol_label(symbol_id: str) -> str:
    return symbol_id.split("::", 1)[-1].replace("::", ".")


def _line_span(lines: set[int]) -> str:
    first, last = min(lines), max(lines)
    return f"line {first}" if first == last else f"lines {first}-{last}"


# Sort keys put a test with no call path behind every test with one: past the
# deepest call hop, an import of the file, then nothing but a name.
_IMPORT_ONLY = 2 * DEFAULT_CALL_DEPTH + 1
_NO_PATH = 2 * DEFAULT_CALL_DEPTH + 2

# The same few hundred test paths are classified once per plan they guard;
# the classifier is the hot spot of a whole-repository hydration.
_is_support = functools.lru_cache(maxsize=8192)(is_test_support_path)

RankedTest = tuple[tuple[int, ...], str]


# Test names are compared once per plan they guard, like the support check.
_names_test_for = functools.lru_cache(maxsize=65536)(names_test_for)


@dataclass(frozen=True, slots=True)
class _Target:
    """What a test's path is compared against: the file's name and directories."""

    path: str
    name: str
    dirs: list[str]
    siblings: tuple[str, ...]

    @classmethod
    def of(cls, file_path: str, evidence: ValidationEvidence) -> _Target:
        directory = file_path.rsplit("/", 1)[0] if "/" in file_path else ""
        return cls(
            file_path,
            file_path.rsplit("/", 1)[-1],
            file_path.split("/")[:-1],
            evidence.siblings.get(directory, ()),
        )

    def named(self, path: str) -> bool:
        """A test named for the file (:func:`names_test_for`)."""
        return _names_test_for(path, self.path, self.siblings)

    def shared(self, path: str) -> list[str]:
        test_dirs = set(path.split("/")[:-1])
        return [segment for segment in self.dirs if segment in test_dirs]


def _near_key(path: str, hits: set[int], named: bool, shared: list[str]) -> tuple[int, ...]:
    """Measured coverage of the changed lines, then a test named for the file,
    then one mirroring its directories. Graph distance only breaks ties after."""
    return (
        int(_is_support(path)),
        0 if hits else 1,
        -len(hits),
        0 if named else 1,
        -len(shared),
    )


def _rank_by_name(
    file_path: str, labels: set[str], covered: Mapping[str, set[int]], evidence: ValidationEvidence
) -> dict[str, RankedTest]:
    """:func:`_rank_target_tests` without the graph evidence a rank-only pass skips."""
    target = _Target.of(file_path, evidence)
    out: dict[str, RankedTest] = {}
    for label in labels:
        path = label.split("::", 1)[0]
        hits = covered.get(label) or set()
        out[label] = (_near_key(path, hits, target.named(path), target.shared(path)), "")
    return out


def _test_reason(
    *,
    name: str,
    lines: set[int] | None,
    hits: set[int],
    edited: bool,
    nearest: tuple[int, int, str] | None,
    named_import: str | None,
    reach: ReachDistance | None,
    imported: frozenset[str] | None,
    named: bool,
    shared: list[str],
) -> str:
    """The strongest single piece of evidence, in the order the key ranks it."""
    if hits:
        return f"covers {_line_span(hits)}" if lines is not None else f"covers {name}"
    if edited:
        return "edited by this plan"
    if nearest is not None and nearest[0] == 1:
        callers = -nearest[1]
        suffix = f" from {callers} test functions" if callers > 1 else ""
        return f"calls {_symbol_label(nearest[2])}{suffix}"
    if named_import is not None:
        return f"imports {_symbol_label(named_import)}"
    if nearest is not None:
        return f"reaches {_symbol_label(nearest[2])} in {nearest[0]} calls"
    if reach is not None:
        return f"calls into {name}" if reach.hops == 1 else f"reaches {name} in {reach.hops} calls"
    if imported is not None:
        return f"imports {name.rsplit('.', 1)[0]}"
    if named:
        return f"named for {name}"
    if shared:
        return f"shares {'/'.join(shared)}"
    return f"reaches {name}"


def _rank_target_tests(
    file_path: str,
    lines: set[int] | None,
    labels: set[str],
    covered: Mapping[str, set[int]],
    reached: ReachedBy | None,
    symbol_ids: Sequence[str],
    evidence: ValidationEvidence,
    *,
    plan_has_symbols: bool,
) -> dict[str, RankedTest]:
    """Each test's sort key and reason for one target file.

    Measured coverage of the changed lines first, then a test named for the
    file, then directory overlap (:func:`_near_key`); within those, a test
    naming the changed symbol (a direct call or an import of it), then fewer
    call hops to it, then more of the test's functions at that distance. Test
    support (a helper module) runs nothing on its own, so it goes last; a
    ``conftest.py`` was already replaced by the tests under it
    (:func:`_expand_scopes`).
    """
    target = _Target.of(file_path, evidence)
    name = target.name
    file_reach = reached.reach if reached is not None and reached.reach else {}
    imports = evidence.imports.get(file_path, {})
    out: dict[str, RankedTest] = {}
    for label in labels:
        path = label.split("::", 1)[0]
        hits = covered.get(label) or set()
        nearest = min(
            (
                (reach.hops, -reach.callers, symbol)
                for symbol in symbol_ids
                if (reach := evidence.symbol_reach.get(symbol, {}).get(path)) is not None
            ),
            default=None,
        )
        imported = imports.get(path)
        named_import = next(
            (
                symbol
                for symbol in symbol_ids
                if _symbol_label(symbol).split(".", 1)[0] in (imported or ())
            ),
            None,
        )
        reach = file_reach.get(path)
        # A test the plan itself edits (an importer a split rewrites) runs the
        # change by definition.
        edited = path == file_path
        if edited:
            distance, callers = 0, 0
        elif nearest is not None:
            distance, callers = nearest[0], -nearest[1]
        elif reach is not None:
            # A test that reaches the file but not the changed symbol ranks
            # behind every test that reaches the symbol.
            distance = reach.hops + (DEFAULT_CALL_DEPTH if plan_has_symbols else 0)
            callers = reach.callers
        else:
            distance, callers = (_IMPORT_ONLY if imported is not None else _NO_PATH), 0
        shared = target.shared(path)
        named = target.named(path)
        references = (
            edited or named_import is not None or (nearest is not None and nearest[0] == 1)
        )
        key = (
            *_near_key(path, hits, named, shared),
            0 if references else 1,
            distance,
            -callers,
        )
        out[label] = (
            key,
            _test_reason(
                name=name,
                lines=lines,
                hits=hits,
                edited=edited,
                nearest=nearest,
                named_import=named_import,
                reach=reach,
                imported=imported,
                named=named,
                shared=shared,
            ),
        )
    return out


def _commands(tests: list[str], *, total: int | None = None) -> list[str]:
    """A command that runs at least everything the plan says validates it.

    When the displayed list is capped, enumerating only the shown tests would
    read as a complete validation run while silently skipping the rest, so the
    selection widens to the files those tests live in: bounded by file count
    rather than test count, and never narrower than the evidence.

    An empty list means the plan has no command to suggest. Every command names
    its tests: with none found, a bare ``pytest`` or ``npm test`` would run a
    whole suite as if it guarded the change. Nothing here looks at the
    repository's tooling, so a language other than Python or JS/TS gets none in
    place of a guess that would fail when run.
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
    return commands


def _characterization_step(suggestion: RefactoringSuggestion) -> str:
    """The step before an edit no test reaches: pin today's behaviour first."""
    symbol = suggestion.target_symbol
    if not symbol or not symbol.replace(".", "_").isidentifier():
        symbol = suggestion.file_path.rsplit("/", 1)[-1]
    return f"No test reaches this; add a characterization test for `{symbol}` before the edit."


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


def _measured_hits(rows: list[dict[str, Any]], lines: set[int] | None) -> dict[str, set[int]]:
    """Measured tests and the lines of the target each one ran."""
    hits: dict[str, set[int]] = {}
    for row in rows:
        covered = set(row.get("covered_lines") or [])
        ran = covered if lines is None else lines & covered
        if not ran:
            continue
        label = row.get("test_id") or row.get("test_file")
        if isinstance(label, str) and label:
            hits.setdefault(label, set()).update(ran)
    return hits


def _measured_labels(rows: list[dict[str, Any]], lines: set[int] | None) -> set[str]:
    return set(_measured_hits(rows, lines))


def _hub_tests(
    file_path: str, symbol_ids: Sequence[str], evidence: ValidationEvidence
) -> set[str] | None:
    """For a hub file, the tests that may exercise the symbols the plan changes.

    Most of the suite reaches a hub through some other symbol of it, so its
    file-level reach says nothing about this change. A test counts when it
    reaches a changed symbol, imports one by name, or imports the module whole
    (``import utils``, ``import * as u``), whose calls the graph may not have
    resolved; only a test proven to import other names is left out. ``None``
    keeps the file's reach: it is no hub, or the plan names no symbol in it.
    """
    if file_path not in evidence.hubs or not symbol_ids:
        return None
    module = file_path.rsplit("/", 1)[-1].split(".", 1)[0]
    names = {_symbol_label(symbol).split(".", 1)[0] for symbol in symbol_ids} | {module, "*"}
    imported = evidence.imports.get(file_path, {})
    return {
        test for symbol in symbol_ids for test in evidence.symbol_reach.get(symbol, {})
    } | {test for test, imports in imported.items() if not imports or imports & names}


def _drop_hub_only(file_path: str, labels: set[str], keep: set[str]) -> set[str]:
    """A hub target's tests: :func:`_hub_tests`, which may name an importer the
    file walk never reached, and any of *labels* with the file's exact paired
    name. A qualified name (``test_utils_extra.py``) earns no exemption."""
    paired = paired_test_names(file_path)
    return set(keep) | {
        label for label in labels if label.split("::", 1)[0].rsplit("/", 1)[-1] in paired
    }


def _validation_target(
    file_path: str,
    lines: set[int] | None,
    measured: Mapping[str, list[dict[str, Any]]],
    inferred: Mapping[str, ReachedBy],
    cap: int,
    rank: Callable[[set[str], Mapping[str, set[int]], ReachedBy | None], dict[str, RankedTest]],
    keep: set[str] | None = None,
) -> tuple[ValidationTarget, set[str], bool, dict[str, RankedTest]]:
    covered = _measured_hits(measured.get(file_path, []), lines)
    labels = set(covered)
    reached = None
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
        if keep is not None and reached is not None:
            labels = _drop_hub_only(file_path, labels, keep)
            if identities_complete:
                # The whole list was filtered, so what is left is the answer.
                basis, via = ("inferred", via) if labels else ("unknown", None)
                total = len(labels)
    ranked = rank(labels, covered, reached)
    ordered = sorted(labels, key=lambda label: (ranked[label][0], label))
    return (
        ValidationTarget(
            file_path=file_path,
            basis=basis,
            via=via,
            total=total,
            tests=ordered[:cap],
            truncated=total > cap or not identities_complete,
        ),
        labels,
        identities_complete,
        ranked,
    )


def build_validation_plan(
    suggestion: RefactoringSuggestion,
    measured: Mapping[str, list[dict[str, Any]]],
    inferred: Mapping[str, ReachedBy],
    *,
    test_limit: int = DEFAULT_TEST_LIMIT,
    evidence: ValidationEvidence | None = None,
    order_tests: bool = True,
) -> ValidationPlan:
    """Resolve target evidence in strict measured/call/import precedence.

    Tests are ordered by how directly they exercise what the plan changes
    (:func:`_rank_target_tests`). A test reaching several of the plan's files
    keeps its best evidence, and ties go to the one reaching more of them.
    """
    cap = max(0, test_limit)
    facts = evidence or ValidationEvidence()
    ranges = sorted(_line_ranges(suggestion).items())
    symbols = {
        path: target_symbol_ids(suggestion, path, lines, facts.symbols.get(path, ()))
        for path, lines in ranges
    }
    plan_has_symbols = any(symbols.values())
    target_rows: list[ValidationTarget] = []
    best: dict[str, RankedTest] = {}
    by_file: dict[str, set[str]] = {}
    identities_complete = True
    for file_path, lines in ranges:

        def rank(
            labels: set[str],
            covered: Mapping[str, set[int]],
            reached: ReachedBy | None,
            path: str = file_path,
            span: set[int] | None = lines,
        ) -> dict[str, RankedTest]:
            if not order_tests:
                # A rank-only pass reads no graph evidence; name and directory still order.
                return _rank_by_name(path, labels, covered, facts)
            return _rank_target_tests(
                path,
                span,
                labels,
                covered,
                reached,
                symbols[path],
                facts,
                plan_has_symbols=plan_has_symbols,
            )

        target, labels, target_complete, ranked = _validation_target(
            file_path,
            lines,
            measured,
            inferred,
            cap,
            rank,
            _hub_tests(file_path, symbols[file_path], facts),
        )
        by_file[file_path] = labels
        for test, scored in ranked.items():
            if test not in best or scored[0] < best[test][0]:
                best[test] = scored
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
    reach_order = {test: index for index, test in enumerate(rank_tests_by_reach(by_file))}
    ordered_tests = sorted(best, key=lambda test: (best[test][0], reach_order[test]))
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
        commands=_commands(ordered_tests[:cap], total=aggregate_total),
        targets=target_rows,
        reasons={test: best[test][1] for test in ordered_tests[:cap]} if order_tests else {},
        prerequisite=(
            _characterization_step(suggestion) if aggregate_basis == "unknown" else None
        ),
    )


def with_step_verify(
    plan: ValidationPlan,
    suggestion: RefactoringSuggestion,
    measured: Mapping[str, list[dict[str, Any]]],
    inferred: Mapping[str, ReachedBy],
    *,
    test_limit: int = DEFAULT_TEST_LIMIT,
    evidence: ValidationEvidence | None = None,
) -> ValidationPlan:
    """*plan* with the ``verify`` of each step of a multi-step plan that differs.

    Each step is validated as a plan of its own file and span, over the same
    batch facts, so it picks its tests the way the plan does. Most steps get
    the plan's own answer, which detail already shows once, so only the rest
    are kept.
    """
    steps = _dict_list((suggestion.plan or {}).get("steps"))
    if len(steps) < 2:
        return plan
    facts = evidence or ValidationEvidence()
    own = verify_of(plan)
    verify: list[dict[str, Any] | None] = []
    for step in steps:
        scoped = _step_scope(suggestion, step, facts)
        check = (
            own
            if scoped is None
            else verify_of(
                build_validation_plan(
                    scoped, measured, inferred, test_limit=test_limit, evidence=facts
                )
            )
        )
        verify.append(None if check == own else check)
    if not any(verify):
        return plan
    return dataclasses.replace(plan, step_verify=verify)


_COVERAGE: dict[str, VerifyCoverage] = {
    "measured": "measured",
    "inferred": "inferred",
    # Some files measured, some only inferred: not every line is proven run.
    "mixed": "inferred",
    "unknown": "none",
}


_VERIFY_KEYS = frozenset({"commands", "tests", "coverage"})


def verify_of(validation: ValidationPlan) -> dict[str, Any]:
    """*validation* in the per-step ``verify`` shape."""
    return {
        "commands": validation.commands,
        "tests": validation.tests,
        "coverage": _COVERAGE[validation.basis],
    }


def _step_scope(
    suggestion: RefactoringSuggestion, step: Mapping[str, Any], facts: ValidationEvidence
) -> RefactoringSuggestion | None:
    """*suggestion* narrowed to the one file and span *step* edits.

    A step with no line (an edit to a whole symbol) takes that symbol's span
    from the graph, else the whole file. ``None`` when the step names no file.
    """
    path = step.get("file_path")
    if not isinstance(path, str) or not path:
        return None
    line = step.get("line") if isinstance(step.get("line"), int) else None
    symbol = step.get("symbol") if isinstance(step.get("symbol"), str) else ""
    scoped = dataclasses.replace(
        suggestion,
        file_path=path,
        target_symbol=symbol,
        line_start=line,
        line_end=line,
        # No steps, locations or blast files: the step's own file and lines only.
        plan={},
        blast_radius={},
        validation={},
    )
    if line is None and symbol:
        span = _symbol_span(scoped, facts)
        if span is not None:
            scoped.line_start, scoped.line_end = span
    return scoped


def _symbol_span(
    scoped: RefactoringSuggestion, facts: ValidationEvidence
) -> tuple[int, int] | None:
    """The graph span of the symbol *scoped* names in its file, if the graph has it."""
    spans = facts.symbols.get(scoped.file_path, ())
    named = set(target_symbol_ids(scoped, scoped.file_path, None, spans))
    return next(((start, end) for symbol_id, start, end in spans if symbol_id in named), None)


def steps_with_verify(steps: Sequence[Any], validation: ValidationPlan) -> list[dict[str, Any]]:
    """*steps*, those whose checks differ from the plan's carrying ``verify``.

    A step without one is checked by the plan-level validation: every step of
    a single-step plan, and of a row stored before steps had their own.
    """
    rows = _dict_list(list(steps))
    verify = validation.step_verify
    if len(verify) != len(rows):
        return rows
    return [
        {**step, "verify": check} if check else step
        for step, check in zip(rows, verify, strict=True)
    ]


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
    # The reads a rank-only pass made, so detailing a page reuses them.
    inputs: ValidationInputs | None = field(default=None, repr=False, compare=False)
    # What other layers say about the target (:mod:`.annotations`), stored at
    # finalize and served on plan detail only; ``None`` when never checked.
    annotations: PlanAnnotations | None = field(default=None, repr=False, compare=False)

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

    def detail_dict(self) -> dict[str, Any]:
        """:meth:`as_dict` for one plan read alone: a step whose checks differ
        from the plan's carries its ``verify``, and the plan carries its
        ``governed_by``, ``risks`` and co-change partners."""
        payload = self.as_dict()
        steps = payload["plan"].get("steps")
        if isinstance(steps, list) and self.validation.step_verify:
            payload["plan"] = {
                **payload["plan"],
                "steps": steps_with_verify(steps, self.validation),
            }
        notes = self.annotations
        if notes is None:
            # Never checked (an older or unfinalized row): absent, not "none found".
            return payload
        payload["governed_by"] = list(notes.governed_by)
        payload["risks"] = [risk.as_dict() for risk in notes.risks]
        if notes.co_change_partners:
            payload["blast_radius"] = {
                **payload["blast_radius"],
                "co_change_partners": partner_rows(notes.co_change_partners),
            }
        return payload

    def rank_facts(self) -> dict[str, Any]:
        """What ranking added to the stored plan, persisted once at finalize."""
        return {
            "benefit": self.benefit,
            "leverage": self.leverage,
            "cost": self.cost,
            "risk": self.risk,
            "rank_score": self.rank_score,
            "dependents": self.dependents,
            "file_nloc": self.file_nloc,
            "file_weighted_deficit": self.file_weighted_deficit,
            # Enriched with the caller rollup, which the stored column lacks.
            "blast_radius": self.suggestion.blast_radius or {},
            "validation": self.validation.as_dict(),
            **({"step_verify": self.validation.step_verify} if self.validation.step_verify else {}),
            **(
                {"annotations": self.annotations.as_dict()}
                if self.annotations is not None
                else {}
            ),
        }


_RANK_FACTS = (
    "benefit",
    "leverage",
    "cost",
    "risk",
    "rank_score",
    "dependents",
    "file_nloc",
    "file_weighted_deficit",
)


def stored_recommendation(row: Any) -> Recommendation | None:
    """The recommendation finalize persisted on *row*, or ``None`` when it has
    none or an incomplete one, so the caller rebuilds it instead."""
    from .serving import validation_from_profile

    facts = _loads_dict(_attr(row, "rank_json", None))
    if not isinstance(facts.get("validation"), dict) or any(k not in facts for k in _RANK_FACTS):
        return None
    try:
        validation = validation_from_profile(facts["validation"])
    except TypeError:
        return None
    stored_steps = facts.get("step_verify")
    validation = dataclasses.replace(
        validation,
        step_verify=[
            check if isinstance(check, dict) and check.keys() >= _VERIFY_KEYS else None
            for check in (stored_steps if isinstance(stored_steps, list) else [])
        ],
    )
    suggestion = rehydrate_suggestion(row)
    suggestion.blast_radius = dict(facts.get("blast_radius") or {})
    suggestion.validation = validation.as_dict()
    return Recommendation(
        suggestion=suggestion,
        validation=validation,
        annotations=PlanAnnotations.from_dict(facts.get("annotations")),
        **{name: facts[name] for name in _RANK_FACTS},
    )


#: Each bucket's place in the shared effort scale, for the ``effort`` sort.
EFFORT_RANK = {bucket: rank for rank, bucket in enumerate(EFFORT_ORDER)}
#: Where a bucket outside the scale sorts: with ``L``.
UNKNOWN_EFFORT = EFFORT_RANK["L"]

#: The plan list's filters, read in SQL on a ranked store
#: (``persistence.sql.rule_predicate``) and in memory on the live path
#: (``queue_rules.keep``), so the two cannot disagree.
PLAN_FILTERS = (
    FilterRule("refactoring_types", "refactoring_type", "in", "set"),
    FilterRule("file_path", "file_path", "eq", "set"),
    FilterRule("confidences", "confidence", "in", "truthy"),
    FilterRule("efforts", "effort_bucket", "in", "truthy"),
)

#: The plan list's field sorts; every one but ``file`` breaks ties by the view
#: order. ``effort`` reads :data:`EFFORT_RANK` and ``canonical`` is the view.
PLAN_SORTS: dict[str, SortKeys] = {
    "health": (("impact_delta", True),),
    "blast": (("blast_size", True),),
    "file": (("file_path", False), ("target_symbol", False), ("id", False)),
}


def plan_types(refactoring_type: str | None) -> tuple[str, ...] | None:
    """The plan types a list's ``refactoring_type`` names; ``structural`` is a lens."""
    if refactoring_type == "structural":
        return tuple(sorted(STRUCTURAL_TYPES))
    return (refactoring_type,) if refactoring_type else None


def matches_search(suggestion: RefactoringSuggestion, query: str) -> bool:
    """Whether lower-cased *query* occurs in the plan's searchable text."""
    plan = suggestion.plan or {}
    haystack = " ".join(
        (
            suggestion.file_path,
            suggestion.target_symbol,
            suggestion.refactoring_type,
            suggestion.source_biomarker,
            str(plan.get("strategy") or ""),
            str(plan.get("intervention_symbol") or ""),
        )
    ).lower()
    return query in haystack


def _priority_components(
    suggestion: RefactoringSuggestion,
    *,
    nloc: int,
    health_score: float,
    dependents: int,
    validation: ValidationPlan,
) -> tuple[float, float, float, float, float, int]:
    weighted_deficit = round(max(TARGET_SCORE - health_score, 0.0) * max(nloc, 1))
    benefit = detector_native_benefit(suggestion)
    entry_bonus = 0.5 if (suggestion.evidence or {}).get("reliable_entry_reachability") else 0.0
    leverage = 0.5 * math.log1p(weighted_deficit) + math.log1p(max(0, dependents)) + entry_bonus
    surface = max(blast_size(suggestion), max(0, len(affected_files(suggestion)) - 1))
    # Blast radius is charged once, in ``risk``. Cost is the work itself.
    cost = EFFORT_COST.get(suggestion.effort_bucket, 3.0)
    provenance = str((suggestion.evidence or {}).get("provenance") or "")
    weak_graph = 1.0 if provenance in _WEAK_PROVENANCE else 0.0
    # A test named for the file, with no edge proving it runs it, is weaker
    # evidence than a graph walk; price it between inferred and unknown.
    validation_risk = (
        1.0
        if validation.via == "name-match"
        else {"measured": 0.0, "mixed": 0.5, "inferred": 0.75, "unknown": 1.5}[validation.basis]
    )
    risk = surface_confidence_risk(surface, suggestion.confidence) + weak_graph + validation_risk
    # Benefit multiplies rather than offsets: leverage (the host file's
    # deficit and dependents) scales a real gain, and scales nothing when
    # there is none. A plan with no evidence of benefit scores 0 and cannot
    # outrank one that recovers health, however popular its file.
    priority = priority_score(benefit=benefit, uplift=leverage, cost=cost, risk=risk)
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
        raw_health_score = _attr(metric, "score", TARGET_SCORE)
        health_score = float(TARGET_SCORE if raw_health_score is None else raw_health_score)
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
    """Rank order, production files first: a plan on a build script, a tool or
    copied code comes after them, and a test plan last."""
    return sorted(
        recommendations,
        key=lambda recommendation: (
            ship_rank(recommendation.suggestion.file_path),
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
    rank_only: bool = False,
    step_verify: bool = False,
) -> list[Recommendation]:
    """Hydrate, enrich, validate, rank, and serialize-ready all *rows*.

    Query shape is constant in plan/test count: health metrics and graph metrics
    are bulk reads, measured coverage is one ``IN`` query, and inferred walks
    use their existing bounded level queries over the complete unanswered set.

    *rank_only* skips the symbol-level evidence that orders each plan's tests:
    rank and validation basis are unchanged, the test order falls back to name
    and directory. A paged surface ranks every row this way, then passes the
    rows it returns through :func:`detail_recommendations`.

    *step_verify* also validates each step of a multi-step plan
    (:func:`with_step_verify`). Finalize asks, so it is stored once; a live
    read never rebuilds it and serves the plan-level answer.
    """
    from repowise.core.persistence import crud

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
    plans, inputs = await _validation_plans(
        session,
        repository_id,
        suggestions,
        test_limit=test_limit,
        detailed=not rank_only,
        in_degree=centrality,
        step_verify=step_verify and not rank_only,
    )
    recommendations = build_recommendations(
        suggestions,
        metric_by_path={metric.file_path: metric for metric in metrics},
        centrality=centrality,
        validations=dict(enumerate(plans)),
    )
    if rank_only:
        recommendations = [dataclasses.replace(item, inputs=inputs) for item in recommendations]
    return apply_view(recommendations, view)


async def detail_recommendations(
    session: AsyncSession,
    repository_id: str,
    recommendations: Sequence[Recommendation],
    *,
    test_limit: int = DEFAULT_TEST_LIMIT,
) -> list[Recommendation]:
    """*recommendations* with their tests ordered by full evidence, same order.

    Only the validation is rebuilt; rank, benefit and the rest come from the
    pass that ranked them, so a page detailed here matches a full hydration.
    """
    if not recommendations:
        return []
    suggestions = [item.suggestion for item in recommendations]
    shared = {id(item.inputs): item.inputs for item in recommendations}
    plans, _ = await _validation_plans(
        session,
        repository_id,
        suggestions,
        test_limit=test_limit,
        detailed=True,
        inputs=next(iter(shared.values())) if len(shared) == 1 else None,
    )
    out = []
    for item, plan in zip(recommendations, plans, strict=True):
        item.suggestion.validation = plan.as_dict()
        out.append(dataclasses.replace(item, validation=plan))
    return out


async def _validation_plans(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    *,
    test_limit: int,
    detailed: bool,
    inputs: ValidationInputs | None = None,
    in_degree: Mapping[str, float] | None = None,
    step_verify: bool = False,
) -> tuple[list[ValidationPlan], ValidationInputs]:
    """One validation plan per suggestion, every read batched across the set.

    *inputs* from an earlier pass over a superset of these suggestions skips
    the coverage read and the reachability walk; only the symbol evidence is
    read again, and only for these suggestions.
    """
    target_files = sorted(
        {path for suggestion in suggestions for path in affected_files(suggestion)}
    )
    if inputs is None:
        inputs = await _validation_inputs(
            session, repository_id, suggestions, target_files, in_degree=in_degree
        )
    evidence = (
        await _validation_evidence(
            session, repository_id, suggestions, target_files, inputs.test_files, inputs.evidence
        )
        if detailed
        else inputs.evidence
    )
    plans = [
        build_validation_plan(
            suggestion,
            inputs.measured,
            inputs.inferred,
            test_limit=test_limit,
            evidence=evidence,
            order_tests=detailed,
        )
        for suggestion in suggestions
    ]
    if step_verify:
        plans = [
            with_step_verify(
                plan,
                suggestion,
                inputs.measured,
                inputs.inferred,
                test_limit=test_limit,
                evidence=evidence,
            )
            for plan, suggestion in zip(plans, suggestions, strict=True)
        ]
    return plans, inputs


async def _validation_inputs(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    target_files: Sequence[str],
    *,
    in_degree: Mapping[str, float] | None = None,
) -> ValidationInputs:
    """Measured coverage and the tiered reachability walk for every plan's files.

    *in_degree* is the stored fan-in a caller already read; without it, read here.
    """
    from repowise.core.persistence import crud
    from repowise.core.persistence.crud.analysis.coverage_map import tests_covering_files

    measured = await tests_covering_files(session, repository_id, set(target_files))

    # A measured row only answers a target when it intersects the plan's line
    # range (if one exists).  Seed inference once with every file that remains
    # unanswered for at least one plan.
    unanswered: set[str] = set()
    for suggestion in suggestions:
        for file_path, lines in _line_ranges(suggestion).items():
            if not _measured_labels(measured.get(file_path, []), lines):
                unanswered.add(file_path)
    test_files = await cached_test_files(session, repository_id)
    if in_degree is None:
        in_degree = {
            node: float(metric.get("in_degree") or 0.0)
            for node, metric in (await crud.get_graph_metrics(session, repository_id)).items()
        }
    # A test that reaches the file only through a hub tests the hub; it is not
    # listed, and with nothing else the plan says no test reaches it.
    hubs = hub_files(in_degree, test_files)
    inferred = (
        await tests_reaching_by_tier(
            session, repository_id, sorted(unanswered), test_files=test_files, avoid=hubs
        )
        if unanswered
        else {}
    )
    unreached = sorted(unanswered - inferred.keys())
    if unreached:
        inferred.update(tests_matching_by_name(unreached, test_files))
    walked = {path: reached.all_tests or tuple(reached.tests) for path, reached in inferred.items()}
    narrowed = await narrow_scopes(session, repository_id, walked, test_files)
    inferred = {
        path: _expand_scopes(path, reached, narrowed[path]) for path, reached in inferred.items()
    }
    hub_targets = [path for path in target_files if path in hubs]
    evidence = await _validation_evidence(
        session,
        repository_id,
        [item for item in suggestions if hubs.intersection(affected_files(item))],
        hub_targets,
        test_files,
        ValidationEvidence(hubs=hubs, siblings=_siblings(in_degree)),
    )
    return ValidationInputs(
        measured=measured, inferred=inferred, test_files=test_files, evidence=evidence
    )


def _expand_scopes(path: str, reached: ReachedBy, expanded: list[str]) -> ReachedBy:
    """*reached* with a conftest or test package it stopped at replaced by *expanded*.

    *expanded* is :func:`~repowise.core.analysis.test_collection.narrow_scopes`'
    answer: a conftest reached only through its imports stands for the tests
    the selection narrows it to, any other scope for the tests under it.

    A validation command must name tests a runner collects; ``pytest
    tests/conftest.py`` runs nothing. A scope with no runnable test under it
    drops out, so a target reached only through one can end up unknown.

    A root conftest stands for every test in the repository, so the expansion
    is ranked nearest-first and capped like the walk's own list. ``all_tests``
    is cleared rather than left uncapped: plan ranking then scores at most the
    cap per target, and ``total`` keeps the true count, so a capped target
    reads as incomplete instead of as the whole answer.
    """
    found = reached.all_tests or tuple(reached.tests)
    if tuple(expanded) == tuple(found):
        return reached
    ranked = rank_tests(path, expanded)
    return dataclasses.replace(
        reached,
        tests=ranked[:MAX_TESTS_PER_TARGET],
        total=len(ranked),
        all_tests=None,
    )


async def _validation_evidence(
    session: AsyncSession,
    repository_id: str,
    suggestions: Sequence[RefactoringSuggestion],
    target_files: Sequence[str],
    test_files: set[str],
    base: ValidationEvidence,
) -> ValidationEvidence:
    """Symbol spans, symbol-level reach and test imports for every plan at once.

    Bounded reads over the whole plan set (the symbol walk is one ``IN`` query
    per hop), so adding plans adds no statements.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import GraphNode

    if not target_files:
        return base
    rows = await session.execute(
        select(GraphNode.file_path, GraphNode.node_id, GraphNode.start_line, GraphNode.end_line)
        .where(GraphNode.repository_id == repository_id)
        .where(GraphNode.node_type == "symbol")
        .where(GraphNode.file_path.in_(list(target_files)))
    )
    spans: dict[str, list[SymbolSpan]] = {}
    for file_path, node_id, start, end in rows:
        spans.setdefault(file_path, []).append((node_id, int(start or 0), int(end or 0)))
    symbol_ids = {
        symbol
        for suggestion in suggestions
        for file_path, lines in _line_ranges(suggestion).items()
        for symbol in target_symbol_ids(suggestion, file_path, lines, spans.get(file_path, ()))
    }
    return dataclasses.replace(
        base,
        symbols=spans,
        symbol_reach=await reach_into_symbols(
            session, repository_id, symbol_ids, test_files, avoid=base.hubs
        ),
        imports=await imported_names_by_test(session, repository_id, target_files, test_files),
    )


def serialize_recommendations(
    recommendations: Sequence[Recommendation],
) -> list[dict[str, Any]]:
    return [recommendation.as_dict() for recommendation in recommendations]


__all__ = [
    "CONFIDENCE_RISK",
    "DEFAULT_TEST_LIMIT",
    "EFFORT_COST",
    "EFFORT_RANK",
    "PLAN_FILTERS",
    "PLAN_SORTS",
    "UNKNOWN_EFFORT",
    "Recommendation",
    "RecommendationView",
    "ValidationEvidence",
    "ValidationPlan",
    "ValidationTarget",
    "VerifyCoverage",
    "affected_files",
    "affected_symbols",
    "apply_view",
    "blast_size",
    "build_recommendations",
    "build_validation_plan",
    "canonical_order",
    "detail_recommendations",
    "detector_native_benefit",
    "enrich_blast_radius",
    "hub_files",
    "hydrate_recommendations",
    "matches_search",
    "plan_types",
    "priority_score",
    "rehydrate_suggestion",
    "serialize_recommendations",
    "steps_with_verify",
    "stored_recommendation",
    "surface_confidence_risk",
    "target_symbol_ids",
    "verify_of",
    "with_step_verify",
]
