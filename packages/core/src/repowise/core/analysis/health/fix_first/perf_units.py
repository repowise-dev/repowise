"""Fix-first units for performance opportunities."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from repowise.core.analysis.health.queue.eligibility import perf_low_priority
from repowise.core.analysis.health.queue.value import (
    perf_confidence,
    perf_ready,
    perf_value,
    perf_worth,
)
from repowise.core.analysis.health.rows import detail_map, field
from repowise.core.analysis.health.worth import execution_role

from . import text
from ._files import _action, _Files, _finish, _opportunity_call
from ._parts import MAX_FACTS, _one_of, _risk, _step_command, _Unit, _verify
from .model import (
    FIX_EFFORTS,
    FixConfidence,
    FixEffortEstimate,
    FixFact,
    FixGain,
    FixRankFact,
    FixSource,
    FixStep,
    FixTarget,
)


def _perf_step_text(step: Mapping[str, Any], path: str) -> str:
    """A plan step's action, naming its function once: an action that already
    says the name is not followed by it again."""
    action = step.get("action") or ""
    name = text.scope_name(step.get("symbol"), path)
    # A whole word that is not an attribute: ``get`` is not named by a quoted
    # ``session.get`` call, nor by ``getattr``.
    short = name.rsplit(".", 1)[-1] if name else ""
    if not name or re.search(rf"(?<![\w.]){re.escape(short)}(?!\w)", action):
        return action
    return f"{action} ({name})"


def _site_symbol(symbol: str | None) -> str | None:
    """The function a call site sits in; ``None`` for none, or a file's top
    level, which is no symbol to jump to."""
    short = text.short_symbol(symbol)
    if not short or short.rsplit(".", 1)[-1] == "__module__":
        return None
    return short


def _perf_unit(
    rows: list[Any], files: _Files, symbol_lines: Mapping[str, int] | None = None
) -> _Unit:
    cause = _PerfCause.read(rows, files, symbol_lines)
    lead, path = cause.lead, cause.path
    return _finish(
        kind="perf_fix",
        may_lead=cause.details.get("may_lead") is not False,
        source_id=f"{path}::{cause.symbol or path}",
        value=perf_value(lead, cause.facets),
        ready=perf_ready(lead, cause.plan),
        worth=perf_worth(cause.call_sites, files.churning(path)),
        confidence=perf_confidence(cause.facets),
        effort=cause.plan.get("effort_bucket") or "M",
        improves="performance",
        low=perf_low_priority(lead),
        rank_inputs=lambda: _perf_rank_facts(cause),
        fields=lambda: _perf_fields(cause),
    )


@dataclass(frozen=True, slots=True)
class _PerfCause:
    """One intervention's performance rows as its item reads them, led by
    the best ranked."""

    rows: list[Any]
    files: _Files
    symbol_lines: Mapping[str, int]
    lead: Any
    details: Mapping[str, Any]
    facets: Mapping[str, Any]
    plan: Mapping[str, Any]
    path: str
    symbol: str | None
    call_sites: int

    @classmethod
    def read(
        cls, rows: list[Any], files: _Files, symbol_lines: Mapping[str, int] | None
    ) -> _PerfCause:
        lead = min(
            rows, key=lambda r: (field(r, "rank_position") or 0, field(r, "opportunity_id"))
        )
        details = detail_map(lead)
        return cls(
            rows,
            files,
            symbol_lines or {},
            lead,
            details,
            details.get("facets") or {},
            details.get("plan") or {},
            field(lead, "file_path") or "",
            field(lead, "intervention_symbol"),
            int(field(lead, "affected_call_sites_total") or 0),
        )

    @property
    def plan_steps(self) -> list[Mapping[str, Any]]:
        return self.plan.get("steps") or []


def _perf_rank_facts(cause: _PerfCause) -> list[FixRankFact]:
    lead = cause.lead
    return [
        FixRankFact("runs in", field(lead, "execution_context") or "unknown"),
        FixRankFact("run by", execution_role(lead)),
        FixRankFact("call sites", str(cause.call_sites)),
        FixRankFact("loop size", text.loop_size(cause.facets.get("loop_magnitude"))),
        FixRankFact(
            "boundary", text.BOUNDARY_NOUN.get(field(lead, "boundary_kind") or "", "none")
        ),
    ]


def _perf_fields(cause: _PerfCause) -> dict[str, Any]:
    lead, path = cause.lead, cause.path
    name, symbol = _perf_name(cause)
    files_n = int(field(lead, "affected_files_total") or 1)
    what, gain_text = text.perf_cost(
        name,
        field(lead, "biomarker_type"),
        field(lead, "boundary_kind"),
        cause.facets.get("amplification"),
        cause.facets.get("loop_magnitude"),
    )
    verify = _verify(cause.plan.get("validation"))
    steps = _perf_steps(cause, verify.command)
    strategy = field(lead, "fix_strategy") or ""
    return {
        "title": text.clip(_perf_title(cause, name)),
        "target": _perf_target(cause, symbol, steps),
        "why": f"{what}; {_perf_reach(cause, files_n)}.",
        "facts": _perf_facts(cause, files_n),
        "action": _action(
            text.FIX_STRATEGY.get(strategy, text.humanize(strategy).capitalize()),
            steps,
            _all_mechanical(cause.plan_steps),
        ),
        "gain": FixGain("performance", None, gain_text),
        "effort": _perf_effort(cause.plan.get("effort_bucket")),
        "risk": _risk(files_n, None),
        "confidence": FixConfidence(
            perf_confidence(cause.facets),
            cause.details.get("fix_rationale")
            or f"{text.humanize(field(lead, 'actionability_state'))} plan",
        ),
        "verify": verify,
        "context": cause.files.context(path, symbol),
        "source": FixSource(field(lead, "opportunity_id")),
        "next_call": _opportunity_call(
            "The full opportunity: ordered steps, validation and other causes here",
            field(lead, "opportunity_id"),
        ),
    }


def _perf_name(cause: _PerfCause) -> tuple[str, str | None]:
    """What the item calls the function, and the symbol its target names:
    ``None`` at module scope or when no symbol is stored."""
    # A cause with no intervention symbol is named by the function its plan edits.
    first = cause.plan_steps[0] if cause.plan_steps else {}
    name = (
        text.scope_name(cause.symbol, cause.path)
        or text.scope_name(first.get("symbol"), cause.path)
        or text.basename(cause.path)
    )
    module = name.startswith("module scope of ")
    return name, name if (cause.symbol or first.get("symbol")) and not module else None


def _all_mechanical(plan_steps: list[Mapping[str, Any]]) -> bool:
    return bool(plan_steps) and all(s.get("applicability") == "mechanical" for s in plan_steps)


def _perf_title(cause: _PerfCause, name: str) -> str:
    shaped = text.PERF_SHAPE.get(field(cause.lead, "biomarker_type") or "")
    if shaped:
        return shaped[0].format(name=name)
    noun = text.BOUNDARY_NOUN.get(field(cause.lead, "boundary_kind") or "")
    if noun and cause.call_sites > 1:
        module = name.startswith("module scope of ")
        return f"Batch the {noun} calls loops make {'in' if module else 'through'} {name}"
    if noun:
        return f"Move the {noun} call in {name} out of its loop"
    return f"Fix the repeated work in {name}"


def _perf_target(cause: _PerfCause, symbol: str | None, steps: tuple[FixStep, ...]) -> FixTarget:
    """The loop around the first call site the plan names, else the call
    itself; with no call site, the function."""
    site = next((s for s in cause.plan_steps if s.get("line")), None)
    if site:
        return FixTarget(
            site.get("file_path") or cause.path,
            _site_symbol(site.get("symbol")),
            site.get("loop_line") or site.get("line"),
        )
    return FixTarget(cause.path, symbol, steps[0].line if steps else None)


def _perf_reach(cause: _PerfCause, files_n: int) -> str:
    reach = []
    if cause.call_sites > 1:
        reach.append(f"{cause.call_sites} call sites in {text.plural(files_n, 'file')} reach it")
    if cause.facets.get("exposure") == "entry_reachable":
        reach.append("an entry point reaches it")
    if cause.facets.get("loop_magnitude") == "grows_with_data":
        reach.append("the loop grows with the data")
    return ", ".join(reach) or "the loop size is unknown"


def _perf_facts(cause: _PerfCause, files_n: int) -> tuple[FixFact, ...]:
    exposure, loop_magnitude = cause.facets.get("exposure"), cause.facets.get("loop_magnitude")
    facts = [
        FixFact("call sites", str(cause.call_sites)),
        FixFact("files", str(files_n)),
        FixFact(
            "reachable from an entry point",
            text.REACH_ANSWER.get(exposure or "", "unknown"),
            "inferred" if exposure in ("entry_reachable", "not_entry_reachable") else "unknown",
        ),
        FixFact(
            "loop size",
            text.loop_size(loop_magnitude),
            "inferred" if loop_magnitude in ("grows_with_data", "bounded") else "unknown",
        ),
    ]
    sinks = sorted({field(r, "terminal_sink") for r in cause.rows if field(r, "terminal_sink")})
    if len(sinks) > 1:
        facts.append(FixFact("sinks this fix covers", str(len(sinks))))
    return tuple(facts[:MAX_FACTS])


def _perf_steps(cause: _PerfCause, item_command: str | None) -> tuple[FixStep, ...]:
    path, lines = cause.path, cause.symbol_lines
    return tuple(
        FixStep(
            int(s.get("order") or i + 1),
            _perf_step_text(s, path),
            s.get("file_path") or path,
            s.get("line") or lines.get(s.get("symbol") or ""),
            s.get("applicability") == "mechanical",
            command=_step_command(s.get("verify"), item_command),
        )
        for i, s in enumerate(cause.plan_steps)
    )


def _perf_effort(effort: str | None) -> FixEffortEstimate:
    return FixEffortEstimate(
        _one_of(effort, FIX_EFFORTS, "M"),
        "sized by the stored plan" if effort else "not sized; the plan has no estimate",
    )
