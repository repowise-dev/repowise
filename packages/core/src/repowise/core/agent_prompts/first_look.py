"""First-look prompts: one item no other producer covers, and the whole report.

An item comes as its wire dict (``first_look.model.Item.to_json()``): the
facts and locations the report holds, never source text. The whole-report
prompt walks the items in report order, one at a time, and stops on the same
rule the setup loop stops on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from .preamble import preamble
from .sections import and_more, bullet_list, closing_sections, count, join_sections, repo_suffix

#: What finished looks like, per item rule; a rule not listed reads the default.
DONE_WHEN: dict[str, str] = {
    "import_cycle": "The files no longer import each other, directly or through one another.",
    "vendored_in_tree": "Each vendored file sits under a vendor or third_party folder, or is gone.",
    "unreachable_file": "Each file is deleted after you confirmed nothing loads it, or kept with the reason.",
    "fragile_measured_coverage": "The file's bug-fix paths are covered by tests that fail without the fix.",
    "lowest_health_file": "The file's worst findings are fixed and its tests still pass.",
    "bus_factor": "A second person has reviewed or changed the files only one person knows.",
}
_DONE_DEFAULT = "The item no longer holds on the next `repowise update`."

_CONSTRAINTS = (
    "**Confirm the item before changing anything.** The report is stored, not live; the code may have moved since it was written.",
    "**Stay inside the files the item names.** If the right fix is elsewhere, stop and say where and why.",
    "**Keep behaviour.** Run the tests that cover each file before and after.",
    "**Say when the item is wrong.** Report it rather than forcing an edit.",
)

_EXPECTED = (
    "1. Whether the item held, and what you changed or why you left it.",
    "2. The tests you ran, before and after.",
)


def _fact(value: Any) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, list | tuple):
        return ", ".join(str(v) for v in value)
    return str(value)


def _where(loc: Mapping[str, Any]) -> str:
    line = loc.get("line")
    symbol = loc.get("symbol")
    return f"- `{loc['path']}{f':{line}' if line else ''}`" + (f": `{symbol}`" if symbol else "")


def render_first_look_item(
    item: Mapping[str, Any], flavor: str = "generic", repo_name: str | None = None
) -> str:
    locations = item.get("locations") or []
    more = int(item.get("locations_total") or len(locations)) - len(locations)
    facts = item.get("title_facts") or {}
    return join_sections(
        [
            preamble(flavor, "first_look_item"),
            "",
            f"## First-look item{repo_suffix(repo_name)}",
            "",
            f"**{item['rule'].replace('_', ' ')}** ({item['section'].replace('_', ' ')})",
            "",
            "## What the report knows",
            "",
            bullet_list(
                [
                    *(f"{k.replace('_', ' ')}: {_fact(v)}" for k, v in facts.items() if v not in (None, "", [])),
                    f"Basis: {item['basis']}; confidence: {item['confidence']}; effort estimate: {item['effort']}",
                ]
            ),
            "",
            *(
                [
                    "## Where",
                    "",
                    "\n".join(_where(loc) for loc in locations),
                    and_more(more, prefix="\n"),
                    "",
                ]
                if locations
                else []
            ),
            "## Done when",
            "",
            DONE_WHEN.get(item["rule"], _DONE_DEFAULT),
            "",
            *closing_sections(_CONSTRAINTS, _EXPECTED),
        ]
    )


_REPORT_LOOP = (
    "Work through the items below in the order listed, one at a time. For each one: "
    "confirm it against the code, make the smallest change that resolves it, run the "
    "tests that cover the files you touched, then move to the next. Rotate a leaked "
    "credential with its provider before you remove it from the file; deleting it does "
    "not remove it from git history. Do not spend money, and do not turn on anything "
    "that sends code to a model, without asking. Stop when the list is done, or when an "
    "item repeats one you already handled."
)


def render_first_look_report(
    items: Sequence[Mapping[str, Any]], flavor: str = "generic", repo_name: str | None = None
) -> str:
    """The whole report as one task list, in report order (*items* as served)."""

    def line(i: int, item: Mapping[str, Any]) -> str:
        loc = (item.get("locations") or [{}])[0]
        at = f":{loc['line']}" if loc.get("line") else ""
        where = f" `{loc['path']}{at}`" if loc.get("path") else ""
        return f"{i}. [{item['section'].replace('_', ' ')}] {item['rule'].replace('_', ' ')}{where} (`{item['id']}`)"

    return join_sections(
        [
            preamble(flavor, "first_look_report"),
            "",
            f"## First-look report{repo_suffix(repo_name)}",
            "",
            _REPORT_LOOP,
            "",
            f"## Items ({count(len(items))})",
            "",
            "\n".join(line(i, item) for i, item in enumerate(items, 1)),
            "",
            *closing_sections(
                _CONSTRAINTS,
                (
                    "1. Per item: held or not, what you changed, the tests you ran.",
                    "2. The items you skipped, and why.",
                ),
            ),
        ]
    )


__all__ = ["DONE_WHEN", "render_first_look_item", "render_first_look_report"]
