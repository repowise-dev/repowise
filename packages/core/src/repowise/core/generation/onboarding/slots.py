"""Onboarding slot identifiers and reading order.

The Onboarding collection is a fixed set of curated slots. One slot is
"promoted": it reuses the existing `repo_overview` page, tagged via
``metadata.onboarding_slot``. The others are pages with
``page_type='onboarding'`` and a ``metadata.subkind`` discriminator.

Slot identifiers are used three ways:
  - as the ``metadata.subkind`` value on generated pages,
  - as the trailing path of ``target_path = f"onboarding/{slot}"``,
  - as the ordering key in the web UI's Onboarding folder.

Every slot here must also appear in ``ONBOARDING_SLOT_TITLES`` in
``packages/ui/src/lib/page-types.ts``: that map gates whether a page is placed
in the Onboarding folder at all, and a slot missing from it falls through to
path-based grouping and surfaces as a stray ``onboarding/`` directory. The
reading *order* is not duplicated there — it arrives on the pages themselves as
``display_order``, assigned from this module at generation time.

A slot without enough signal is skipped at generation time, so a repo can end
up with fewer pages than slots.

Retiring a slot is not just a deletion here. The pages already written into
every existing index have to be retired too, or they keep being served: see
``generation.page_redirects`` for where their ids now send a reader, and
``pipeline.persist.sweep_retired_pages`` for how the rows leave a store
that was built before the cut.
"""

from __future__ import annotations

import re

# Generation version for onboarding pages. Folded into every onboarding
# page's source_hash, so bumping it forces a one-time regeneration of all
# onboarding slots on an existing user's next docs update - even when the
# rendered prompt is byte-identical. Bump when a builder or template change
# should reach already-cached pages. (File pages have an equivalent in
# ``_generation_fingerprint``; onboarding pages had none until this.)
ONBOARDING_GENERATION_VERSION = "6"

# ---- Slot identifiers ----

SLOT_PROJECT_OVERVIEW = "project_overview"
SLOT_GETTING_STARTED = "getting_started"
SLOT_HOW_IT_WORKS = "how_it_works"
SLOT_ACTIVE_LANDSCAPE = "active_landscape"

# Fixed reading order: the pages a newcomer reads first, each one's "Next"
# link pointing at the following one that exists.
ONBOARDING_ORDER: tuple[str, ...] = (
    SLOT_PROJECT_OVERVIEW,
    SLOT_GETTING_STARTED,
    SLOT_HOW_IT_WORKS,
)

# Every slot, in display order. A slot past the reading order is kept out of
# it: no "Next" link leads to or from it, and the docs tree places it in its
# project activity section, read from ``metadata.nav_section`` (see
# :func:`slot_metadata`).
ONBOARDING_SLOTS: tuple[str, ...] = (*ONBOARDING_ORDER, SLOT_ACTIVE_LANDSCAPE)

#: The ``metadata.nav_section`` value of a slot outside the reading order.
NAV_SECTION_ACTIVITY = "project_activity"

# Maps existing page_type → onboarding slot. The generator tags these pages
# with ``metadata.onboarding_slot`` after they're produced at level 6; no
# extra content is generated for promoted slots.
PROMOTED_SLOTS: dict[str, str] = {
    "repo_overview": SLOT_PROJECT_OVERVIEW,
}

# Human-readable titles used both server-side (page title) and as a fallback
# label in the UI when a page hasn't been hydrated yet.
SLOT_TITLES: dict[str, str] = {
    SLOT_PROJECT_OVERVIEW: "Project Overview",
    SLOT_GETTING_STARTED: "Getting Started",
    SLOT_HOW_IT_WORKS: "How It Works",
    SLOT_ACTIVE_LANDSCAPE: "Active Landscape",
}


def target_path(slot: str) -> str:
    """Canonical wiki ``target_path`` for an onboarding slot."""
    return f"onboarding/{slot}"


def slot_metadata(slot: str) -> dict[str, str]:
    """The metadata every generated page of *slot* carries."""
    meta = {"subkind": slot, "onboarding_slot": slot}
    if slot not in ONBOARDING_ORDER:
        meta["nav_section"] = NAV_SECTION_ACTIVITY
    return meta


#: A trailing "Next:" line, matched so a reused page gets the current one.
_NEXT_LINE = re.compile(r"\n*^Next: \*\*[^*\n]+\*\*[ \t]*$\n?", re.MULTILINE)
#: The provenance footer a structure-only page ends with.
_FOOTER = "\n---\n"


def next_slot(slot: str, present: set[str]) -> str | None:
    """The slot a reader opens after *slot*: the next one in reading order that exists.

    ``None`` for a slot outside the reading order.
    """
    if slot not in ONBOARDING_ORDER:
        return None
    after = ONBOARDING_ORDER[ONBOARDING_ORDER.index(slot) + 1 :]
    return next((s for s in after if s in present), None)


def with_next_link(content: str, title: str | None) -> str:
    """*content* ending with one ``Next: **<title>**`` line, above any provenance footer.

    Idempotent: an earlier Next line is replaced, so a reused page carries the
    current reading order rather than the one it was written under.
    """
    body = _NEXT_LINE.sub("\n", content).rstrip() + "\n"
    if not title:
        return body
    line = f"\nNext: **{title}**\n"
    cut = body.rfind(_FOOTER)
    if cut == -1:
        return body + line
    return body[:cut].rstrip() + "\n" + line + body[cut:]
