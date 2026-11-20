"""Order inside a section, the section order, and the "Do these first" three.

Inside a section: basis (measured, inferred, declared), then the action tier,
then ``shared_priority`` (value x confidence / effort, the number Do next
orders a tier on), then ``RULE_RANK``, then the rule's own weight. Pure.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from repowise.core.analysis.actions.model import RULE_RANK

from .model import BASES, SECTIONS, SEVERITIES, Item, SectionBlock

#: Items "Do these first" holds, at most one per section.
TOP_ITEMS = 3

_BASIS = {b: i for i, b in enumerate(BASES)}
_SEVERITY = {s: i for i, s in enumerate(SEVERITIES)}
_SECTION = {s: i for i, s in enumerate(SECTIONS)}


def item_key(item: Item) -> tuple:
    return (
        _BASIS[item.basis],
        _SEVERITY[item.severity],
        -item.priority,
        RULE_RANK.get(item.rule, len(RULE_RANK)),
        -item.weight,
        item.id,
    )


def order_items(items: Iterable[Item]) -> tuple[Item, ...]:
    return tuple(sorted(items, key=item_key))


def section_key(key: str) -> int:
    """Built-in sections in their fixed order; a plugin's after them."""
    return _SECTION.get(key, len(_SECTION))


def top_items(sections: Sequence[SectionBlock], limit: int = TOP_ITEMS) -> tuple[str, ...]:
    """Every ``act_now`` item first, then the highest priority measured, then
    inferred, then declared; one per section, so the summary shows breadth.

    *sections* are in report order with their items ranked.
    """
    picked: list[str] = []
    used: set[str] = set()

    def take(candidates: Iterable[Item]) -> None:
        for item in candidates:
            if len(picked) >= limit:
                return
            if item.section not in used:
                picked.append(item.id)
                used.add(item.section)

    everything = [i for s in sections for i in s.items]
    take(i for i in everything if i.severity == "act_now")
    for basis in BASES:
        take(
            sorted(
                (i for i in everything if i.basis == basis and i.severity != "act_now"),
                key=lambda i: (-i.priority, section_key(i.section), item_key(i)),
            )
        )
    return tuple(picked)


__all__ = ["TOP_ITEMS", "item_key", "order_items", "section_key", "top_items"]
