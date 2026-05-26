"""Onboarding subkind: Guided Tour.

A single pedagogical page that narrates the topology-driven tour — an ordered
walk that starts at the entry points and follows the import graph inward,
closing on how the system is built and deployed. The *ordering* is computed
deterministically upstream (``generation.tour``) and arrives here as
``signals.tour_stops``; this subkind hands those ordered stops (each already
pointing at a real wiki page, with its one-line summary) to the LLM, which
writes the connective narration that ties one step to the next.

Gate: skips when no tour could be built (e.g. a repo with no documented
files), so the slot simply doesn't appear.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..registry import SubkindSpec, register
from ..signals import OnboardingSignals
from ..slots import SLOT_GUIDED_TOUR, SLOT_TITLES

# A tour with only the overview stop isn't worth a page on its own.
_MIN_STOPS = 2


@dataclass
class TourStopContext:
    """One stop as the template sees it — ordered page reference + summary."""

    order: int
    target_path: str
    page_type: str
    title: str
    kind: str
    reason: str
    summary: str


@dataclass
class GuidedTourContext:
    repo_name: str
    stops: list[TourStopContext] = field(default_factory=list)
    layer_order: list[str] = field(default_factory=list)


def _build(signals: OnboardingSignals) -> GuidedTourContext | None:
    if len(signals.tour_stops) < _MIN_STOPS:
        return None

    stops: list[TourStopContext] = []
    for s in signals.tour_stops:
        target = s.get("target_path", "")
        # The overview's summary is keyed by repo_name; file/infra pages by path.
        summary = (signals.completed_page_summaries.get(target) or "").strip()[:240]
        stops.append(
            TourStopContext(
                order=int(s.get("order", len(stops) + 1)),
                target_path=target,
                page_type=s.get("page_type", "file_page"),
                title=s.get("title", target),
                kind=s.get("kind", "code"),
                reason=s.get("reason", ""),
                summary=summary,
            )
        )

    return GuidedTourContext(
        repo_name=signals.repo_name,
        stops=stops,
        layer_order=list(signals.layer_order),
    )


register(
    SubkindSpec(
        slot=SLOT_GUIDED_TOUR,
        title=SLOT_TITLES[SLOT_GUIDED_TOUR],
        template="guided_tour.j2",
        build_context=_build,
    )
)
