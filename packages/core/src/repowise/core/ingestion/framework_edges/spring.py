"""Spring Boot dependency-injection convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
"""

from __future__ import annotations

import re
from pathlib import Path
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


_SPRING_BEAN_ANNOT = (
    "@Component",
    "@Service",
    "@Repository",
    "@Controller",
    "@RestController",
    "@Configuration",
)
_SPRING_AUTOWIRED_FIELD_RE = re.compile(
    r"@Autowired\s+(?:private|protected|public|final|\s)*\s*([A-Z]\w*)\s+\w+"
)
_SPRING_CTOR_PARAM_RE = re.compile(r"\b([A-Z]\w*)\s+\w+\s*[,)]")
_SPRING_BEAN_METHOD_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|static|final|\s)+\s*([A-Z]\w*)\s+\w+\s*\("
)
_SPRING_BEAN_METHOD_KOTLIN_RE = re.compile(
    r"@Bean\b[^\n]*\n\s*(?:public|protected|private|internal|fun|open|\s)+\s*\w+\s*\([^)]*\)\s*:\s*([A-Z]\w*)"
)


def _has_spring_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            if imp.module_path.startswith("org.springframework"):
                return True
    return False


def _add_spring_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
) -> int:
    count = 0
    class_to_file = _build_class_to_file(parsed_files, ("java", "kotlin"))

    # Build interface → list of impl files map from heritage
    impl_map: dict[str, list[str]] = {}
    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for rel in parsed.heritage:
            if rel.kind in ("implements", "extends"):
                impl_map.setdefault(rel.parent_name, []).append(path)

    def _resolve_type(type_name: str) -> list[str]:
        results: list[str] = []
        own = class_to_file.get(type_name)
        if own:
            results.append(own)
        for impl in impl_map.get(type_name, []):
            if impl not in results:
                results.append(impl)
        return results

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        text = read_text(parsed)
        if not text:
            continue
        is_bean = any(annot in text for annot in _SPRING_BEAN_ANNOT)
        if not is_bean:
            continue

        # @Autowired field injection
        for m in _SPRING_AUTOWIRED_FIELD_RE.finditer(text):
            type_name = m.group(1)
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # Constructor parameter injection: collect param types from any @Autowired
        # constructor or any single public constructor in a bean (Spring 4.3+ omits
        # the annotation when the class has only one constructor).
        for ctor_match in re.finditer(
            r"(?:@Autowired\s*\n\s*)?(?:public|protected|private|\s)*"
            + re.escape(Path(path).stem)
            + r"\s*\(([^)]*)\)",
            text,
        ):
            params = ctor_match.group(1)
            if not params.strip():
                continue
            for pm in _SPRING_CTOR_PARAM_RE.finditer(params + ","):
                type_name = pm.group(1)
                if type_name in ("String", "Integer", "Long", "Boolean", "Double", "Float"):
                    continue
                for target in _resolve_type(type_name):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

        # @Bean factory methods → return-type file
        if "@Configuration" in text:
            for m in _SPRING_BEAN_METHOD_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1
            for m in _SPRING_BEAN_METHOD_KOTLIN_RE.finditer(text):
                for target in _resolve_type(m.group(1)):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    return count


class _SpringHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        spring_in_stack = any(
            token in dctx.stack_lower
            for token in ("spring", "springboot", "spring-boot", "spring boot")
        )
        return spring_in_stack or _has_spring_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_spring_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_SpringHandler()]
