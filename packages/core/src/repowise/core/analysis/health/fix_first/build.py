"""Build the Fix-first queue from plain rows, with no store behind them.

Every input is a sequence of rows, a row being a mapping or any object with the
named attributes (``health.rows.field``). The SQL loader in
``repowise.core.persistence.crud.analysis.fix_first`` narrows its reads and
hands the rows here; a caller holding the same rows in memory calls
:func:`build_fix_first` directly. The builder applies the whole rule, so a
caller may pass unfiltered rows.

Row shapes (field names are the SQL columns):

``metrics``
    ``file_path``, ``score``, ``nloc``, ``is_test``, ``analyzed_commit``,
    ``updated_at``, plus ``commit_count_90d`` (git) and ``dependents`` (graph
    in-degree).
``findings``
    ``file_path``, ``biomarker_type``, ``severity``, ``function_name``,
    ``line_start``, ``line_end``, ``reason``, ``health_impact``, ``public_id``,
    ``dimension``, ``status`` (absent = open).
``refactoring``
    The ``refactoring_opportunities`` columns, ``details`` (or
    ``details_json``) carrying ``steps``, ``validation_profiles``,
    ``dependents`` and ``lead_finding_ids``. A row under the minimum worth may
    omit its details.
``performance``
    The ``performance_opportunities`` columns, ``details`` (or
    ``details_json``) carrying ``facets``, ``plan`` and ``fix_rationale``. A
    row with no plan, or an expected one, may omit its details.
``plans``
    Extract-method plan rows named by a refactoring step: ``public_id``,
    ``evidence`` / ``evidence_json`` (``slice_nloc``) and ``plan`` /
    ``plan_json`` (``span``). A step whose plan is absent is kept.
"""

from __future__ import annotations

import functools
import math
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from repowise.core.analysis.finding_registry import excluded_types
from repowise.core.analysis.health.models import primary_finding, split_by_origin
from repowise.core.analysis.health.perf.causal import execution_context
from repowise.core.analysis.health.refactoring.extract_helper import _is_generated_path
from repowise.core.analysis.health.rows import detail_map, field, json_field
from repowise.core.analysis.health.scoring import biomarker_dimension
from repowise.core.analysis.health.suggestions import suggestion_for

from . import text
from .model import (
    EFFORT_RANK,
    FIX_EFFORTS,
    FIX_EXCLUSIONS,
    FIX_IMPROVES,
    LEVEL_RANK,
    TIER_RANK,
    FixAction,
    FixConfidence,
    FixContext,
    FixEffortEstimate,
    FixFact,
    FixFirstQueue,
    FixGain,
    FixItem,
    FixNextCall,
    FixRankFact,
    FixRisk,
    FixSource,
    FixStep,
    FixTarget,
    FixTest,
    FixTotals,
    FixVerify,
    fix_id,
)

Rows = Iterable[Any]

#: A refactoring whose credited gain is under this is not worth an item.
#: Ceiling: a stand-in until refactoring benefit is proportional to the
#: complexity removed; then the floor moves into that model.
MIN_WORTH = 0.5
#: Extract-method steps lifting fewer statement lines than this are dropped.
MIN_SLICE_NLOC = 5
#: Credited health gain cut points for value 1, 2 and 3.
GAIN_CUTS = (0.5, 1.5, 3.0)
#: A file in the top fifth of production files by churn or dependents is hot.
HOT_QUANTILE = 0.8
MAX_FACTS = 5
MAX_TESTS = 5
MAX_STEPS = 5
MAX_CONTEXT = 3
#: In the first HEAD places no kind takes more than HEAD_PER_KIND, unless the
#: other kinds have nothing at value 2 or above.
HEAD = 5
HEAD_PER_KIND = 3
DEFAULT_LIMIT = 10


@dataclass(frozen=True, slots=True)
class _Unit:
    item: FixItem
    value: int
    score: float


_VENDORED = frozenset({"vendor", "third_party", "thirdparty", "node_modules"})


def _open(row: Any) -> bool:
    return (field(row, "status") or "open") == "open"


def _num(value: Any) -> float:
    return float(value or 0.0)


#: The shared execution-context rule, memoised: it is pure on the path and
#: its test check dominates a cold build.
_context = functools.lru_cache(maxsize=65536)(execution_context)


def _path_exclusion(path: str, is_test: bool | None, context: str | None = None) -> str | None:
    """Why a file is out of the queue, or ``None`` when it ships.

    The performance pillar's execution context decides test and tooling for
    every kind of item; the stored ``is_test`` flag also marks a test.
    ``context`` is a stored context, when the row carries one.
    """
    ctx = context or _context(path)
    if is_test or ctx == "test":
        return "test"
    parts = set(path.lower().split("/")[:-1])
    if _is_generated_path(path) or parts & _VENDORED:
        return "generated"
    # ``unknown`` under a directory is docs, examples or demos: code that does
    # not ship. A root-level file stays eligible.
    if ctx == "tooling" or (ctx == "unknown" and parts):
        return "tooling"
    return None


def _quantile_cut(values: list[int]) -> float:
    """The smallest value in the top fifth, never below 1 (zero is not hot)."""
    if not values:
        return math.inf
    ordered = sorted(values)
    return max(ordered[min(int(len(ordered) * HOT_QUANTILE), len(ordered) - 1)], 1)


def _gain_value(gain: float, hot: bool) -> int:
    return min(3, sum(gain >= cut for cut in GAIN_CUTS) + int(hot))


def _risk(files_touched: int, dependents: int | None) -> FixRisk:
    deps = dependents or 0
    if files_touched >= 5 or deps >= 20:
        level = "high"
    elif files_touched >= 2 or deps >= 5:
        level = "medium"
    else:
        level = "low"
    parts = [f"touches {text.plural(files_touched, 'file')}"]
    if dependents:
        parts.append(f"{text.plural(dependents, 'file')} import it")
    return FixRisk(level, dependents, files_touched, "; ".join(parts).capitalize() + ".")


def _verify(profile: Mapping[str, Any] | None) -> FixVerify:
    """The stored validation profile, shown short. Never recomputed here."""
    if not profile:
        return FixVerify((), 0, None, "unknown")
    via = profile.get("via")
    reason = f"reaches the changed code through the {text.humanize(via)}" if via else "covers it"
    tests = tuple(FixTest(str(t), reason) for t in (profile.get("tests") or [])[:MAX_TESTS])
    commands = profile.get("commands") or []
    basis = profile.get("basis")
    return FixVerify(
        tests,
        int(profile.get("total") or len(tests)),
        commands[0] if commands else None,
        basis if basis in ("measured", "inferred") else "unknown",
    )


class _Files:
    """Per-file facts the units share: metrics, heat, history context."""

    def __init__(self, metrics: Rows, history: Mapping[str, list[Any]]) -> None:
        self.by_path = {field(m, "file_path"): m for m in metrics}
        production = [m for m in self.by_path.values() if not field(m, "is_test")]
        self.churn_cut = _quantile_cut([field(m, "commit_count_90d") or 0 for m in production])
        self.deps_cut = _quantile_cut([field(m, "dependents") or 0 for m in production])
        self.history = history

    def is_test(self, path: str) -> bool | None:
        """The stored flag; ``None`` when the file has no metric row or flag."""
        return field(self.by_path.get(path), "is_test")

    def commits(self, path: str) -> int:
        return int(field(self.by_path.get(path), "commit_count_90d") or 0)

    def dependents(self, path: str) -> int | None:
        return field(self.by_path.get(path), "dependents")

    def nloc(self, path: str) -> int | None:
        return field(self.by_path.get(path), "nloc")

    def hot(self, path: str) -> bool:
        return self.commits(path) >= self.churn_cut or (self.dependents(path) or 0) >= self.deps_cut

    def matters(self, path: str, dependents: int | None = None) -> str:
        """Why the problem matters here, from the file's own exposure."""
        deps = dependents if dependents is not None else self.dependents(path)
        commits = self.commits(path)
        if self.hot(path) and deps and deps >= self.deps_cut:
            return f"{text.plural(deps, 'file')} import it, so every caller inherits the risk"
        if self.hot(path) and commits:
            return f"it changed {text.plural(commits, 'time')} in 90 days, so the cost recurs"
        return "fixing it makes the file easier to change safely"

    def context(self, path: str) -> tuple[FixContext, ...]:
        """History is context: shown beside the item, never ranked on."""
        out: list[FixContext] = []
        commits = self.commits(path)
        if commits:
            out.append(FixContext("changes in 90 days", str(commits)))
        if self.is_test(path):
            out.append(FixContext("file kind", "test"))
        ranked = sorted(self.history.get(path, ()), key=lambda f: -_num(field(f, "health_impact")))
        for f in ranked:
            out.append(
                FixContext(text.humanize(field(f, "biomarker_type") or ""), field(f, "reason") or "")
            )
        return tuple(out[:MAX_CONTEXT])

    def common_facts(self, path: str, dependents: int | None = None) -> list[FixFact]:
        out = []
        deps = dependents if dependents is not None else self.dependents(path)
        if deps:
            out.append(FixFact("files that import it", str(deps)))
        commits = self.commits(path)
        if commits:
            out.append(FixFact("changes in 90 days", str(commits)))
        return out


def _tier(value: int, confidence: str, ready: bool) -> tuple[str, str]:
    if value >= 2 and LEVEL_RANK.get(confidence, 0) >= 1 and ready:
        return "now", "worth doing, and the plan is safe to start"
    if value >= 2:
        return "next", "worth doing; the fix needs judgment"
    return "later", "smaller payoff"


def _finish(
    *,
    kind: str,
    source_id: str,
    value: int,
    ready: bool,
    score: float,
    rank_inputs: list[FixRankFact],
    **fields: Any,
) -> _Unit:
    tier, why_tier = _tier(value, fields["confidence"].level, ready)
    item = FixItem(
        id=fix_id(kind, source_id),
        rank=0,
        tier=tier,
        kind=kind,
        why_ranked=(
            FixRankFact("value", str(value)),
            *rank_inputs,
            FixRankFact("tier", why_tier),
        ),
        **fields,
    )
    return _Unit(item, value, score)


# --- refactoring ----------------------------------------------------------------


def _slice_nloc(plan: Any) -> int | None:
    evidence = json_field(plan, "evidence", None) or json_field(plan, "evidence_json", {})
    value = evidence.get("slice_nloc") if isinstance(evidence, dict) else None
    return int(value) if isinstance(value, (int, float)) else None


def _span(plan: Any) -> tuple[int | None, int | None]:
    body = json_field(plan, "plan", None) or json_field(plan, "plan_json", {})
    span = body.get("span") if isinstance(body, dict) else None
    if isinstance(span, dict):
        return span.get("start"), span.get("end")
    return None, None


def _refactor_step(order: int, step: Mapping[str, Any], plan: Any) -> FixStep:
    kind = step.get("refactoring_type") or ""
    path = step.get("file_path") or ""
    sym = text.short_symbol(step.get("target_symbol")) or text.basename(path)
    start, end = _span(plan) if plan is not None else (None, None)
    if kind == "extract_method":
        where = f"lines {start}-{end} of {sym}" if start and end else f"part of {sym}"
        line_text = f"Extract {where} into a helper"
    elif kind == "extract_helper":
        line_text = f"Replace the duplicate in {sym} with a call to one shared helper"
    elif kind == "extract_class":
        line_text = f"Move a cohesive group of {sym}'s methods into a new class"
    elif kind == "split_file":
        line_text = f"Split {text.basename(path)} along its independent groups"
    elif kind == "break_cycle":
        line_text = f"Break the import cycle at {text.basename(path)}"
    elif kind == "move_method":
        line_text = f"Move {sym} to the class it uses most"
    else:
        line_text = f"Apply the {text.humanize(kind)} step to {sym}"
    mechanical = (step.get("applicability") or {}).get("classification") == "mechanical"
    return FixStep(order, line_text, path, start or step.get("line_start"), mechanical)


def _refactor_unit(
    row: Any,
    details: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
    gain: float,
    plans: Mapping[str, Any],
    files: _Files,
) -> _Unit:
    path = field(row, "file_path")
    lead = steps[0]
    lead_type = lead.get("refactoring_type") or field(row, "lead_refactoring_type") or ""
    sym = text.short_symbol(lead.get("target_symbol")) or text.basename(path)
    marker = field(row, "lead_biomarker") or (
        lead_type if lead_type in ("split_file", "break_cycle") else None
    )
    title = text.REFACTOR_TITLE.get(lead_type, "Refactor {file}").format(
        sym=sym, file=text.basename(path)
    )
    if len(steps) > 1:
        title += f" ({len(steps)} steps)"
    dependents = details.get("dependents")
    if dependents is None:
        dependents = files.dependents(path)
    fix_steps = tuple(
        _refactor_step(i + 1, s, plans.get(s.get("plan_id"))) for i, s in enumerate(steps)
    )
    mechanical_n = sum(s.mechanical for s in fix_steps)
    mechanical = mechanical_n == len(fix_steps)
    confidence = field(row, "confidence") or "medium"
    hot = files.hot(path)
    facts = [
        FixFact("health recoverable", f"+{gain:.1f}", "inferred"),
        FixFact("steps", f"{len(steps)} ({mechanical_n} mechanical)"),
        *files.common_facts(path, dependents),
    ]
    nloc = files.nloc(path)
    if nloc:
        facts.append(FixFact("file size", f"{nloc} lines"))
    profiles = {p.get("id"): p for p in details.get("validation_profiles") or []}
    profile = profiles.get(lead.get("validation_profile_id")) or next(iter(profiles.values()), None)
    effort = field(row, "effort_bucket") or "M"
    dimension = biomarker_dimension(marker) if marker else "maintainability"
    plan_ids = tuple(s.get("plan_id") for s in steps if s.get("plan_id"))
    return _finish(
        kind="refactor",
        source_id=field(row, "opportunity_id"),
        value=_gain_value(gain, hot),
        ready=mechanical or confidence == "high",
        score=_num(field(row, "rank_score")),
        rank_inputs=[
            FixRankFact("health gain", f"{gain:.2f}"),
            FixRankFact("hot file", "yes" if hot else "no"),
        ],
        improves=dimension if dimension in FIX_IMPROVES else "maintainability",
        title=text.clip(title),
        target=FixTarget(path, sym if lead.get("target_symbol") else None,
                         fix_steps[0].line, lead.get("line_end")),
        why=f"{text.problem(marker, sym)}; {files.matters(path, dependents)}.",
        facts=tuple(facts[:MAX_FACTS]),
        action=FixAction(
            f"{text.plural(len(steps), 'step')}, {mechanical_n} mechanical",
            fix_steps[:MAX_STEPS],
            len(fix_steps),
            mechanical,
        ),
        gain=FixGain("health_points", round(gain, 3), text.health_gain(gain, ceiling=False)),
        effort=FixEffortEstimate(effort if effort in FIX_EFFORTS else "M", "sized by the stored plan"),
        risk=_risk(int(field(row, "affected_files_total") or 1), dependents),
        confidence=FixConfidence(
            confidence if confidence in LEVEL_RANK else "medium",
            f"{mechanical_n} of {text.plural(len(steps), 'step')} proven mechanical by the plan",
        ),
        verify=_verify(profile),
        context=files.context(path),
        source=FixSource(
            field(row, "opportunity_id"),
            plan_ids,
            tuple(details.get("lead_finding_ids") or ()),
        ),
        next_call=FixNextCall("get_health", {"opportunity_id": field(row, "opportunity_id")}),
    )


def _credited_steps(
    row: Any, details: Mapping[str, Any], plans: Mapping[str, Any]
) -> tuple[list[Mapping[str, Any]], float]:
    """The steps worth doing and the gain they credit (trivial extractions drop)."""
    steps = list(details.get("steps") or [])
    kept, dropped = [], 0.0
    for s in steps:
        plan = plans.get(s.get("plan_id"))
        size = _slice_nloc(plan) if plan is not None else None
        if s.get("refactoring_type") == "extract_method" and size is not None and size < MIN_SLICE_NLOC:
            dropped += _num(s.get("impact_delta"))
        else:
            kept.append(s)
    return kept, max(_num(field(row, "recoverable_health")) - dropped, 0.0)


# --- performance ----------------------------------------------------------------


def _perf_ready(row: Any) -> bool:
    return (
        field(row, "actionability_state") != "expected"
        and field(row, "plan_state") == "available"
        and bool(field(row, "fix_strategy"))
    )


def _perf_value(row: Any, facets: Mapping[str, Any]) -> int:
    production = field(row, "execution_context") == "production"
    grows = facets.get("loop_magnitude") == "grows_with_data"
    unknown = facets.get("loop_magnitude") in (None, "unknown")
    if (
        production
        and facets.get("exposure") == "entry_reachable"
        and grows
        and field(row, "boundary_kind") in ("db", "network")
    ):
        return 3
    if production and (grows or unknown):
        return 2
    return 1


def _perf_unit(rows: list[Any], files: _Files) -> _Unit:
    lead = min(rows, key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")))
    details = detail_map(lead)
    facets = details.get("facets") or {}
    plan = details.get("plan") or {}
    path = field(lead, "file_path") or ""
    symbol = field(lead, "intervention_symbol")
    name = text.short_symbol(symbol) or text.basename(path)
    noun = text.BOUNDARY_NOUN.get(field(lead, "boundary_kind") or "")
    call_sites = int(field(lead, "affected_call_sites_total") or 0)
    files_n = int(field(lead, "affected_files_total") or 1)
    amplification = facets.get("amplification")
    magnitude = facets.get("loop_magnitude")
    exposure = facets.get("exposure")
    call = f"{noun} call" if noun else "costly call"

    if noun and call_sites > 1:
        title = f"Batch the {noun} calls loops make through {name}"
    elif noun:
        title = f"Move the {noun} call in {name} out of its loop"
    else:
        title = f"Fix the repeated work in {name}"

    if amplification == "quadratic":
        what, gain_text = (
            f"{name} runs nested loops over the same data",
            "nested loop work that grows with the square of the data",
        )
    elif amplification == "per_call":
        what, gain_text = f"{name} repeats a {call} on every call", f"one fewer {call} per call"
    else:
        what = f"{name} makes a {call} once per loop iteration"
        gain_text = f"one {call} per loop iteration" + (
            ", grows with the data"
            if magnitude == "grows_with_data"
            else ", bounded by a fixed loop"
            if magnitude == "bounded"
            else "; loop size unknown"
        )
    reach = []
    if call_sites > 1:
        reach.append(f"{call_sites} call sites reach it")
    if exposure == "entry_reachable":
        reach.append("an entry point reaches it")
    if magnitude == "grows_with_data":
        reach.append("the loop grows with the data")

    facts = [
        FixFact("call sites", str(call_sites)),
        FixFact("files", str(files_n)),
        FixFact(
            "reachable from an entry point",
            {"entry_reachable": "Yes", "not_entry_reachable": "No"}.get(exposure or "", "Unknown"),
            "inferred" if exposure in ("entry_reachable", "not_entry_reachable") else "unknown",
        ),
        FixFact(
            "loop size",
            text.humanize(magnitude) if magnitude and magnitude != "n/a" else "unknown",
            "inferred" if magnitude in ("grows_with_data", "bounded") else "unknown",
        ),
    ]
    sinks = sorted({field(r, "terminal_sink") for r in rows if field(r, "terminal_sink")})
    if len(sinks) > 1:
        facts.append(FixFact("sinks this fix covers", str(len(sinks))))
    steps = tuple(
        FixStep(
            int(s.get("order") or i + 1),
            s.get("action", "")
            + (f" ({text.short_symbol(s.get('symbol'))})" if s.get("symbol") else ""),
            s.get("file_path") or path,
            s.get("line"),
            s.get("applicability") == "mechanical",
        )
        for i, s in enumerate(plan.get("steps") or [])
    )
    mechanical = bool(steps) and all(s.mechanical for s in steps)
    confidence = facets.get("actionability_confidence") or "low"
    ready = field(lead, "actionability_state") == "plan_ready" or field(lead, "fix_safety") == "proven"
    effort = plan.get("effort_bucket")
    value = _perf_value(lead, facets)
    strategy = field(lead, "fix_strategy") or ""
    return _finish(
        kind="perf_fix",
        source_id=f"{path}::{symbol or path}",
        value=value,
        ready=ready or mechanical,
        score=_num(field(lead, "rank_score")),
        rank_inputs=[
            FixRankFact("runs in", field(lead, "execution_context") or "unknown"),
            FixRankFact("entry reachable", exposure or "unknown"),
            FixRankFact("loop size", magnitude or "unknown"),
            FixRankFact("boundary", field(lead, "boundary_kind") or "none"),
        ],
        improves="performance",
        title=text.clip(title),
        target=FixTarget(path, name if symbol else None),
        why=f"{what}; {', '.join(reach) or 'the loop size is unknown'}.",
        facts=tuple(facts[:MAX_FACTS]),
        action=FixAction(
            text.FIX_STRATEGY.get(strategy, text.humanize(strategy).capitalize()),
            steps[:MAX_STEPS],
            len(steps),
            mechanical,
        ),
        gain=FixGain("performance", None, gain_text),
        effort=FixEffortEstimate(
            effort if effort in FIX_EFFORTS else "M",
            "sized by the stored plan" if effort else "not sized; the plan has no estimate",
        ),
        risk=_risk(files_n, None),
        confidence=FixConfidence(
            confidence if confidence in LEVEL_RANK else "low",
            details.get("fix_rationale") or f"{text.humanize(field(lead, 'actionability_state'))} plan",
        ),
        verify=_verify(plan.get("validation")),
        context=files.context(path),
        source=FixSource(field(lead, "opportunity_id")),
        next_call=FixNextCall("get_health", {"opportunity_id": field(lead, "opportunity_id")}),
    )


# --- findings with no plan ----------------------------------------------------------


def _finding_unit(lead: Any, files: _Files) -> _Unit:
    path = field(lead, "file_path")
    marker = field(lead, "biomarker_type") or ""
    function = field(lead, "function_name")
    where = function or text.basename(path)
    impact = _num(field(lead, "health_impact"))
    hot = files.hot(path)
    summary = text.first_sentence(suggestion_for(marker))
    line = field(lead, "line_start")
    public_id = field(lead, "public_id")
    dimension = biomarker_dimension(marker)
    return _finish(
        kind="finding",
        source_id=public_id or f"{path}::{marker}::{function or ''}",
        value=_gain_value(impact, hot),
        ready=False,
        score=impact,
        rank_inputs=[
            FixRankFact("health gain", f"{impact:.2f}"),
            FixRankFact("hot file", "yes" if hot else "no"),
        ],
        improves=dimension if dimension in FIX_IMPROVES else "defect",
        title=text.clip(
            text.FINDING_TITLE.get(marker, "Address the finding in {where}").format(where=where)
        ),
        target=FixTarget(path, function, line, field(lead, "line_end")),
        why=f"{text.problem(marker, where)}; {files.matters(path)}.",
        facts=tuple(
            [
                FixFact("finding", marker),
                FixFact("severity", field(lead, "severity") or "unknown"),
                *files.common_facts(path),
            ][:MAX_FACTS]
        ),
        action=FixAction(summary, (FixStep(1, summary, path, line, False),), 1, False),
        gain=FixGain("health_points", round(impact, 3), text.health_gain(impact, ceiling=True)),
        effort=FixEffortEstimate("M", "not sized; no stored plan covers this finding"),
        risk=_risk(1, files.dependents(path)),
        confidence=FixConfidence(
            "medium", "Measured from the code; no stored plan has checked a fix."
        ),
        verify=_verify(None),
        context=files.context(path),
        source=FixSource(None, (), (public_id,) if public_id else ()),
        next_call=FixNextCall("get_health", {"targets": [path], "include": ["biomarkers"]}),
    )


# --- order ------------------------------------------------------------------------


def _order(units: list[_Unit]) -> list[_Unit]:
    ranked = sorted(
        units,
        key=lambda u: (
            TIER_RANK[u.item.tier],
            -u.value,
            -LEVEL_RANK.get(u.item.confidence.level, 0),
            EFFORT_RANK.get(u.item.effort.bucket, 1),
            -u.score,
            u.item.id,
        ),
    )
    head: list[_Unit] = []
    taken: Counter[str] = Counter()
    while ranked and len(head) < HEAD:
        pick = ranked[0]
        if taken[pick.item.kind] >= HEAD_PER_KIND:
            pick = next(
                (
                    u
                    for u in ranked
                    if u.value >= 2
                    and u.item.kind != pick.item.kind
                    and taken[u.item.kind] < HEAD_PER_KIND
                ),
                pick,
            )
        ranked.remove(pick)
        head.append(pick)
        taken[pick.item.kind] += 1
    return head + ranked


def _basis(metrics: Rows) -> dict[str, str | None]:
    stamped = [(str(at), at, m) for m in metrics if (at := field(m, "updated_at"))]
    if not stamped:
        return {"analyzed_commit": None, "health_analyzed_at": None}
    _key, at, latest = max(stamped, key=lambda s: s[0])
    return {
        "analyzed_commit": field(latest, "analyzed_commit"),
        "health_analyzed_at": at.isoformat() if hasattr(at, "isoformat") else str(at),
    }


def build_fix_first(
    *,
    metrics: Rows = (),
    findings: Rows = (),
    refactoring: Rows = (),
    performance: Rows = (),
    plans: Rows = (),
    limit: int | None = DEFAULT_LIMIT,
    scope: str = "production",
) -> FixFirstQueue:
    """One ranked queue of what to fix, from stored rows (shapes in the module docstring).

    ``scope="all"`` keeps test files (labelled in ``context``); ``limit=None``
    keeps every eligible item.
    """
    metrics = list(metrics)
    keep_tests = scope == "all"
    excluded = dict.fromkeys(FIX_EXCLUSIONS, 0)
    hidden = excluded_types()

    by_file: dict[str, list[Any]] = defaultdict(list)
    for f in findings:
        if (
            _num(field(f, "health_impact")) > 0
            and field(f, "biomarker_type") not in hidden
            and field(f, "dimension") != "performance"
            and _open(f)
        ):
            by_file[field(f, "file_path")].append(f)
    split = {path: split_by_origin(rows) for path, rows in by_file.items()}
    files = _Files(metrics, {p: hist for p, (_shape, hist) in split.items() if hist})

    def out_of_scope(path: str, context: str | None = None) -> bool:
        reason = _path_exclusion(path, files.is_test(path), context)
        if reason is None or (reason == "test" and keep_tests):
            return False
        excluded[reason] += 1
        return True

    plan_rows = {field(p, "public_id"): p for p in plans}
    units: list[_Unit] = []
    planned_files: set[str] = set()
    for row in sorted(
        (r for r in refactoring if _open(r)),
        key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id")),
    ):
        path = field(row, "file_path")
        planned_files.add(path)
        if out_of_scope(path):
            continue
        if _num(field(row, "recoverable_health")) < MIN_WORTH:
            excluded["below_min_worth"] += 1
            continue
        details = detail_map(row)
        steps, gain = _credited_steps(row, details, plan_rows)
        if not steps or gain < MIN_WORTH:
            excluded["below_min_worth"] += 1
            continue
        units.append(_refactor_unit(row, details, steps, gain, plan_rows, files))

    # Ceiling: grouped by the intervention as stored, until persistence writes
    # one row per intervention.
    groups: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in performance:
        if _open(row):
            path = field(row, "file_path") or ""
            groups[(path, field(row, "intervention_symbol") or path)].append(row)
    for (path, _symbol), rows in groups.items():
        context = field(rows[0], "execution_context")
        if out_of_scope(path, context):
            continue
        ready = [r for r in rows if _perf_ready(r)]
        if not ready:
            expected = any(field(r, "actionability_state") == "expected" for r in rows)
            excluded["expected" if expected else "no_plan"] += 1
            continue
        units.append(_perf_unit(ready, files))

    for path, (shape, history) in split.items():
        if path in planned_files:
            continue
        lead = primary_finding(shape)
        if lead is None and not history:
            continue  # advisory only: nothing to fix, nothing to count
        if out_of_scope(path):
            continue
        if lead is None:
            excluded["history_only"] += 1
            continue
        units.append(_finding_unit(lead, files))

    ordered = _order(units)
    shown = ordered if limit is None else ordered[: max(limit, 0)]
    by_improves = Counter(u.item.improves for u in units)
    return FixFirstQueue(
        items=tuple(replace(u.item, rank=i) for i, u in enumerate(shown)),
        totals=FixTotals(
            candidates=len(units) + sum(excluded.values()),
            eligible=len(units),
            shown=len(shown),
            excluded=excluded,
        ),
        by_improves={k: by_improves.get(k, 0) for k in FIX_IMPROVES},
        basis=_basis(metrics),
    )


__all__ = [
    "DEFAULT_LIMIT",
    "GAIN_CUTS",
    "HEAD",
    "HEAD_PER_KIND",
    "MIN_SLICE_NLOC",
    "MIN_WORTH",
    "build_fix_first",
]
