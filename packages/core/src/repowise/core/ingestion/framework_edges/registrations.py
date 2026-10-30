"""Static Python registry relationships used by test reachability."""

from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

from ..models import FILE_DEPENDENCY_EDGE_TYPES, MODULE_REGISTRATION_HINT
from ..resolvers import ResolverContext, resolve_import
from .base import DetectionContext, FrameworkHandler, source_text

if TYPE_CHECKING:
    import networkx as nx

_REGISTRY_NAME = re.compile(
    r"(?:_registry|_dialects|_commands|_tools|_plugins|_routers|_handlers|^_register$)",
    re.IGNORECASE,
)
_REGISTRATION_CALLS = frozenset(
    {
        "add_command",
        "add_typer",
        "add_tool",
        "include_router",
        "register",
        "register_command",
        "register_lazy",
        "register_lazy_command",
        "register_tool",
        "tool",
    }
)
_REGISTRATION_SOURCE = re.compile(
    r"\b(?:register|register_lazy|add_command|add_typer|add_tool|include_router|tool|"
    r"register_command|register_lazy_command|register_tool|"
    r"[A-Z_]*(?:REGISTRY|DIALECTS|COMMANDS|TOOLS|PLUGINS|ROUTERS|HANDLERS|_REGISTER))\b",
    re.IGNORECASE,
)
_REGISTRY_PATH = re.compile(
    r"(?:registry|dialect|plugin|command|tool|router|handler)", re.IGNORECASE
)


def _imported_files(
    path: str, parsed: Any, ctx: ResolverContext, path_set: set[str]
) -> dict[str, str]:
    imports: dict[str, str] = {}
    for imported in parsed.imports:
        resolved = imported.resolved_file
        if resolved not in path_set:
            resolved = next(
                (binding.source_file for binding in imported.bindings if binding.source_file in path_set),
                None,
            )
        if resolved not in path_set:
            resolved = resolve_import(imported.module_path, path, "python", ctx)
        if not resolved or resolved not in path_set:
            continue
        for binding in imported.bindings:
            target = binding.source_file or resolved
            if target in path_set:
                imports[binding.local_name] = target
    return imports


def _names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def _registry_locals(parsed: Any) -> set[str]:
    return {
        binding.local_name
        for imported in parsed.imports
        for binding in imported.bindings
        if _REGISTRY_NAME.search(binding.local_name)
        or (
            binding.exported_name is not None
            and _REGISTRY_NAME.search(binding.exported_name)
        )
    }


def _mark_registration(
    graph: nx.DiGraph, source: str, target: str
) -> bool:
    if source == target:
        return False
    edge = graph.get_edge_data(source, target)
    if edge is None:
        graph.add_edge(
            source,
            target,
            edge_type="framework",
            imported_names=[],
            hint_source=MODULE_REGISTRATION_HINT,
        )
        return True
    if edge.get("edge_type") not in FILE_DEPENDENCY_EDGE_TYPES:
        return False
    if edge.get("hint_source") not in (None, MODULE_REGISTRATION_HINT):
        return False
    if edge.get("hint_source") == MODULE_REGISTRATION_HINT:
        return False
    edge["hint_source"] = MODULE_REGISTRATION_HINT
    return True


def _lazy_target(
    call: ast.Call, path: str, ctx: ResolverContext, path_set: set[str]
) -> str | None:
    name = (
        call.func.attr
        if isinstance(call.func, ast.Attribute)
        else call.func.id
        if isinstance(call.func, ast.Name)
        else ""
    )
    if not (
        name in {"register_lazy", "register_lazy_command"}
        and len(call.args) > 1
        and isinstance(call.args[1], ast.Constant)
        and isinstance(call.args[1].value, str)
    ):
        return None
    module = call.args[1].value.partition(":")[0]
    target = resolve_import(module, path, "python", ctx)
    if target not in path_set:
        module_path = module.replace(".", "/")
        candidates = {
            candidate
            for candidate in (
                f"{module_path}.py",
                f"{module_path}/__init__.py",
            )
            if candidate in path_set
        }
        if not candidates:
            candidates = {
                candidate
                for candidate in path_set
                if candidate.endswith((f"/{module_path}.py", f"/{module_path}/__init__.py"))
            }
        target = next(iter(candidates)) if len(candidates) == 1 else None
    return target if target in path_set else None


def _registration_relationships(
    path: str, parsed: Any, ctx: ResolverContext, path_set: set[str]
) -> set[tuple[str, str]]:
    imports = _imported_files(path, parsed, ctx, path_set)
    source = source_text(path, parsed, ctx.source_map or {})
    if not source or not _REGISTRATION_SOURCE.search(source):
        return set()
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()

    relationships: set[tuple[str, str]] = set()
    decorator_registries: set[str] = set()
    registry_locals = _registry_locals(parsed)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for decorator in node.decorator_list:
            expression = decorator.func if isinstance(decorator, ast.Call) else decorator
            if (
                isinstance(expression, ast.Attribute)
                and expression.attr in _REGISTRATION_CALLS
                and isinstance(expression.value, ast.Name)
                and expression.value.id in registry_locals
                and expression.value.id in imports
            ):
                registry = expression.value.id
                decorator_registries.add(registry)
                relationships.add((imports[registry], path))

    relationships.update(
        (path, imports[binding.local_name])
        for imported in parsed.imports
        for binding in imported.bindings
        if binding.local_name in imports
        and binding.local_name in registry_locals
        and binding.local_name not in decorator_registries
    )
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = (
                node.func.attr
                if isinstance(node.func, ast.Attribute)
                else node.func.id
                if isinstance(node.func, ast.Name)
                else ""
            )
            if name in _REGISTRATION_CALLS:
                relationships.update(
                    (path, imports[local])
                    for arg in node.args
                    for local in _names(arg)
                    if local in imports
                )
                lazy = _lazy_target(node, path, ctx, path_set)
                if lazy:
                    relationships.add((path, lazy))

        if isinstance(node, ast.Assign):
            registry = any(
                isinstance(target, ast.Name) and _REGISTRY_NAME.search(target.id)
                for target in node.targets
            )
            if registry:
                relationships.update(
                    (path, imports[name]) for name in _names(node.value) if name in imports
                )
        elif isinstance(node, ast.AnnAssign):
            registry = isinstance(node.target, ast.Name) and bool(
                _REGISTRY_NAME.search(node.target.id)
            )
            if registry and node.value is not None:
                relationships.update(
                    (path, imports[name]) for name in _names(node.value) if name in imports
                )
    return relationships


class _PythonRegistrations:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(
            parsed.file_info.language == "python" and self._is_candidate(path, parsed)
            for path, parsed in dctx.parsed_files.items()
        )

    @staticmethod
    def _is_candidate(path: str, parsed: Any) -> bool:
        return (
            bool(_REGISTRY_PATH.search(path))
            or bool(_registry_locals(parsed))
            or any(call.target_name in _REGISTRATION_CALLS for call in parsed.calls)
        )

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        count = 0
        for path, parsed in parsed_files.items():
            if (
                parsed.file_info.language != "python"
                or not self._is_candidate(path, parsed)
            ):
                continue
            count += sum(
                _mark_registration(graph, source, target)
                for source, target in sorted(
                    _registration_relationships(path, parsed, ctx, path_set)
                )
            )
        return count


HANDLERS: list[FrameworkHandler] = [_PythonRegistrations()]
