"""Onboarding documentation collection.

A curated set of up to eight pages — Project Overview, Architecture Guide,
Getting Started, Codebase Map, Key Concepts, How It Works, Development Guide,
Active Landscape — designed to be the first thing a new contributor (or LLM
agent) reads.

Two slots ("project_overview", "architecture_guide") are *promoted*: they
reuse the existing ``repo_overview`` and ``architecture_diagram`` pages,
tagged via ``metadata.onboarding_slot``. The other six slots are new pages
generated at level 8 with ``page_type='onboarding'`` and a
``metadata.subkind`` discriminator.

Architecture:
  - :mod:`slots`     — slot identifiers, fixed reading order, promoted map.
  - :mod:`signals`   — typed bundle of inputs passed to every subkind builder.
  - :mod:`registry`  — :class:`SubkindSpec` + register/get/iter API.
  - :mod:`subkinds`  — subkind modules that register themselves on import.
"""

from .registry import SubkindSpec, get_spec, iter_specs, register
from .signals import OnboardingSignals
from .slots import (
    ONBOARDING_ORDER,
    PROMOTED_SLOTS,
    SLOT_ACTIVE_LANDSCAPE,
    SLOT_ARCHITECTURE_GUIDE,
    SLOT_CODEBASE_MAP,
    SLOT_DEVELOPMENT_GUIDE,
    SLOT_GETTING_STARTED,
    SLOT_HOW_IT_WORKS,
    SLOT_KEY_CONCEPTS,
    SLOT_PROJECT_OVERVIEW,
    SLOT_TITLES,
    target_path,
)

# Side-effect import: registers every implemented subkind.
from . import subkinds  # noqa: E402, F401

__all__ = [
    "ONBOARDING_ORDER",
    "OnboardingSignals",
    "PROMOTED_SLOTS",
    "SLOT_ACTIVE_LANDSCAPE",
    "SLOT_ARCHITECTURE_GUIDE",
    "SLOT_CODEBASE_MAP",
    "SLOT_DEVELOPMENT_GUIDE",
    "SLOT_GETTING_STARTED",
    "SLOT_HOW_IT_WORKS",
    "SLOT_KEY_CONCEPTS",
    "SLOT_PROJECT_OVERVIEW",
    "SLOT_TITLES",
    "SubkindSpec",
    "get_spec",
    "iter_specs",
    "register",
    "target_path",
]
