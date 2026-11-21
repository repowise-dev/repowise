"""The repo report: what Repowise found after the first index, in order.

Built from structured findings only (:mod:`.collect`), as one versioned JSON
document (:mod:`.model`) stored in the checkout and the index (:mod:`.store`),
compared with the index as it moves (:mod:`.staleness`), with each item's agent
prompt rendered on demand (:mod:`.prompts`). Sections are providers on the
``first-look-section`` seam (:mod:`.sections`).
"""

from __future__ import annotations

from .model import (
    ROUTE_KEYS,
    SCHEMA_VERSION,
    SECTION_CAP,
    SECTIONS,
    Collected,
    FirstLookReport,
    FirstLookSection,
    Item,
    Link,
    Location,
    Member,
    PromptRef,
    SectionBlock,
    SetupStep,
    Stamp,
    first_look_sections,
    item_id,
)

__all__ = [
    "ROUTE_KEYS",
    "SCHEMA_VERSION",
    "SECTIONS",
    "SECTION_CAP",
    "Collected",
    "FirstLookReport",
    "FirstLookSection",
    "Item",
    "Link",
    "Location",
    "Member",
    "PromptRef",
    "SectionBlock",
    "SetupStep",
    "Stamp",
    "first_look_sections",
    "item_id",
]
