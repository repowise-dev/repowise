"""Onboarding documentation collection.

A curated set of pages: Project Overview, Getting Started and How It Works,
the first thing a new contributor (or LLM agent) reads, plus Active Landscape,
which sits outside that reading order.

One slot ("project_overview") is *promoted*: it reuses the existing
``repo_overview`` page, tagged via ``metadata.onboarding_slot``. The other
slots are pages generated at level 8 with ``page_type='onboarding'`` and a
``metadata.subkind`` discriminator.

Architecture:
  - :mod:`slots`     — slot identifiers, fixed reading order, promoted map.
  - :mod:`signals`   — typed bundle of inputs passed to every subkind builder.
  - :mod:`registry`  — :class:`SubkindSpec` + register/get/iter API.
  - :mod:`subkinds`  — subkind modules that register themselves on import.
"""

# Side-effect import: registers every implemented subkind.
from . import subkinds  # noqa: F401
from .grounding import check_grounding
from .registry import SubkindSpec, get_spec, iter_specs, register
from .signals import OnboardingSignals
from .slots import (
    ONBOARDING_GENERATION_VERSION,
    ONBOARDING_ORDER,
    ONBOARDING_SLOTS,
    PROMOTED_SLOTS,
    SLOT_ACTIVE_LANDSCAPE,
    SLOT_GETTING_STARTED,
    SLOT_HOW_IT_WORKS,
    SLOT_PROJECT_OVERVIEW,
    SLOT_TITLES,
    next_slot,
    slot_metadata,
    target_path,
    with_next_link,
)

__all__ = [
    "ONBOARDING_GENERATION_VERSION",
    "ONBOARDING_ORDER",
    "ONBOARDING_SLOTS",
    "PROMOTED_SLOTS",
    "SLOT_ACTIVE_LANDSCAPE",
    "SLOT_GETTING_STARTED",
    "SLOT_HOW_IT_WORKS",
    "SLOT_PROJECT_OVERVIEW",
    "SLOT_TITLES",
    "OnboardingSignals",
    "SubkindSpec",
    "check_grounding",
    "get_spec",
    "iter_specs",
    "next_slot",
    "register",
    "slot_metadata",
    "target_path",
    "with_next_link",
]
