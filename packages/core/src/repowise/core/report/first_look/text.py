"""The template text of a repo report: one table, written from facts.

:func:`template_prose` fills ``report.prose`` with the shape every writer
fills and every renderer prints::

    {"writer": "template", "summary": str, "headline": str,
     "titles": {section: str}, "sections": {section: intro},
     "items": {item_id: text}, "setup": {action_id: text}}

Every sentence is written from an item's ``title_facts`` and a section's
counts, never from source text. A secret is a kind and a place: its value is
never in the facts, so it can never be in the text. A rule with no writer of
its own reads :func:`generic_item`, and :func:`has_writer` says which rules
have one, so a new rule is a visible gap rather than a silent placeholder.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from repowise.core.analysis.actions.rules._text import code, plural
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS
from repowise.core.analysis.health.biomarker_labels import biomarker_phrase

from .model import FirstLookReport, Item, SectionBlock

WRITER = "template"

#: Section headings, in report order.
TITLES: dict[str, str] = {
    "security": "Security",
    "performance": "Performance",
    "health": "Code health: refactor first",
    "tests": "Tests and coverage",
    "dead_code": "Dead code",
    "docs": "Docs",
    "decisions": "Decisions",
    "hygiene": "Repo hygiene",
    "ownership": "Ownership",
}

_ROTATE = "Deleting the line is not enough: it stays in git history. Rotate {it} with its provider first."


def _num(value: Any) -> str:
    """A count with thousands separators; a float keeps one decimal."""
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.1f}"
    return f"{int(value or 0):,}"


def _pct(value: Any) -> str:
    return f"{float(value):.1f}%"


def _months(days: Any) -> str:
    months = max(1, round(int(days or 0) / 30))
    return "month" if months == 1 else f"{months} months"


def title(key: str) -> str:
    """The heading of section *key*; a plugin section reads its key."""
    return TITLES.get(key) or key.replace("_", " ").capitalize()


# ---------------------------------------------------------------------------
# Items: one writer per rule
# ---------------------------------------------------------------------------


def _secret(item: Item) -> str:
    f = item.title_facts
    # A group of kinds reads as what they share.
    rules = item.rules or (item.rule,)
    label = SECRET_KINDS.get(item.rule, "credential") if len(rules) == 1 else "hard-coded credential"
    count = int(f.get("locations") or len(item.locations) or 1)
    if count == 1:
        return (
            f"Rotate the {label} (value redacted), then read it from an environment variable."
        )
    return (
        f"Rotate the {_num(count)} {label}s (values redacted), then read them from "
        "environment variables."
    )


def _public_env(item: Item) -> str:
    count = int(item.title_facts.get("locations") or len(item.locations) or 1)
    if count > 1:
        return (
            f"{_num(count)} secrets in public environment variables, values redacted. A web "
            "build ships them to every visitor: read each from a server-side variable instead."
        )
    return (
        "A secret in a public environment variable, value redacted. A web build ships it "
        "to every visitor: read it from a server-side variable instead."
    )


def _perf(item: Item) -> str:
    f = item.title_facts
    symbol = f.get("symbol")
    head = f"{code(symbol)}: {biomarker_phrase(item.rule)}." if symbol else (
        f"{biomarker_phrase(item.rule)[0].upper()}{biomarker_phrase(item.rule)[1:]}."
    )
    tail = []
    if f.get("role") == "request":
        tail.append("It runs on a request path.")
    sites = int(f.get("call_sites") or 0)
    if sites > 1:
        tail.append(f"Reached from {plural(sites, 'call site')}.")
    return " ".join([head, *tail])


#: Fix-first counts quoted after the title: the fact's label, and the noun it counts.
_FIX_FACTS = (
    ("files that import its file", "file", " import{s} it"),
    ("changes in 90 days", "change", " in 90 days"),
)


def _count(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except ValueError:
        return None


def _fix(item: Item) -> str:
    f = item.title_facts
    facts: Mapping[str, Any] = f.get("facts") or {}
    head = str(f.get("title") or f.get("symbol") or "Refactor this code").rstrip(".")
    parts = [head + "."]
    detail = []
    if facts.get("finding") and facts.get("size"):
        size = str(facts["size"])
        # A title that already states the size keeps only the finding's name.
        detail.append(str(facts["finding"]) + ("" if size.split(",")[0] in head else f", {size}"))
    for key, noun, tail in _FIX_FACTS:
        n = _count(facts.get(key))
        if n:
            detail.append(plural(n, noun) + tail.format(s="" if n != 1 else "s"))
    if detail:
        parts.append("; ".join(detail) + ".")
    return " ".join(parts)


def _lowest(item: Item) -> str:
    f = item.title_facts
    lines = f" over {plural(int(f['nloc']), 'line')}" if f.get("nloc") else ""
    return (
        f"Health {f.get('score')}/10{lines}, one of the lowest-scoring production files. "
        "Open its health page for the findings behind the score."
    )


def _fragile(item: Item) -> str:
    f = item.title_facts
    return (
        f"{_pct(f.get('line_pct') or 0)} of lines covered, and "
        f"{_num(f.get('fix_commits_90d'))} of its {plural(int(f.get('commits_90d') or 0), 'commit')} "
        "in 90 days were bug fixes. Add tests before the next change."
    )


def _safe_exports(item: Item) -> str:
    f = item.title_facts
    count = int(f.get("symbols") or item.locations_total or 1)
    lines = f", {plural(int(f['lines']), 'line')}" if f.get("lines") else ""
    them = "it" if count == 1 else "them"
    return (
        f"{plural(count, 'unused export')}{lines}, safe to delete: nothing imports, calls "
        f"or loads {them}. Remove only the {'name' if count == 1 else 'names'} listed."
    )


def _unreachable(item: Item) -> str:
    f = item.title_facts
    lines = f" ({plural(int(f['lines']), 'line')})" if f.get("lines") else ""
    return (
        f"Nothing reaches this file{lines}. Check that nothing loads it by path, then delete it."
    )


def _doc_refs(item: Item) -> str:
    f = item.title_facts
    count = int(f.get("references") or item.locations_total or 1)
    doc = item.locations[0].path if item.locations else None
    # An anchor in the document itself reads as ``#anchor``.
    targets = [
        "#" + t.split("#", 1)[1] if doc and t.startswith(f"{doc}#") else str(t)
        for t in f.get("targets") or ()
    ]
    shown = ", ".join(code(t) for t in targets[:3])
    more = f" and {_num(count - 3)} more" if count > 3 and targets else ""
    if "anchor" in (f.get("kinds") or ()) and "path" not in (f.get("kinds") or ()):
        what = f"{plural(count, 'anchor')} that match no heading"
    else:
        what = f"{plural(count, 'reference')} to something that no longer exists"
    return f"{what[0].upper()}{what[1:]}" + (f": {shown}{more}." if shown else ".")


def _stale_decision(item: Item) -> str:
    name = item.title_facts.get("title")
    which = f'The decision "{name}"' if name else "An accepted decision"
    return f"{which} governs files that changed since it was accepted. Check it still holds."


def _cycle(item: Item) -> str:
    n = int(item.title_facts.get("files") or item.locations_total or 2)
    return (
        f"Import cycle between {_num(n)} files. Move what they share into a module "
        "neither imports back."
    )


def _vendored(item: Item) -> str:
    n = int(item.title_facts.get("files") or item.locations_total or 1)
    them = "it" if n == 1 else "them"
    return (
        f"{plural(n, 'vendored file')} in the code tree. Move {them} under a vendor or "
        f"third_party folder, so tools and reviewers skip {them}."
    )


def _bus_factor(item: Item) -> str:
    f = item.title_facts
    return (
        f"{_num(f.get('bus_factor_1'))} of {plural(int(f.get('components') or 0), 'component')} "
        "rest on one person. Pair a second reviewer on them."
    )


ItemWriter = Callable[[Item], str]

#: Writer per ``(section, rule)``; ``(section, None)`` covers a section whose rule
#: is an open vocabulary (a performance marker, a Fix-first kind).
ITEM_WRITERS: dict[tuple[str, str | None], ItemWriter] = {
    **{("security", kind): _secret for kind in SECRET_KINDS},
    ("security", PUBLIC_ENV_KIND): _public_env,
    ("performance", None): _perf,
    ("health", "lowest_health_file"): _lowest,
    ("health", None): _fix,
    ("tests", "fragile_measured_coverage"): _fragile,
    ("dead_code", "safe_to_delete"): _safe_exports,
    ("dead_code", "unreachable_file"): _unreachable,
    ("docs", "broken_doc_refs"): _doc_refs,
    ("decisions", "stale_decision"): _stale_decision,
    ("hygiene", "import_cycle"): _cycle,
    ("hygiene", "vendored_in_tree"): _vendored,
    ("ownership", "bus_factor"): _bus_factor,
}


def _writer(item: Item) -> ItemWriter | None:
    return ITEM_WRITERS.get((item.section, item.rule)) or ITEM_WRITERS.get((item.section, None))


def has_writer(section: str, rule: str) -> bool:
    """Whether ``(section, rule)`` has text of its own rather than :func:`generic_item`."""
    return (section, rule) in ITEM_WRITERS or (section, None) in ITEM_WRITERS


def generic_item(item: Item) -> str:
    """The text of a rule no writer covers (a plugin section's): its rule and place."""
    where = f" in {code(item.locations[0].path)}" if item.locations else ""
    return f"{item.rule.replace('_', ' ').capitalize()}{where}."


def item_text(item: Item) -> str:
    """The template sentence for *item*."""
    writer = _writer(item)
    return writer(item) if writer is not None else generic_item(item)


# ---------------------------------------------------------------------------
# Section intros: one writer per section, from its counts
# ---------------------------------------------------------------------------


def _security_intro(block: SectionBlock) -> str:
    history = int(block.facts.get("history_only") or 0)
    also = (
        f" {plural(history, 'credential')} also sit in git history only: rotate those too."
        if history
        else ""
    )
    if not block.items:
        return "No live secrets in tracked files." + also
    kinds = {r for i in block.items for r in (i.rules or (i.rule,))}
    if len(kinds) == 1 and next(iter(kinds)) in SECRET_KINDS:
        held = f"a {SECRET_KINDS[next(iter(kinds))]}"
    else:
        held = "a credential"
    files = len(_secret_files(block))
    verb = "holds" if files == 1 else "hold"
    rotate = _ROTATE.format(it="it" if files == 1 else "each one")
    return f"{plural(files, 'tracked file')} {verb} {held}. {rotate}{also}"


def _secret_files(block: SectionBlock) -> set[str]:
    return {loc.path for i in block.items for loc in i.locations}


def _performance_intro(block: SectionBlock) -> str:
    n = len(block.items)
    total = int(block.facts.get("production_causes") or 0)
    of = f", of {_num(total)} found in production code" if total > n else ""
    worth = "is" if n == 1 else "are"
    return f"{plural(n, 'cause')} {worth} worth fixing now{of}."


def _health_intro(block: SectionBlock) -> str:
    accuracy = block.facts.get("defect_accuracy")
    lead = _defect_line(accuracy) + " " if accuracy else ""
    return lead + "Start with the Fix-first items, then the lowest-scoring files."


def _defect_line(stat: Mapping[str, Any]) -> str:
    line = (
        f"{_num(stat['hits'])} of the {_num(stat['k'])} lowest-health files had a bug fix "
        f"in the last {_months(stat.get('window_days'))}"
    )
    if stat.get("lift") is not None and stat.get("base_rate") is not None:
        line += f", {stat['lift']}x the {round(float(stat['base_rate']) * 100)}% baseline"
    return line + "."


def _tests_intro(block: SectionBlock) -> str:
    f = block.facts
    if not f.get("files_measured"):
        return "No coverage report recorded yet."
    parts = []
    if f.get("line_pct") is not None:
        parts.append(f"{_pct(f['line_pct'])} of lines")
    if f.get("branch_pct") is not None:
        parts.append(f"{_pct(f['branch_pct'])} of branches")
    covered = ", ".join(parts) or "coverage"
    text = f"Measured coverage: {covered}, {plural(int(f['files_measured']), 'file')} measured."
    if f.get("partial"):
        text += " Some report paths did not map to files in the index."
    if block.items:
        text += (
            f" {plural(len(block.items), 'file')} where bug fixes keep landing "
            "and measured coverage is lowest:"
        )
    return text


def _dead_intro(block: SectionBlock) -> str:
    f = block.facts
    parts = []
    safe = int(f.get("safe_exports") or 0)
    files = sum(1 for i in block.items if i.rule == "safe_to_delete")
    if safe:
        parts.append(
            f"{plural(safe, 'unused export')} {'is' if safe == 1 else 'are'} safe to delete "
            f"({plural(files, 'file')})"
        )
    unreachable = int(f.get("unreachable_files") or 0)
    if unreachable:
        parts.append(
            f"{plural(unreachable, 'file')} nothing reaches (check each one, then delete)"
        )
    text = (" and ".join(parts) + ".") if parts else ""
    rest = int(f.get("not_listed") or 0)
    if rest:
        text += f" {plural(rest, 'lower-confidence finding')} {'is' if rest == 1 else 'are'} in the app."
    return text.strip()


def _docs_intro(block: SectionBlock) -> str:
    refs = sum(int(i.title_facts.get("references") or 0) for i in block.items)
    n = len(block.items)
    return f"{plural(n, 'document')} {'has' if n == 1 else 'have'} broken references ({_num(refs)} in all)."


def _decisions_intro(block: SectionBlock) -> str:
    n = len(block.items)
    accepted = int(block.facts.get("accepted") or 0)
    of = f" (of {_num(accepted)} accepted)" if accepted >= n else ""
    return f"{plural(n, 'accepted decision')}{of} govern files that changed since. Check each still holds."


def _hygiene_intro(block: SectionBlock) -> str:
    cycles = sum(1 for i in block.items if i.rule == "import_cycle")
    vendored = sum(
        int(i.title_facts.get("files") or 0) for i in block.items if i.rule == "vendored_in_tree"
    )
    parts = []
    if cycles:
        parts.append(f"{plural(cycles, 'import cycle')}.")
    if vendored:
        parts.append(f"{plural(vendored, 'vendored file')} inside the code tree.")
    return " ".join(parts)


def _ownership_intro(block: SectionBlock) -> str:
    n = int(block.facts.get("components") or 0)
    return f"Counted across {plural(n, 'component')}; owners are counted, never named."


SECTION_WRITERS: dict[str, Callable[[SectionBlock], str]] = {
    "security": _security_intro,
    "performance": _performance_intro,
    "health": _health_intro,
    "tests": _tests_intro,
    "dead_code": _dead_intro,
    "docs": _docs_intro,
    "decisions": _decisions_intro,
    "hygiene": _hygiene_intro,
    "ownership": _ownership_intro,
}


def section_text(block: SectionBlock) -> str:
    writer = SECTION_WRITERS.get(block.key)
    if writer is None:
        return f"{plural(len(block.items), 'item')}."
    return writer(block)


# ---------------------------------------------------------------------------
# The summary and the headline
# ---------------------------------------------------------------------------

#: How the summary counts a section: noun for one item, from its items or facts.
_FOUND: dict[str, Callable[[SectionBlock], str | None]] = {
    "security": lambda b: (
        plural(sum(max(i.locations_total, 1) for i in b.items), "live secret") if b.items else None
    ),
    "performance": lambda b: plural(len(b.items), "performance cause") + " worth fixing now",
    "health": lambda b: plural(len(b.items), "refactor-first item"),
    "tests": lambda b: (
        f"{_pct(b.facts['line_pct'])} line coverage" if b.facts.get("line_pct") is not None else None
    ),
    "dead_code": lambda b: _dead_count(b),
    "docs": lambda b: plural(len(b.items), "document") + " with broken references",
    "decisions": lambda b: plural(len(b.items), "stale decision"),
    "hygiene": lambda b: plural(len(b.items), "hygiene item"),
    "ownership": lambda b: plural(len(b.items), "ownership item"),
}


def _dead_count(block: SectionBlock) -> str | None:
    safe = int(block.facts.get("safe_exports") or 0)
    unreachable = int(block.facts.get("unreachable_files") or 0)
    parts = []
    if safe:
        parts.append(f"{plural(safe, 'export')} safe to delete")
    if unreachable:
        parts.append(f"{plural(unreachable, 'file')} nothing reaches")
    return " and ".join(parts) or None


def _summary(report: FirstLookReport) -> str:
    health = report.repo.get("health") or {}
    lead = ""
    if health.get("score") is not None:
        band = f" ({health['band_label']})" if health.get("band_label") else ""
        lead = f"Health is {float(health['score']):.1f}/10{band}. "
    found = [
        text
        for block in report.sections
        if (block.items or block.key == "tests")
        and (text := _FOUND.get(block.key, lambda b: plural(len(b.items), "item"))(block))
    ]
    if not found:
        return lead + "Nothing to act on was found at the precision this report lists."
    first = report.item(report.top[0]) if report.top else None
    then = f" {title(first.section).split(':')[0]} comes first." if first else ""
    return f"{lead}Found: {', '.join(found)}.{then}"


def _headline(report: FirstLookReport) -> str:
    for fact in report.repo.get("headline_facts") or ():
        if fact.get("id") == "defect_accuracy":
            return _defect_line(fact)
    return ""


def template_prose(report: FirstLookReport) -> dict[str, Any]:
    """The template text for every section, item and setup step of *report*."""
    return {
        "writer": WRITER,
        "summary": _summary(report),
        "headline": _headline(report),
        "titles": {b.key: title(b.key) for b in report.sections},
        "sections": {b.key: section_text(b) for b in report.sections},
        "items": {i.id: item_text(i) for i in report.items()},
        "setup": {s.action_id: s.title for s in report.setup},
    }


__all__ = [
    "ITEM_WRITERS",
    "SECTION_WRITERS",
    "TITLES",
    "WRITER",
    "generic_item",
    "has_writer",
    "item_text",
    "section_text",
    "template_prose",
    "title",
]
