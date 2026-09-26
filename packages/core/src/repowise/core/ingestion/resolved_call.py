"""The edge record call resolution emits for one resolved call site."""

from __future__ import annotations

from dataclasses import dataclass

from .models import CallSiteEdgeType, ResolutionOrigin


@dataclass(frozen=True, slots=True)
class ResolvedCall:
    """A call resolved to concrete symbol IDs with a confidence score."""

    caller_id: str  # symbol node ID of the calling function/method
    callee_id: str  # symbol node ID of the called function/method
    confidence: float  # 0.0–1.0
    line: int  # call site line number (for diagnostics)
    origin: ResolutionOrigin  # which strategy produced it
    edge_type: CallSiteEdgeType = "calls"  # carried through from the CallSite
    supplied_props: frozenset[str] | None = None  # prop names supplied in JSX element (None if unknown/spread)
