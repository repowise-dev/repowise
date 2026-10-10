"""One plan as a recipe an agent applies: an id, what must hold before the edit,
the edits in order, what must hold after, and what the edit must not do.

The shape borrows the recipe idea from automated-refactoring tools (id,
preconditions, steps, postconditions) so a stored plan reads the same on plan
detail, in the CLI's JSON and in the copy-prompt
(:mod:`repowise.core.agent_prompts.refactoring`). Repowise never applies an
edit: every step is a spec an agent carries out.

Input is a plan detail dict (:meth:`Recommendation.detail_dict`, or the stored
plan payload an opportunity embeds); every field is read defensively, so an
older row yields a shorter recipe rather than an error.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Literal, get_args

from .annotations import RiskKind
from .render import NAME_PLACEHOLDER, TYPE_PLACEHOLDER

RecipeAction = Literal[
    "extract",
    "add_helper",
    "replace_with_call",
    "extract_class",
    "move",
    "cut_import",
    "keep",
    "reexport",
    "edit",
    "delete",
]
RECIPE_ACTIONS: tuple[str, ...] = get_args(RecipeAction)
#: What a precondition is: a test run or a test to add first, a plan risk, a plan
#: whose risks were never checked, or a plan type whose output is not yet audited.
PRECONDITION_KINDS: tuple[str, ...] = (
    "tests",
    "characterization",
    *get_args(RiskKind),
    "unchecked",
    "kind_unaudited",
)

#: The heading each plan type's prompt carries.
TYPE_LABEL: dict[str, str] = {
    "performance_fix": "Performance",
    "extract_class": "Extract Class",
    "extract_helper": "Extract Helper",
    "extract_method": "Extract Method",
    "move_method": "Move Method",
    "break_cycle": "Break Cycle",
    "split_file": "Split File",
}

_KEEP_BEHAVIOUR = {
    "constraint": "change behaviour",
    "reason": "what the code returns, raises or writes stays the same",
}
#: Types whose steps are one way to do it, not the edit to make.
_ADVISORY = frozenset({"break_cycle"})
#: Types whose plans have not been through an accuracy audit yet.
_UNAUDITED = frozenset({"move_method", "extract_class"})

_STRATEGY_SUMMARY: dict[str, str] = {
    "batch_or_prefetch_io": "Batch the repeated per-item calls",
    "parallelize_independent_awaits": "Run the independent awaits concurrently",
    "replace_membership_collection": "Probe a set instead of searching a list",
    "buffer_string_accumulation": "Join the string once instead of growing it in a loop",
    "shrink_lock_scope": "Move the I/O out of the locked section",
    "push_reduction_into_query": "Let the query do the reduction",
}

Step = dict[str, Any]

#: Types whose ``target_symbol`` is a display label (``big.py -> 3 files``), not a symbol.
_LABEL_TARGETS = frozenset({"split_file", "break_cycle", "extract_helper"})


def label(refactoring_type: str | None) -> str:
    return TYPE_LABEL.get(refactoring_type or "", "Refactoring")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _strs(value: Any) -> list[str]:
    return [str(item) for item in _list(value) if item]


def _code(items: list[str]) -> str:
    return ", ".join(f"`{item}`" for item in items)


def _span(start: Any, end: Any = None) -> dict[str, int] | None:
    if not isinstance(start, int) or start <= 0:
        return None
    return {"start": start, "end": end if isinstance(end, int) and end >= start else start}


def _step(
    action: RecipeAction, file: str | None, span: dict[str, int] | None, text: str, **extra: Any
) -> Step:
    return {"action": action, "file": file, "span": span, "text": text, **extra}


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _short(symbol: str | None) -> str:
    return (symbol or "").rsplit("::", 1)[-1]


def _s(count: int) -> str:
    return "" if count == 1 else "s"


# -- steps, per plan type ------------------------------------------------------


def _extract_method(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    span = _dict(plan.get("span"))
    where = _span(span.get("start"), span.get("end"))
    host = _short(d.get("target_symbol"))
    name = plan.get("suggested_name") or NAME_PLACEHOLDER
    if where is None:
        text = f"Extract a slice of `{host}` into a helper."
        return [_step("extract", d.get("file_path"), None, text)]
    symbol = _dict(plan.get("new_symbol"))
    is_async = bool(symbol.get("async", plan.get("needs_async")))
    named = (
        " (a starting name; rename it if it collides)"
        if plan.get("suggested_name")
        else " (name it for what the lines do)"
    )
    text = (
        f"Move lines {where['start']}-{where['end']} of `{host}` into a new helper "
        f"`{name}`{named} in the same scope, and replace them with one call to it."
    )
    return [
        _step(
            "extract",
            d.get("file_path"),
            where,
            text,
            new_symbol={
                "name": name,
                "kind": symbol.get("kind"),
                "async": is_async,
                "params": _strs(plan.get("params")),
                "returns": _strs(plan.get("returns")),
                "signature_text": symbol.get("signature_text"),
                "notes": _extract_notes(plan, symbol, is_async),
            },
            **({"call_site": plan["call_site"]} if _dict(plan.get("call_site")) else {}),
        )
    ]


def _extract_notes(
    plan: Mapping[str, Any], symbol: Mapping[str, Any], is_async: bool
) -> list[str]:
    notes = _strs(symbol.get("notes"))
    if is_async and not symbol.get("signature_text"):
        notes.append("The lines await, so declare the helper async and await its call.")
    if symbol.get("kind") == "method" and not symbol.get("signature_text"):
        notes.append(
            "The lines use their object, so make the helper a method of the same object."
        )
    if plan.get("receiver_hazard"):
        notes.append(
            "Lifting the lines changes which object they read or write; check the helper "
            "still sees the same one."
        )
    call = _dict(plan.get("call_site")).get("new_text") or ""
    texts = f"{symbol.get('signature_text') or ''} {call}"
    if NAME_PLACEHOLDER in texts or TYPE_PLACEHOLDER in texts:
        notes.append(
            f"Fill each `{NAME_PLACEHOLDER}` / `{TYPE_PLACEHOLDER}` placeholder from the code."
        )
    return notes


def _reused(d: Mapping[str, Any]) -> str | None:
    """The existing function an Extract Helper plan calls, when it reuses one."""
    reuse = _dict(_dict(d.get("plan")).get("reuse"))
    return (_short(reuse.get("existing_symbol")) or None) if reuse else None


def _reuse_steps(reuse: Mapping[str, Any]) -> list[Step]:
    """One step per site that calls the existing function, or deletes a copy of it."""
    name = _short(reuse.get("existing_symbol"))
    home = reuse.get("file")
    link = {k: reuse.get(k) for k in ("existing_symbol", "file", "reason")}
    steps = []
    for site in (_dict(s) for s in _list(reuse.get("sites"))):
        span = _dict(site.get("span"))
        where = _span(span.get("start"), span.get("end"))
        lines = f"lines {where['start']}-{where['end']}" if where else "these lines"
        if site.get("action") == "delete":
            text = (
                f"Delete {lines}, a copy of `{name}`, and import `{name}` from `{home}` in its "
                "place; re-export it if other files import it from here."
            )
            steps.append(_step("delete", site.get("file"), where, text, reuse=link))
            continue
        imported = "" if site.get("file") == home else f" (import it from `{home}`)"
        text = f"Replace {lines} with a call to the existing `{name}`{imported}."
        call = {"replace_span": where, "new_text": site["new_text"]} if site.get("new_text") else None
        extra = {"call_site": call} if call and where else {}
        steps.append(_step("replace_with_call", site.get("file"), where, text, reuse=link, **extra))
    return steps


def _extract_helper(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    reuse = _dict(plan.get("reuse"))
    if reuse:
        return _reuse_steps(reuse)
    occurrences = [_dict(o) for o in _list(plan.get("occurrences"))]
    site = _dict(plan.get("suggested_site"))
    where = site.get("directory") or site.get("module")
    name = plan.get("suggested_name") or NAME_PLACEHOLDER
    lines = plan.get("duplicated_lines")
    size = f" ({lines} lines)" if isinstance(lines, int) and lines else ""
    # The site is a directory: the file the helper goes in is the agent's call.
    first = _step(
        "add_helper",
        None,
        None,
        f"Add one helper `{name}`{f' in `{where}`' if where else ''} holding the duplicated block"
        f"{size}; a difference between the sites becomes a parameter.",
        directory=where,
    )
    return [first] + [
        _step(
            "replace_with_call",
            o.get("file"),
            _span(o.get("line_start"), o.get("line_end")),
            "Replace these lines with a call to the helper.",
        )
        for o in occurrences
    ]


def _extract_class(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    host = d.get("target_symbol") or "the class"
    span = _span(d.get("line_start"), d.get("line_end"))
    groups = [_dict(g) for g in _list(plan.get("groups"))]
    groups = [g for g in groups if _list(g.get("methods")) or _list(g.get("fields"))]
    steps = [
        _step(
            "extract_class",
            d.get("file_path"),
            span,
            f"Move {_members(g)} out of `{host}` into a new class "
            f"`{g.get('name') or f'NewClass{i}'}`; they only use each other.",
        )
        for i, g in enumerate(groups, 1)
    ]
    steps.append(
        _step(
            "edit",
            d.get("file_path"),
            span,
            f"Keep `{host}` as a thin facade that delegates to the new classes, so callers "
            "outside it keep working.",
        )
    )
    return steps


def _members(group: Mapping[str, Any]) -> str:
    methods, fields = _strs(group.get("methods")), _strs(group.get("fields"))
    parts = [
        f"methods {_code(methods)}" if methods else "",
        f"fields {_code(fields)}" if fields else "",
    ]
    return " and ".join(p for p in parts if p)


def _move_method(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    method = plan.get("method") or _short(d.get("target_symbol"))
    to_file = plan.get("to_file")
    dest = f"`{plan.get('to_class') or 'its target class'}`"
    dest += f" in `{to_file}`" if to_file else ""
    return [
        _step(
            "move",
            d.get("file_path"),
            _span(d.get("line_start"), d.get("line_end")),
            f"Move `{method}` from `{plan.get('from_class') or 'its class'}` to {dest}, "
            "then update every call site; keep a delegating wrapper only for callers you "
            "cannot reach.",
            to_file=to_file,
        )
    ]


def _break_cycle(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    return [
        _step(
            "cut_import",
            edge.get("from"),
            None,
            f"Remove the import of `{edge.get('to')}`: invert the dependency or move what both "
            "need into a module neither imports. A function-local import only if it really "
            "breaks the cycle.",
        )
        for edge in (_dict(e) for e in _list(plan.get("cut_edges")))
    ]


def _split_file(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    path = d.get("file_path")
    groups = [_dict(g) for g in _list(plan.get("groups"))]
    steps = [
        _step(
            "move",
            g.get("suggested_file"),
            None,
            f"Move {_code(_strs(g.get('symbols')))} to "
            + (f"`{g['suggested_file']}`" if g.get("suggested_file") else f"a new file (part {i})")
            + " (a starting name; follow the repo's convention).",
            symbols=_strs(g.get("symbols")),
        )
        for i, g in enumerate((g for g in groups if _list(g.get("symbols"))), 1)
    ]
    residual = _strs(_dict(plan.get("residual")).get("symbols"))
    if residual:
        text = f"Leave {_code(residual)} in `{path}`: more than one group uses them."
        steps.append(_step("keep", path, None, text))
    if plan.get("shim_required") is False:
        text = "Files in one package share scope, so no import changes are needed."
        steps.append(_step("edit", path, None, text))
    else:
        text = f"Keep `{path}` importable: re-export the moved symbols, or update every importer."
        steps.append(_step("reexport", path, None, text))
    return steps


def _merged_verify(kept: Mapping[str, Any] | None, more: Mapping[str, Any] | None) -> Any:
    """Two checks for one edit as one: the union of their commands and tests."""
    if not kept or not more:
        return kept or more
    out = dict(kept)
    for key in ("commands", "tests"):
        out[key] = list(dict.fromkeys([*_strs(kept.get(key)), *_strs(more.get(key))]))
    return out


def _performance_fix(d: Mapping[str, Any], plan: Mapping[str, Any]) -> list[Step]:
    out: dict[tuple[Any, ...], Step] = {}
    for index, raw in enumerate(_list(plan.get("steps"))):
        step = _dict(raw)
        symbol = _short(step.get("symbol"))
        text = str(step.get("action") or "Apply the fix")
        verify = step["verify"] if isinstance(step.get("verify"), dict) else None
        # A site the evidence lists twice is still one edit, checked by both checks.
        # Only a placed site is known to repeat; a step without a line stays its own.
        line = step.get("line")
        key = (text, symbol, step.get("file_path"), line) if line is not None else (index,)
        if key in out:
            merged = _merged_verify(out[key].get("verify"), verify)
            if merged:
                out[key]["verify"] = merged
            continue
        where = f" in `{symbol}`" if symbol and symbol not in text else ""
        out[key] = _step(
            "edit",
            step.get("file_path"),
            _span(step.get("line")),
            f"{text}{where}.",
            applicability=step.get("applicability"),
            **({"verify": verify} if verify else {}),
        )
    return list(out.values())


_STEPS: dict[str, Callable[[Mapping[str, Any], Mapping[str, Any]], list[Step]]] = {
    "extract_method": _extract_method,
    "extract_helper": _extract_helper,
    "extract_class": _extract_class,
    "move_method": _move_method,
    "break_cycle": _break_cycle,
    "split_file": _split_file,
    "performance_fix": _performance_fix,
}


def recipe_steps(detail: Mapping[str, Any]) -> list[Step]:
    """The plan's edits in order, numbered from 1."""
    build = _STEPS.get(str(detail.get("refactoring_type") or ""))
    plan = _dict(detail.get("plan"))
    steps = build(detail, plan) if build else []
    if not steps:
        text = "Apply the change the plan describes."
        steps = [_step("edit", detail.get("file_path"), None, text)]
    return [{"n": n, **step} for n, step in enumerate(steps, 1)]


# -- the rest of the recipe ----------------------------------------------------


def _summary(d: Mapping[str, Any], steps: list[Step]) -> str:
    kind, plan = d.get("refactoring_type"), _dict(d.get("plan"))
    if kind == "extract_method" and steps[0].get("span"):
        span, name = steps[0]["span"], steps[0]["new_symbol"]["name"]
        host = _short(d.get("target_symbol"))
        return f"Extract lines {span['start']}-{span['end']} of `{host}` into `{name}`."
    if kind == "extract_helper" and (name := _reused(d)):
        return f"Call the existing `{name}` at {len(steps)} site{_s(len(steps))} that repeat it."
    if kind == "extract_helper":
        return f"Replace the block duplicated at {len(steps) - 1} sites with one shared helper."
    if kind == "extract_class":
        return f"Split `{d.get('target_symbol')}` into {len(steps) - 1} cohesive classes."
    if kind == "split_file":
        moves = sum(s["action"] == "move" for s in steps)
        return f"Split `{d.get('file_path')}`: move {moves} groups of symbols into their own files."
    if kind == "break_cycle":
        size, cuts = len(_list(plan.get("cycle"))), len(steps)
        return f"Break the {size}-file import cycle by cutting {cuts} import{_s(cuts)}."
    if kind == "performance_fix":
        lead = _STRATEGY_SUMMARY.get(str(plan.get("strategy")), "Apply the performance fix")
        return f"{lead} in `{_short(d.get('target_symbol'))}`."
    return steps[0]["text"]


def _preconditions(d: Mapping[str, Any]) -> list[dict[str, Any]]:
    validation = _dict(d.get("validation"))
    out: list[dict[str, Any]] = []
    if _strs(validation.get("tests")) or _count(validation.get("total")):
        out.append({"kind": "tests", "text": "The guarding tests pass before the edit."})
    if validation.get("prerequisite"):
        out.append({"kind": "characterization", "text": str(validation["prerequisite"])})
    if d.get("refactoring_type") in _UNAUDITED:
        text = "This plan type is not yet audited for accuracy; confirm it fits before applying."
        out.append({"kind": "kind_unaudited", "text": text})
    if "risks" not in d and "governed_by" not in d:
        # Absent is "never checked" (an older row), not "nothing found".
        text = "Risks and governing decisions were not checked for this plan."
        out.append({"kind": "unchecked", "text": text})
    risks = [_dict(r) for r in _list(d.get("risks"))]
    out.extend(
        {"kind": r["kind"], "text": r["text"], **({"ref": r["ref"]} if r.get("ref") else {})}
        for r in risks
        if r.get("kind") and r.get("text")
    )
    shown = {r.get("ref") for r in risks}
    out.extend(
        {"kind": "decision", "text": f"Decision `{ref}` also governs this file; read it.", "ref": ref}
        for ref in _strs(d.get("governed_by"))
        if ref not in shown
    )
    return out


def verify_check(validation: Any) -> dict[str, Any]:
    """The ``verify`` postcondition for a plan's (or a shared profile's) validation."""
    validation = _dict(validation)
    if not validation:
        text = "The tests that exercise the change pass; add one if none does."
        return {"kind": "verify", "text": text}
    tests = _strs(validation.get("tests"))
    total = validation.get("total") if isinstance(validation.get("total"), int) else len(tests)
    if tests:
        text = "The guarding tests pass after the edit, unchanged."
    elif total:
        text = f"The {total} guarding tests (not listed here) pass after the edit."
    else:
        text = "No guarding test was found; the test added before the edit passes after it."
    return {
        "kind": "verify",
        "text": text,
        "tests": tests,
        "tests_total": max(total, len(tests)),
        "commands": _strs(validation.get("commands")),
        "basis": validation.get("basis"),
        **({"reasons": validation["reasons"]} if _dict(validation.get("reasons")) else {}),
    }


def _metric(d: Mapping[str, Any]) -> str | None:
    kind, ev = d.get("refactoring_type"), _dict(d.get("evidence"))
    host = _short(d.get("target_symbol"))
    if kind == "extract_method" and ev.get("ccn_removed"):
        return f"`{host}` loses about {ev['ccn_removed']} branches of cyclomatic complexity."
    if kind == "extract_helper" and ev.get("duplicated_lines"):
        return f"The {ev['duplicated_lines']} duplicated lines exist once."
    if kind == "extract_class" and ev.get("lcom4"):
        return f"Each new class is one cohesive group (LCOM4 1; `{host}` is {ev['lcom4']} today)."
    if kind == "split_file" and ev.get("file_nloc"):
        return f"No resulting file is near the original's {ev['file_nloc']} lines."
    if kind == "break_cycle" and ev.get("cycle_size"):
        return f"The {ev['cycle_size']}-file import cycle is gone."
    if kind == "performance_fix":
        sites = _dict(d.get("blast_radius")).get("call_sites")
        if sites:
            return f"The repeated work no longer runs once per item at {sites} call site{_s(sites)}."
    return None


def _postconditions(d: Mapping[str, Any]) -> list[dict[str, Any]]:
    out = [verify_check(d.get("validation"))]
    metric = _metric(d)
    if metric:
        out.append({"kind": "metric", "text": metric})
    impact = d.get("impact_delta")
    if isinstance(impact, (int, float)) and impact > 0:
        out.append({"kind": "metric", "text": f"File health recovers about {impact:.2f}."})
    return out


#: Per type, what the edit must not do and, when it is not obvious, why.
_DOES_NOT: dict[str, tuple[str, str | None]] = {
    "extract_helper": ("merge sites that differ", "a difference between them becomes a parameter"),
    "extract_class": ("rename or remove anything callers outside the class use", None),
    "move_method": ("leave a call site pointing at the old class", None),
    "break_cycle": ("merge the files in the cycle", None),
    "split_file": ("put one symbol in two files", None),
    "performance_fix": (
        "change the results or errors the batched or reordered calls produce",
        None,
    ),
}


def _does_not(d: Mapping[str, Any], steps: list[Step]) -> list[dict[str, str | None]]:
    """What the edit must not do, each a phrase read after "Do not", with its reason."""
    out: list[dict[str, str | None]] = [dict(_KEEP_BEHAVIOUR)]
    kind = str(d.get("refactoring_type") or "")
    span = steps[0].get("span")
    if kind == "extract_method" and span:
        lines = f"{span['start']}-{span['end']}"
        out.append(
            {"constraint": f"touch anything outside lines {lines} and the call that replaces them",
             "reason": None}
        )
    elif kind == "extract_helper" and (name := _reused(d)):
        out.append({"constraint": f"change `{name}` to fit a site", "reason": "its callers rely on it"})
    elif kind in _DOES_NOT:
        constraint, reason = _DOES_NOT[kind]
        out.append({"constraint": constraint, "reason": reason})
    if any(s.get("applicability") == "judgment" for s in steps):
        out.append(
            {"constraint": "apply a judgment step without reading the code it names first",
             "reason": None}
        )
    return out


def build_recipe(detail: Mapping[str, Any]) -> dict[str, Any]:
    """*detail* as ``{id, kind, advisory, summary, target, preconditions, steps,
    postconditions, does_not}``. Pure: reads only the dict it is given."""
    steps = recipe_steps(detail)
    return {
        "id": detail.get("id"),
        "kind": detail.get("refactoring_type"),
        # The steps are one way to do it, for a person to judge, not an edit to apply.
        "advisory": detail.get("refactoring_type") in _ADVISORY,
        "summary": _summary(detail, steps),
        "target": {
            "file": detail.get("file_path"),
            "symbol": (detail.get("target_symbol") or None)
            if detail.get("refactoring_type") not in _LABEL_TARGETS
            else None,
            "span": _span(detail.get("line_start"), detail.get("line_end")),
        },
        "preconditions": _preconditions(detail),
        "steps": steps,
        "postconditions": _postconditions(detail),
        "does_not": _does_not(detail, steps),
    }


__all__ = [
    "PRECONDITION_KINDS",
    "RECIPE_ACTIONS",
    "TYPE_LABEL",
    "RecipeAction",
    "build_recipe",
    "label",
    "recipe_steps",
    "verify_check",
]
