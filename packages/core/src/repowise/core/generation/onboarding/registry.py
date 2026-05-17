"""Registry of onboarding subkind specifications.

Each subkind module declares a :class:`SubkindSpec` and calls :func:`register`
on import. The dispatcher in :mod:`page_generator` iterates :func:`iter_specs`
to drive generation — adding a new subkind never requires changes to the
dispatcher itself.

A subkind's ``build_context`` is allowed to return ``None`` when the slot's
gate (e.g. "needs >= 50 commits") fails for this repo. The dispatcher then
silently skips the slot — both the page and any UI nav entry.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from .signals import OnboardingSignals
from .slots import ONBOARDING_ORDER, PROMOTED_SLOTS

# A builder returns either a context object the template can render, or None
# to indicate the gate failed and the slot should be skipped for this repo.
BuildContext = Callable[[OnboardingSignals], object | None]


@dataclass(frozen=True)
class SubkindSpec:
    """One onboarding subkind.

    Attributes:
        slot:          Slot identifier from :mod:`onboarding.slots`.
        title:         Display title for the generated page.
        template:      Jinja template filename, relative to
                       ``templates/onboarding/``.
        build_context: Returns the template context, or ``None`` to skip.
    """

    slot: str
    title: str
    template: str
    build_context: BuildContext


_REGISTRY: dict[str, SubkindSpec] = {}


def register(spec: SubkindSpec) -> None:
    """Register a subkind spec. Idempotent re-registration is rejected."""
    if spec.slot in PROMOTED_SLOTS.values():
        # Defensive: promoted slots are not generated through this path.
        raise ValueError(
            f"Slot '{spec.slot}' is promoted and must not be registered "
            "as a generated subkind."
        )
    if spec.slot in _REGISTRY:
        raise ValueError(f"Duplicate onboarding subkind: {spec.slot}")
    if spec.slot not in ONBOARDING_ORDER:
        raise ValueError(
            f"Unknown onboarding slot '{spec.slot}'. "
            f"Add it to ONBOARDING_ORDER in slots.py first."
        )
    _REGISTRY[spec.slot] = spec


def get_spec(slot: str) -> SubkindSpec | None:
    """Return the spec for a slot, or ``None`` if unregistered."""
    return _REGISTRY.get(slot)


def iter_specs() -> list[SubkindSpec]:
    """Return registered specs in canonical reading order.

    Promoted slots (project_overview, architecture_guide) are not generated
    through this path and are excluded here.
    """
    promoted = set(PROMOTED_SLOTS.values())
    return [
        _REGISTRY[slot]
        for slot in ONBOARDING_ORDER
        if slot not in promoted and slot in _REGISTRY
    ]
