"""Plain-language copy for Fix-first items: titles, problems, gains, context.

Biomarker ids never reach any text field: a fact names a finding by its plain
label. A title may carry a function's size; the why sentence never repeats it,
and says why the problem matters here instead (who imports the file, how
often it changes, how much of it tests run). History context is a short plain
sentence, never a percentile.
"""

from __future__ import annotations

import posixpath

from ..refactoring.render import brief

TITLE_MAX = 90

#: What the finding says about the code, completing "<subject> ...". The
#: fallback when no measured fact is stored; each phrase must hold for every
#: finding of its kind, so none claims coverage or callers it was not given.
PROBLEM: dict[str, str] = {
    "brain_method": "is long, branchy and widely depended on",
    "complex_method": "has many independent paths through it",
    "nested_complexity": "nests its control flow deeply",
    "bumpy_road": "repeats nested blocks side by side, a sign it does several jobs",
    "large_method": "is long",
    "god_class": "holds too much of its subsystem in one class",
    "low_cohesion": "holds groups of methods that share little state",
    "complex_conditional": "packs several boolean operators into one condition",
    "primitive_obsession": "takes a long list of parameters",
    "dry_violation": "duplicates code, so a fix has to land in more than one place",
    "error_handling": "handles an exception in a way that can hide a failure",
    "coverage_gap": "has low measured test coverage",
    "untested_hotspot": "changes often and has low measured test coverage",
    "function_hotspot": "is complex and keeps changing",
    "split_file": "holds several loosely related parts in one file",
    "break_cycle": "sits in an import cycle",
}

#: Imperative title for a finding with no plan, given "<where>".
FINDING_TITLE: dict[str, str] = {
    "brain_method": "Split {where} into smaller functions",
    "complex_method": "Reduce the branching in {where}",
    "nested_complexity": "Flatten the nesting in {where}",
    "bumpy_road": "Split {where} into stages",
    "large_method": "Shorten {where}",
    "god_class": "Break up the {where} class",
    "low_cohesion": "Split {where} along its unrelated method groups",
    "complex_conditional": "Simplify the condition in {where}",
    "primitive_obsession": "Group the parameters of {where}",
    "dry_violation": "Remove the duplicated code in {where}",
    "error_handling": "Fix the exception handling in {where}",
    "coverage_gap": "Add tests for {where}",
    "untested_hotspot": "Add tests for {where}",
    "function_hotspot": "Simplify {where}, which keeps changing",
}

#: Imperative title for a composed refactoring, by its lead step's type.
REFACTOR_TITLE: dict[str, str] = {
    "extract_method": "Extract a helper from {sym}",
    "extract_method_span": "Extract lines {start}-{end} of {sym} into {name}",
    "extract_helper": "Replace the duplicated code in {sym} with one shared helper",
    "extract_class": "Extract a class from {sym}",
    "split_file": "Split {file} into smaller modules",
    "break_cycle": "Break the import cycle through {file}",
    "move_method": "Move {sym} next to the code it uses",
}

#: Appended to a why when a duplicate sits in the function being split.
CLONED = "and part of it is duplicated, so splitting it also removes a copy"

BOUNDARY_NOUN = {
    "db": "database",
    "filesystem": "file system",
    "network": "network",
    "subprocess": "subprocess",
}

#: Performance causes whose cost is not a boundary call: (title, problem, gain).
PERF_SHAPE: dict[str, tuple[str, str, str]] = {
    "string_concat_in_loop": (
        "Build the string in {name} with one join after the loop",
        "{name} grows a string by concatenation inside a loop",
        "one string copy per loop iteration",
    ),
    "membership_test_against_list_in_loop": (
        "Use a set for the membership test in {name}",
        "{name} searches a list once per loop iteration",
        "one list scan per loop iteration",
    ),
    "unbounded_read_reduced_in_memory": (
        "Let the query reduce the rows {name} reads",
        "{name} reads every row and reduces them in memory",
        "rows the database could have reduced, read on every call",
    ),
}

#: How a verify block found its tests, completing "reaches the changed code ...".
TEST_VIA: dict[str, str] = {
    "coverage": "in a coverage report",
    "call-graph": "through the call graph",
    "import-graph": "through the import graph",
    "name-match": "by a matching test name",
    "mixed": "through the call and import graphs",
}

#: Whether an entry point reaches a performance cause, as a short answer.
REACH_ANSWER: dict[str, str] = {"entry_reachable": "yes", "not_entry_reachable": "no"}

FIX_STRATEGY: dict[str, str] = {
    "batch_or_prefetch_io": "Batch the calls, or fetch the data once before the loop",
    "replace_membership_collection": "Use a set or dict for the membership test",
    "buffer_string_accumulation": "Collect the pieces in a list and join them once",
    "push_reduction_into_query": "Let the query do the reduction",
}


def humanize(token: str) -> str:
    return token.replace("_", " ")


def loop_size(magnitude: str | None) -> str:
    """``grows with data``, ``bounded``, ``no loop`` or ``unknown``."""
    if magnitude == "n/a":
        return "no loop"
    return humanize(magnitude) if magnitude else "unknown"


def imports_it(dependents: int) -> str:
    """``1 file imports it`` / ``7 files import it``."""
    return f"{plural(dependents, 'file')} import{'s' if dependents == 1 else ''} it"


def plural(n: int, noun: str) -> str:
    return f"{n:,} {noun}" if n == 1 else f"{n:,} {noun}s"


def short_symbol(symbol: str | None) -> str | None:
    """``path/to/x.py::Cls.method`` reads as ``Cls.method``."""
    return symbol.rsplit("::", 1)[-1] if symbol else None


def basename(path: str) -> str:
    return posixpath.basename(path) or path


def clip(text: str, limit: int = TITLE_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 3].rstrip() + "..."


def problem(marker: str | None, subject: str) -> str:
    phrase = PROBLEM.get(marker or "")
    if phrase is None:
        return f"{subject} has a {humanize(marker)} finding" if marker else f"{subject} needs work"
    return f"{subject} {phrase}"


def perf_cost(
    name: str,
    marker: str | None,
    boundary: str | None,
    amplification: str | None,
    magnitude: str | None,
) -> tuple[str, str]:
    """A performance cause's problem and what fixing it buys, in words.

    One function so the Fix-first item and the performance drawer say the same
    thing about the same cause. The gain names the repeated work, never a time
    saving: nothing here was measured running.
    """
    noun = BOUNDARY_NOUN.get(boundary or "")
    call = f"{noun} call" if noun else "costly call"
    shaped = PERF_SHAPE.get(marker or "")
    if shaped:
        return shaped[1].format(name=name), shaped[2]
    if amplification == "quadratic":
        return (
            f"{name} runs nested loops over the same data",
            "nested loop work that grows with the square of the data",
        )
    if amplification == "per_call":
        return f"{name} repeats a {call} on every call", f"one fewer {call} per call"
    gain = f"one {call} per loop iteration" + (
        ", grows with the data"
        if magnitude == "grows_with_data"
        else ", bounded by a fixed loop"
        if magnitude == "bounded"
        else "; loop size unknown"
    )
    return f"{name} makes a {call} once per loop iteration", gain


def health_gain(value: float, *, ceiling: bool) -> str:
    shown = "under +0.1" if value < 0.05 else f"+{value:.1f}"
    return f"{'up to ' if ceiling else ''}{shown} health on this file"


def measured(subject: str, shape: dict[str, int]) -> str | None:
    """``Store: 12 methods in 3 groups that share little state``, or ``None``.

    A function's size is a fact and may lead the title, so it is not here.
    """
    if shape.get("lcom4") and shape.get("method_count"):
        return (
            f"{subject}: {plural(shape['method_count'], 'method')} in "
            f"{shape['lcom4']} groups that share little state"
        )
    return None


def size_line(shape: dict[str, int]) -> str | None:
    """``CCN 12, 60 lines, nests 4 deep`` from stored findings, or ``None``."""
    parts = []
    if shape.get("ccn"):
        parts.append(f"CCN {shape['ccn']}")
    if shape.get("nloc"):
        parts.append(plural(shape["nloc"], "line"))
    if (shape.get("max_nesting") or 0) >= 3:
        parts.append(f"nests {shape['max_nesting']} deep")
    return ", ".join(parts) or None


def size_brief(shape: dict[str, int]) -> str:
    """``CCN 249, 1,280 lines``: the two numbers a title has room for."""
    parts = []
    if shape.get("ccn"):
        parts.append(f"CCN {shape['ccn']}")
    if shape.get("nloc"):
        parts.append(plural(shape["nloc"], "line"))
    if not parts and shape.get("max_nesting"):
        parts.append(f"nests {shape['max_nesting']} deep")
    return ", ".join(parts) or "large"


def exposure(dependents: int | None, commits: int, coverage: float | None = None) -> str:
    """``; 7 files import it, changed 15 times in 90 days, tests run 40% of
    its lines``, or nothing. Coverage only when a report measured the file."""
    parts = []
    if dependents:
        parts.append(imports_it(dependents))
    if commits:
        parts.append(f"changed {plural(commits, 'time')} in 90 days")
    if coverage is not None:
        parts.append(
            "no test runs it" if coverage < 0.5 else f"tests run {round(coverage)}% of its lines"
        )
    return "; " + ", ".join(parts) if parts else ""


def marker_label(marker: str) -> str:
    """``complex_method`` reads ``Complex method``: the plain label, not the id."""
    return humanize(marker).capitalize()


def scope_name(symbol: str | None, path: str) -> str | None:
    """A symbol as a reader says it; a file's top level is its module scope."""
    short = short_symbol(symbol)
    if short and short.rsplit(".", 1)[-1] == "__module__":
        return f"module scope of {basename(path)}"
    return short


def changes(commits: int, people: int | None) -> str:
    """``changed 13 times in 90 days; 3 people have worked on it``."""
    out = f"changed {plural(commits, 'time')} in 90 days"
    if people and people > 1:
        out += f"; {people} people have worked on it"
    return out


#: Plain labels for history context, by marker.
HISTORY_LABEL: dict[str, str] = {
    "change_entropy": "scattered changes",
    "ungoverned_hotspot": "no decision",
    "co_change_scatter": "ripple",
    "hidden_coupling": "hidden coupling",
    "churn_risk": "churn",
    "ownership_risk": "ownership",
    "knowledge_loss": "knowledge loss",
    "developer_congestion": "many hands",
    "prior_defect": "past bugs",
    "function_hotspot": "keeps changing",
    "code_age_volatility": "old and changing",
    "stale_governance": "stale decision",
    "contradictory_decision": "conflicting decisions",
}


def _num(details: dict, key: str) -> float:
    value = details.get(key)
    return float(value) if isinstance(value, (int, float)) else 0.0


def history_fact(marker: str, details: dict, function: str | None = None) -> str | None:
    """One short plain sentence for a history marker, from its stored numbers;
    ``None`` when the numbers it needs are absent."""
    d = details or {}
    if marker == "change_entropy":
        return "its changes are spread across many unrelated commits"
    if marker == "ungoverned_hotspot":
        return "it changes often and no recorded decision covers it"
    if marker == "co_change_scatter" and d.get("scatter"):
        return f"it changes together with {int(_num(d, 'scatter'))} other files"
    if marker == "hidden_coupling" and d.get("partner") and d.get("co_change_count"):
        return (
            f"it changes with {basename(str(d['partner']))} in "
            f"{int(_num(d, 'co_change_count'))} of {int(_num(d, 'self_commits'))} commits, "
            "with no import between them"
        )
    if marker == "churn_risk" and d.get("relative_churn"):
        lines = int(_num(d, "lines_added_90d") + _num(d, "lines_deleted_90d"))
        ratio = _num(d, "relative_churn")
        return f"{plural(lines, 'line')} changed in 90 days, {ratio:.1f} times its size"
    if marker == "ownership_risk" and d.get("contributor_count"):
        share = round(_num(d, "top_owner_share") * 100)
        return (
            f"{int(_num(d, 'contributor_count'))} people have changed it; "
            f"one wrote {share}% of its commits"
        )
    if marker == "knowledge_loss":
        return "the person who wrote most of it is no longer the one changing it"
    if marker == "developer_congestion" and d.get("contributor_count"):
        return f"{int(_num(d, 'contributor_count'))} people change it, with no clear owner"
    if marker == "prior_defect" and d.get("prior_defect_count"):
        months = max(1, round(_num(d, "window_days") / 30)) if d.get("window_days") else 6
        fixes = int(_num(d, "prior_defect_count"))
        return f"{fixes} bug fix{'' if fixes == 1 else 'es'} touched it in the last {months} months"
    if marker == "function_hotspot" and d.get("modification_count"):
        who = function or "one function"
        return f"{who} changed in {int(_num(d, 'modification_count'))} commits"
    if marker == "code_age_volatility":
        return "old code that still changes often"
    if marker == "stale_governance":
        return "a decision that covers it is out of date"
    if marker == "contradictory_decision":
        return "decisions that cover it disagree"
    return None


def signature(
    name: str | None, params: list[str], returns: list[str], *, is_async: bool = False
) -> str:
    """``_load(path) -> config``, as the plan's renderer phrases it
    (``refactoring.render.brief``); a helper with no name reads ``<name>(...)``
    and asks the reader to name it."""
    out = brief(name, params, returns, is_async=is_async)
    return out if name else f"{out}; name it for what the lines do"


def first_sentence(text: str) -> str:
    head, dot, _ = text.partition(". ")
    return head + "." if dot else text


__all__ = [
    "BOUNDARY_NOUN",
    "CLONED",
    "FINDING_TITLE",
    "FIX_STRATEGY",
    "HISTORY_LABEL",
    "PERF_SHAPE",
    "PROBLEM",
    "REACH_ANSWER",
    "REFACTOR_TITLE",
    "TEST_VIA",
    "TITLE_MAX",
    "basename",
    "changes",
    "clip",
    "exposure",
    "first_sentence",
    "health_gain",
    "history_fact",
    "humanize",
    "imports_it",
    "loop_size",
    "marker_label",
    "measured",
    "plural",
    "problem",
    "scope_name",
    "short_symbol",
    "signature",
    "size_brief",
    "size_line",
]
