"""Graph serialisation: node-link JSON.

Mixed into :class:`GraphBuilder`. The main pipeline persists through
``persistence.crud``; this module only produces the in-memory node-link dict
that callers dump to an artifact.
"""

from __future__ import annotations

from typing import Any

import networkx as nx
import structlog

log = structlog.get_logger(__name__)


class SerializeMixin:
    """JSON serialisation for :class:`GraphBuilder`."""

    def to_json(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict (node-link format)."""
        return nx.node_link_data(self.graph())
