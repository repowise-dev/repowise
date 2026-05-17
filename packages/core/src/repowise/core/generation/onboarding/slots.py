"""Onboarding slot identifiers and reading order.

The Onboarding collection is a fixed set of eight curated pages. Two slots are
"promoted" — they reuse the existing `repo_overview` and `architecture_diagram`
pages, tagged via ``metadata.onboarding_slot``. The other six are new pages
with ``page_type='onboarding'`` and a ``metadata.subkind`` discriminator.

Slot identifiers are used three ways:
  - as the ``metadata.subkind`` value on generated pages,
  - as the trailing path of ``target_path = f"onboarding/{slot}"``,
  - as the ordering key in the web UI's Onboarding folder.

Keep the order list in lockstep with ``packages/ui/src/lib/page-types.ts``
``ONBOARDING_ORDER``.
"""

from __future__ import annotations

# ---- Slot identifiers ----

SLOT_PROJECT_OVERVIEW = "project_overview"
SLOT_ARCHITECTURE_GUIDE = "architecture_guide"
SLOT_GETTING_STARTED = "getting_started"
SLOT_CODEBASE_MAP = "codebase_map"
SLOT_KEY_CONCEPTS = "key_concepts"
SLOT_HOW_IT_WORKS = "how_it_works"
SLOT_DEVELOPMENT_GUIDE = "development_guide"
SLOT_ACTIVE_LANDSCAPE = "active_landscape"

# Fixed reading order. Slots not yet implemented are silently skipped at
# generation time and absent from the UI tree.
ONBOARDING_ORDER: tuple[str, ...] = (
    SLOT_PROJECT_OVERVIEW,
    SLOT_ARCHITECTURE_GUIDE,
    SLOT_GETTING_STARTED,
    SLOT_CODEBASE_MAP,
    SLOT_KEY_CONCEPTS,
    SLOT_HOW_IT_WORKS,
    SLOT_DEVELOPMENT_GUIDE,
    SLOT_ACTIVE_LANDSCAPE,
)

# Maps existing page_type → onboarding slot. The generator tags these pages
# with ``metadata.onboarding_slot`` after they're produced at level 6; no
# extra content is generated for promoted slots.
PROMOTED_SLOTS: dict[str, str] = {
    "repo_overview": SLOT_PROJECT_OVERVIEW,
    "architecture_diagram": SLOT_ARCHITECTURE_GUIDE,
}

# Human-readable titles used both server-side (page title) and as a fallback
# label in the UI when a page hasn't been hydrated yet.
SLOT_TITLES: dict[str, str] = {
    SLOT_PROJECT_OVERVIEW: "Project Overview",
    SLOT_ARCHITECTURE_GUIDE: "Architecture Guide",
    SLOT_GETTING_STARTED: "Getting Started",
    SLOT_CODEBASE_MAP: "Codebase Map",
    SLOT_KEY_CONCEPTS: "Key Concepts",
    SLOT_HOW_IT_WORKS: "How It Works",
    SLOT_DEVELOPMENT_GUIDE: "Development Guide",
    SLOT_ACTIVE_LANDSCAPE: "Active Landscape",
}


def target_path(slot: str) -> str:
    """Canonical wiki ``target_path`` for an onboarding slot."""
    return f"onboarding/{slot}"
