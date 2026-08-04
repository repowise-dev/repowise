"""Onboarding slot identifiers and reading order.

The Onboarding collection is a fixed set of six curated slots. One slot is
"promoted": it reuses the existing `repo_overview` page, tagged via
``metadata.onboarding_slot``. The other five are new pages with
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
``display_order``, assigned from this tuple at generation time.

A slot without enough signal is skipped at generation time, so a repo can end
up with fewer than five.

Retiring a slot is not just a deletion here. The pages already written into
every existing index have to be retired too, or they keep being served: see
``generation.page_redirects`` for where their ids now send a reader, and
``pipeline.persist.sweep_retired_pages`` for how the rows leave a store
that was built before the cut.
"""

from __future__ import annotations

# Generation version for onboarding pages. Folded into every onboarding
# page's source_hash, so bumping it forces a one-time regeneration of all
# onboarding slots on an existing user's next docs update - even when the
# rendered prompt is byte-identical. Bump when a builder or template change
# should reach already-cached pages. (File pages have an equivalent in
# ``_generation_fingerprint``; onboarding pages had none until this.)
ONBOARDING_GENERATION_VERSION = "3"

# ---- Slot identifiers ----

SLOT_PROJECT_OVERVIEW = "project_overview"
SLOT_GETTING_STARTED = "getting_started"
SLOT_KEY_CONCEPTS = "key_concepts"
SLOT_HOW_IT_WORKS = "how_it_works"
SLOT_ACTIVE_LANDSCAPE = "active_landscape"
SLOT_GLOSSARY = "glossary"

# Fixed reading order. Slots not yet implemented are silently skipped at
# generation time and absent from the UI tree.
#
# The glossary is last on purpose: it is a lookup surface rather than a reading
# step, and a reader who does not yet know the vocabulary is not helped by
# meeting all of it at once.
ONBOARDING_ORDER: tuple[str, ...] = (
    SLOT_PROJECT_OVERVIEW,
    SLOT_GETTING_STARTED,
    SLOT_KEY_CONCEPTS,
    SLOT_HOW_IT_WORKS,
    SLOT_ACTIVE_LANDSCAPE,
    SLOT_GLOSSARY,
)

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
    SLOT_KEY_CONCEPTS: "Key Concepts",
    SLOT_HOW_IT_WORKS: "How It Works",
    SLOT_ACTIVE_LANDSCAPE: "Active Landscape",
    SLOT_GLOSSARY: "Glossary",
}


def target_path(slot: str) -> str:
    """Canonical wiki ``target_path`` for an onboarding slot."""
    return f"onboarding/{slot}"
