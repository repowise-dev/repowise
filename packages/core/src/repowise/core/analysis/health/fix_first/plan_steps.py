"""Read stored refactoring plans and word their steps."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping
from typing import Any

from repowise.core.analysis.health.refactoring.recipe import ELSEWHERE_NOTE, plan_anchors
from repowise.core.analysis.health.rows import field, json_field
from repowise.core.analysis.health.worth import LowPriority
from repowise.core.ci.markdown import code as md_code

from . import text
from ._parts import _helper_code, _open, object_code, shared_object
from .model import FixStep


def _plan_body(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    body = json_field(plan, "plan", None) or json_field(plan, "plan_json", {})
    return body if isinstance(body, dict) else {}


def _above_bar(details: Mapping[str, Any]) -> LowPriority | None:
    """An opportunity one of whose plans, applied as written, still leaves a
    function above the break-up bar: half a fix, so it can wait."""
    return "above_bar_after" if details.get("leaves_above_bar") else None


def _above_bar_reason(plan: Any) -> str | None:
    """Why the plan, applied as written, leaves a function above the break-up
    bar: its rank facts' ``leaves_above_bar``, in the plan's own words."""
    rank = json_field(plan, "rank", None) or json_field(plan, "rank_json", {})
    reason = rank.get("leaves_above_bar") if isinstance(rank, dict) else None
    return reason if isinstance(reason, str) and reason else None


def _above_bar_note(step: str, plan: Any) -> str:
    """A finding's borrowed *step*, saying so when the plan leaves the
    function above the break-up bar (:func:`_above_bar_reason`)."""
    reason = _above_bar_reason(plan)
    return f"{step.removesuffix('.')}. This plan alone {reason}." if reason else step


def _evidence(plan: Any) -> dict[str, Any]:
    if plan is None:
        return {}
    found = json_field(plan, "evidence", None) or json_field(plan, "evidence_json", {})
    return found if isinstance(found, dict) else {}


def _span(plan: Any) -> tuple[int | None, int | None]:
    span = _plan_body(plan).get("span")
    if isinstance(span, dict):
        return span.get("start"), span.get("end")
    return None, None


def _refactor_step(
    order: int, step: Mapping[str, Any], plan: Any, command: str | None = None
) -> FixStep:
    kind = step.get("refactoring_type") or ""
    path = step.get("file_path") or ""
    sym = text.short_symbol(step.get("target_symbol")) or text.basename(path)
    body = _plan_body(plan)
    phrase = _STEP_TEXT.get(kind)
    line_text = (
        phrase(sym, path, plan, body) if phrase else f"Apply the {text.humanize(kind)} step to {sym}"
    )
    code = _helper_code(body) if kind == "extract_method" else {}
    mechanical = (step.get("applicability") or {}).get("classification") == "mechanical"
    line = _span(plan)[0] or step.get("line_start")
    return FixStep(order, line_text, path, line, mechanical, **code, command=command)


def _first_four(names: list[str]) -> str:
    """Up to four names, then how many more."""
    more = f" and {len(names) - 4} more" if len(names) > 4 else ""
    return f"{', '.join(names[:4])}{more}"


def _extract_class_text(sym: str, path: str, plan: Any, body: Mapping[str, Any]) -> str:
    """The smallest named group of methods leaves for a new class."""
    groups = [g for g in body.get("groups") or [] if g.get("methods")]
    if not groups:
        return f"Move a cohesive group of {sym}'s methods into a new class"
    members = sorted(groups, key=lambda g: len(g["methods"]))[0]["methods"]
    return f"Move {_first_four(members)} out of {sym} into a new class"


def _split_file_text(sym: str, path: str, plan: Any, body: Mapping[str, Any]) -> str:
    names = [g.get("name") for g in body.get("groups") or [] if g.get("name")]
    if not names:
        return f"Split {text.basename(path)} along its independent groups"
    return f"Split {text.basename(path)} into {_first_four(names)}"


def _break_cycle_text(sym: str, path: str, plan: Any, body: Mapping[str, Any]) -> str:
    edge = _cut_edge(plan)
    if edge is None:
        return _uncut_cycle_text(path, plan)
    return (
        f"Cut the import of {text.basename(edge['to'])} in "
        f"{text.basename(edge['from'])} (line {edge['line']})"
    )


def _move_method_text(sym: str, path: str, plan: Any, body: Mapping[str, Any]) -> str:
    dest = body.get("to_class") or text.basename(body.get("to_file") or "")
    return f"Move {sym} to {dest}" if dest else f"Move {sym} to the class it uses most"


def _extract_text(sym: str, body: Mapping[str, Any], start: Any, end: Any) -> str:
    """An Extract Method step: its span by the text it starts and ends with
    when the plan anchored it (line numbers move with every edit), else by
    its lines; a staged plan's step is its first stage."""
    into = text.signature(
        body.get("suggested_name"),
        list(body.get("params") or []),
        list(body.get("returns") or []),
        is_async=bool(body.get("needs_async", False)),
    )
    anchors = plan_anchors(body.get("anchors"))
    # Worded like the plan's own step (``recipe._extract_step``).
    if anchors is not None:
        where = f"{sym} from {md_code(anchors['first'])} to {md_code(anchors['last'])}"
    else:
        where = f"lines {start}-{end} of {sym}" if start and end else f"part of {sym}"
    line_text = _stage_one(f"Extract {where} into {into}", body)
    if anchors is not None and anchors["unique_in"] == "function":
        line_text = f"{line_text}. {ELSEWHERE_NOTE}"
    return line_text


def _extract_ready(body: Mapping[str, Any]) -> bool:
    """Whether the step can be written as it reads: a helper taking the shared
    parameter object needs the object's declaration and the line building it,
    or its call names a variable and a type that do not exist."""
    obj = shared_object(body)
    return obj is None or object_code(obj) is not None


#: A refactoring step's sentence, by plan type, from (symbol, path, plan, plan body).
_STEP_TEXT: dict[str, Callable[[str, str, Any, Mapping[str, Any]], str]] = {
    "extract_method": lambda sym, path, plan, body: _extract_text(sym, body, *_span(plan)),
    "extract_helper": lambda sym, *_: (
        f"Replace the duplicate in {sym} with a call to one shared helper"
    ),
    "extract_class": _extract_class_text,
    "split_file": _split_file_text,
    "break_cycle": _break_cycle_text,
    "move_method": _move_method_text,
}


def _stage_count(body: Mapping[str, Any]) -> int:
    """How many helpers a staged Extract Method plan splits its function into;
    0 when unstaged. Counts stages with a span, as the plan detail page does,
    and fewer than two is no split."""
    stages = body.get("stages")
    if not isinstance(stages, (list, tuple)):
        return 0
    count = sum(_has_span(stage) for stage in stages)
    return count if count >= 2 else 0


def _has_span(stage: Any) -> bool:
    """A stage with nonzero integer line bounds (a bool is not a line)."""
    span = stage.get("span") if isinstance(stage, dict) else None
    if not isinstance(span, dict):
        return False
    start, end = span.get("start"), span.get("end")
    return type(start) is int and type(end) is int and bool(start and end)


def _stage_one(line_text: str, body: Mapping[str, Any]) -> str:
    """A staged plan's step is its first stage (the plan's top-level span):
    it says so, and the function's CCN once every stage is done."""
    count = _stage_count(body)
    if not count:
        return line_text
    residual = body.get("orchestrator")
    before, after = (
        (residual.get("ccn_before"), residual.get("ccn_after"))
        if isinstance(residual, dict)
        else (None, None)
    )
    ccn = (
        f" (CCN {before} -> {after} after all {count})"
        if before is not None and after is not None
        else ""
    )
    return f"Stage 1 of {count}: {line_text}{ccn}"


def _uncut_cycle_text(path: str, plan: Any) -> str:
    """A cycle step with no import line to cut, labelled when it is idiomatic."""
    if _plan_body(plan).get("idiom"):
        return (
            f"Optional: {text.basename(path)} is in an import cycle within one "
            "directory, which is idiomatic in this language"
        )
    return f"Break the import cycle at {text.basename(path)}"


def _cut_edge(plan: Any) -> dict[str, Any] | None:
    """The first cut edge whose import line is stored.

    An idiomatic cycle (one directory of a language that compiles mutual
    references in one pass) has no edge to cut, so it never reads as a must-do.
    """
    if _plan_body(plan).get("idiom"):
        return None
    for edge in _plan_body(plan).get("cut_edges") or ():
        if isinstance(edge, dict) and edge.get("line") and edge.get("from") and edge.get("to"):
            return edge
    return None


def _concrete(step: Mapping[str, Any], plan: Any) -> bool:
    """Whether a refactoring step names an edit someone can make: lines to
    lift, a destination, the import to cut, or named groups."""
    kind = step.get("refactoring_type") or ""
    body = _plan_body(plan)
    if kind == "extract_method":
        start, end = _span(plan)
        return bool(start and end) and _extract_ready(body)
    if kind == "extract_helper":
        return any(
            isinstance(o, dict) and o.get("line_start") for o in body.get("occurrences") or ()
        )
    if kind == "move_method":
        return bool(body.get("to_class") or body.get("to_file"))
    if kind == "break_cycle":
        return _cut_edge(plan) is not None
    if kind in ("split_file", "extract_class"):
        members = "symbols" if kind == "split_file" else "methods"
        return any(
            isinstance(g, dict) and g.get(members) and (g.get("name") or kind == "extract_class")
            for g in body.get("groups") or ()
        )
    return bool(step.get("line_start"))


def _extraction_worth(plan: Any) -> tuple[int, int]:
    evidence = _evidence(plan)
    return int(evidence.get("ccn_removed") or 0), int(evidence.get("slice_nloc") or 0)


def _extractions(plans: Iterable[Any]) -> dict[tuple[str, str], list[Any]]:
    """Open Extract Method plans with a span, by (file, function name). A plan
    a step names is read whatever its status, so a dismissed one is skipped."""
    out: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for plan in plans:
        symbol = field(plan, "target_symbol")
        if field(plan, "refactoring_type") != "extract_method" or not symbol or not _open(plan):
            continue
        start, end = _span(plan)
        if start and end and _extract_ready(_plan_body(plan)):
            out[(field(plan, "file_path"), text.short_symbol(symbol).rsplit(".", 1)[-1])].append(
                plan
            )
    return dict(out)
