"""Plain-language copy for Fix-first items: titles, problems, gains.

Biomarker ids and internal units (CCN, LCOM, deficit points) never reach a
title, a why sentence or a gain; they may appear in ``facts``, which renderers
label through their own glossary.
"""

from __future__ import annotations

import posixpath

TITLE_MAX = 90

#: What the finding says about the code, completing "<subject> ...".
PROBLEM: dict[str, str] = {
    "brain_method": "is long, branchy and widely depended on",
    "complex_method": "has more branches than its tests can easily cover",
    "nested_complexity": "nests its control flow deeply, which hides the paths through it",
    "bumpy_road": "repeats nested blocks side by side, a sign it does several jobs",
    "large_method": "is too long to read in one pass",
    "god_class": "holds too much of its subsystem in one class",
    "low_cohesion": "holds groups of methods that share no state or calls",
    "complex_conditional": "packs several boolean operators into one condition",
    "primitive_obsession": "takes a long list of parameters",
    "dry_violation": "duplicates code that lives elsewhere, so a fix has to land twice",
    "error_handling": "swallows an exception, so failures go unseen",
    "coverage_gap": "is mostly untested",
    "untested_hotspot": "changes often and is barely tested",
    "function_hotspot": "is complex and keeps changing",
    "split_file": "holds several loosely related parts in one file",
    "break_cycle": "sits in an import cycle, so neither side can change alone",
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
    "error_handling": "Handle the swallowed exception in {where}",
    "coverage_gap": "Add tests for {where}",
    "untested_hotspot": "Add tests for {where}",
    "function_hotspot": "Simplify {where}, which keeps changing",
}

#: Imperative title for a composed refactoring, by its lead step's type.
REFACTOR_TITLE: dict[str, str] = {
    "extract_method": "Extract a helper from {sym}",
    "extract_helper": "Replace the duplicated code in {sym} with one shared helper",
    "extract_class": "Extract a class from {sym}",
    "split_file": "Split {file} into smaller modules",
    "break_cycle": "Break the import cycle through {file}",
    "move_method": "Move {sym} next to the code it uses",
}

BOUNDARY_NOUN = {
    "db": "database",
    "filesystem": "file system",
    "network": "network",
    "subprocess": "subprocess",
}

FIX_STRATEGY: dict[str, str] = {
    "batch_or_prefetch_io": "Batch the calls, or fetch the data once before the loop",
    "replace_membership_collection": "Use a set or dict for the membership test",
    "buffer_string_accumulation": "Collect the pieces in a list and join them once",
    "push_reduction_into_query": "Let the query do the reduction",
}


def humanize(token: str) -> str:
    return token.replace("_", " ")


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


def health_gain(value: float, *, ceiling: bool) -> str:
    shown = "under +0.1" if value < 0.05 else f"+{value:.1f}"
    lead = "up to" if ceiling else "about"
    return f"{lead} {shown} health on this file"


def first_sentence(text: str) -> str:
    head, dot, _ = text.partition(". ")
    return head + "." if dot else text


__all__ = [
    "BOUNDARY_NOUN",
    "FINDING_TITLE",
    "FIX_STRATEGY",
    "PROBLEM",
    "REFACTOR_TITLE",
    "TITLE_MAX",
    "basename",
    "clip",
    "first_sentence",
    "health_gain",
    "humanize",
    "plural",
    "problem",
    "short_symbol",
]
