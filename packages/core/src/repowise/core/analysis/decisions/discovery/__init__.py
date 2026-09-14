"""Broad, grounded, one-call decision discovery over transcript deltas.

The deterministic gates in :mod:`repowise.core.sessions.miners.decisions` are
a high-precision lane and stay exactly as they are. This package is the recall
lane beside them: one model pass per update over the new session prose those
gates never show anybody, with every candidate grounded in a quote from a span
it cites, and nothing it produces able to govern.

Boundaries, in dependency order: :mod:`spans` acquires evidence,
:mod:`packet` decides what one call sees, :mod:`grounding` decides what
survives, :mod:`runner` is the only thing that talks to a provider.
"""

from repowise.core.analysis.decisions.discovery.grounding import (
    GroundedCandidate,
    ground_candidates,
    parse_response,
)
from repowise.core.analysis.decisions.discovery.packet import (
    DiscoveryPacket,
    build_packet,
)
from repowise.core.analysis.decisions.discovery.runner import (
    DISCOVERY_SOURCE,
    DiscoveryOutcome,
    DiscoveryReport,
    run_update_discovery,
)
from repowise.core.analysis.decisions.discovery.spans import ProseSpan, SpanCollector

__all__ = [
    "DISCOVERY_SOURCE",
    "DiscoveryOutcome",
    "DiscoveryPacket",
    "DiscoveryReport",
    "GroundedCandidate",
    "ProseSpan",
    "SpanCollector",
    "build_packet",
    "ground_candidates",
    "parse_response",
    "run_update_discovery",
]
