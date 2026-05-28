"""Micronaut framework edges.

Micronaut reuses Jakarta-style annotations (``@Inject`` / ``@Singleton``)
but ships its own routing verbs (``@Get`` / ``@Post`` / …) and DI
qualifiers (``@Factory`` / ``@Replaces`` / ``@Primary``). Annotation
short-names collide with Spring's ``@Controller``; we disambiguate by
the import set (``io.micronaut.*``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
    _build_class_to_file,
    read_text,
)

if TYPE_CHECKING:
    import networkx as nx


_MICRONAUT_STEREOTYPE_ANNOT = (
    "@Controller",
    "@Filter",
    "@Singleton",
    "@Prototype",
    "@Factory",
    "@Bean",
    "@MicronautTest",
    "@RequiresConfiguration",
    "@Refreshable",
    "@Context",
    "@JobScheduler",
    "@KafkaListener",
    "@KafkaClient",
)

_MICRONAUT_INJECT_FIELD_RE = re.compile(
    r"@Inject\s+(?:private|protected|public|final|\s)*\s*"
    r"([A-Z][\w.]*)\s*(?:<[^>]+>)?\s+\w+\s*[;=]"
)


def _has_micronaut_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("io.micronaut"):
                return True
    return False


def _add_micronaut_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("java", "kotlin"))

    impl_map: dict[str, list[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for rel in parsed.heritage:
            if rel.kind in ("implements", "extends"):
                impl_map.setdefault(rel.parent_name, []).append(path)

    def _resolve_type(type_name: str) -> list[str]:
        type_name = type_name.split("<")[0].rsplit(".", 1)[-1].strip()
        results: list[str] = []
        own = class_to_file.get(type_name)
        if own:
            results.append(own)
        for impl in impl_map.get(type_name, []):
            if impl not in results:
                results.append(impl)
        return results

    # Per-file: only fire if the file actually imports Micronaut. This
    # keeps ``@Controller``/``@Bean`` from being mistaken for the Spring
    # symbols on a per-class basis.
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        file_imports_micronaut = any(
            imp.module_path.startswith("io.micronaut") for imp in parsed.imports
        )
        if not file_imports_micronaut:
            continue
        text = read_text(parsed)
        if not text:
            continue
        has_stereotype = any(annot in text for annot in _MICRONAUT_STEREOTYPE_ANNOT)
        if not has_stereotype:
            continue

        node = graph.nodes.get(path)
        if node is not None:
            node["is_entry_point"] = True
            node["framework_role"] = node.get("framework_role") or "micronaut_component"

        for m in _MICRONAUT_INJECT_FIELD_RE.finditer(text):
            type_name = m.group(1)
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

    return count


class _MicronautHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        in_stack = any(tok in dctx.stack_lower for tok in ("micronaut",))
        return in_stack or _has_micronaut_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_micronaut_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_MicronautHandler()]
