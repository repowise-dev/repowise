"""Quarkus framework edges.

Quarkus reuses much of Jakarta (CDI scopes, JAX-RS) but layers its own
conventions on top — ``@QuarkusTest`` entry-points, ``@ConfigMapping``
config interfaces, ``@RegisterRestClient`` MicroProfile clients,
``@Scheduled`` tasks, and ``@Incoming("topic") / @Outgoing("topic")``
SmallRye reactive-messaging channels. This handler emits edges for the
Quarkus-specific shapes; CDI / JAX-RS classes are caught by the
``jakarta`` handler too.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_QUARKUS_ENTRY_ANNOT = (
    "@QuarkusMain",
    "@QuarkusTest",
    "@QuarkusIntegrationTest",
    "@QuarkusUnitTest",
    "@NativeImageTest",
    "@ConfigMapping",
    "@RegisterRestClient",
    "@GraphQLApi",
    "@RegisterForReflection",
    "@Scheduled",
)

_INCOMING_RE = re.compile(r"@Incoming\s*\(\s*[\"']([^\"']+)[\"']\s*\)")
_OUTGOING_RE = re.compile(r"@Outgoing\s*\(\s*[\"']([^\"']+)[\"']\s*\)")


def _has_quarkus_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp.startswith(("io.quarkus", "org.eclipse.microprofile", "io.smallrye")):
                return True
    return False


def _add_quarkus_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0

    # First pass: collect @Outgoing topic → producer files, and
    # @Incoming topic → consumer files. Then cross-link producers to
    # consumers on matching topic names.
    producers: dict[str, list[str]] = {}
    consumers: dict[str, list[str]] = {}

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        text = read_text(parsed)
        if not text:
            continue

        # Stamp Quarkus entry annotations on the file node.
        if any(annot in text for annot in _QUARKUS_ENTRY_ANNOT):
            node = graph.nodes.get(path)
            if node is not None:
                node["is_entry_point"] = True
                node["framework_role"] = node.get("framework_role") or "quarkus_component"

        for m in _OUTGOING_RE.finditer(text):
            producers.setdefault(m.group(1), []).append(path)
        for m in _INCOMING_RE.finditer(text):
            consumers.setdefault(m.group(1), []).append(path)

    # Cross-link by topic name. Producers depend on consumers (the channel
    # is the wire); we emit producer → consumer to mirror Spring's
    # @EventListener flow direction.
    for topic, producer_files in producers.items():
        consumer_files = consumers.get(topic, [])
        for src in producer_files:
            for dst in consumer_files:
                if src != dst and dst in path_set and _add_edge_if_new(graph, src, dst):
                    count += 1

    return count


class _QuarkusHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        in_stack = any(
            tok in dctx.stack_lower
            for tok in ("quarkus", "smallrye", "microprofile")
        )
        return in_stack or _has_quarkus_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_quarkus_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_QuarkusHandler()]
