"""Grouping, order inside a section, the section order, and the "Do these first" three.

Items of one section whose rules share a family (one fix covers them, as a
hard-coded password and a hard-coded secret are both rotated) become one item
with every location (:func:`group_items`). Inside a section: basis (measured,
inferred, declared), then the action tier, then ``shared_priority`` (value x
confidence / effort, the number Do next orders a tier on), then ``RULE_RANK``,
then the rule's own weight. Pure.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Iterable, Sequence

from repowise.core.analysis.actions.model import RULE_RANK, fingerprint
from repowise.core.analysis.actions.rules.hygiene import PUBLIC_ENV_KIND, SECRET_KINDS

from .model import BASES, SECTIONS, SEVERITIES, Item, PromptRef, SectionBlock, item_id, locations

#: Rules one fix covers, by family. A section's items in one family merge into
#: one item, whose id is the family's (no path, no line), so it holds while
#: files join or leave the group.
RULE_FAMILIES: dict[str, str] = {
    **dict.fromkeys(SECRET_KINDS, "credentials"),
    PUBLIC_ENV_KIND: "public_env_secrets",
}

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


def _merge(section: str, family: str, found: Sequence[Item]) -> Item:
    """*found* (one family, one section) as one item: the best-ranked member is
    primary and every location is kept, in path order."""
    ranked = order_items(found)
    primary = ranked[0]
    by_path = sorted(ranked, key=lambda i: (i.locations[0].path if i.locations else "", i.id))
    locs, _ = locations([loc for i in by_path for loc in i.locations])
    total = sum(max(i.locations_total, len(i.locations)) for i in ranked)
    rules = tuple(dict.fromkeys(r for i in ranked for r in (i.rules or (i.rule,))))
    return dataclasses.replace(
        primary,
        id=item_id(section, family, ""),
        rules=rules,
        title_facts={
            **primary.title_facts,
            "family": family,
            "kinds": list(rules),
            "files": len({loc.path for i in ranked for loc in i.locations}),
            "locations": total,
        },
        locations=locs,
        locations_total=total,
        basis=min((i.basis for i in ranked), key=BASES.index),
        severity=min((i.severity for i in ranked), key=SEVERITIES.index),
        priority=max(i.priority for i in ranked),
        weight=sum(i.weight for i in ranked),
        # One member keeps its own prompt; a group's covers every location.
        prompt=primary.prompt if len(ranked) == 1 else PromptRef("first_look_item"),
        fingerprint=fingerprint(*sorted(i.fingerprint for i in ranked)),
        members=tuple(m for i in by_path for m in i.members),
    )


def group_items(items: Iterable[Item]) -> tuple[Item, ...]:
    """*items* with each section's family members merged (:data:`RULE_FAMILIES`)."""
    out: list[Item] = []
    families: dict[tuple[str, str], list[Item]] = {}
    for item in items:
        family = RULE_FAMILIES.get(item.rule)
        if family is None:
            out.append(item)
        else:
            families.setdefault((item.section, family), []).append(item)
    return (*out, *(_merge(s, f, found) for (s, f), found in families.items()))


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
    take(sorted((i for i in everything if i.severity == "act_now"), key=item_key))
    for basis in BASES:
        take(
            sorted(
                (i for i in everything if i.basis == basis and i.severity != "act_now"),
                key=lambda i: (-i.priority, section_key(i.section), item_key(i)),
            )
        )
    return tuple(picked)


__all__ = [
    "RULE_FAMILIES",
    "TOP_ITEMS",
    "group_items",
    "item_key",
    "order_items",
    "section_key",
    "top_items",
]
