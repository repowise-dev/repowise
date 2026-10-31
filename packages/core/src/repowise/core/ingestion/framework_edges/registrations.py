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
_REGISTRY_PATH = re.compile(
    r"(?:registry|dialect|plugin|command|tool|router|handler)", re.IGNORECASE
)
_KNOWN_REGISTRATION_METHODS = frozenset({"add_command", "add_typer", "include_router"})
_LAZY_CALLS = frozenset({"register_lazy", "register_lazy_command"})
_REGISTRY_NAMES = frozenset(
    {"dialects", "commands", "tools", "plugins", "routers", "handlers"}
)
_REGISTRY_SUFFIXES = ("_dialects", "_commands", "_tools", "_plugins", "_routers", "_handlers")


def _imported_files(
    path: str, parsed: Any, ctx: ResolverContext, path_set: set[str]
) -> dict[str, str]:
    imports: dict[str, str] = {}
    for imported in parsed.imports:
        imports.update(_imported_bindings(path, imported, ctx, path_set))
    return imports


def _imported_bindings(
    path: str, imported: Any, ctx: ResolverContext, path_set: set[str]
) -> dict[str, str]:
    """Local name → source file, for the bindings of one import that land in the repo."""
    resolved = _resolved_source(path, imported, ctx, path_set)
    if resolved is None:
        return {}
    imports: dict[str, str] = {}
    for binding in imported.bindings:
        target = _binding_target(binding, resolved, path_set)
        if target is not None:
            imports[binding.local_name] = target
    return imports


def _binding_target(binding: Any, resolved: str, path_set: set[str]) -> str | None:
    target = binding.source_file or resolved
    return target if target in path_set else None


def _resolved_source(
    path: str, imported: Any, ctx: ResolverContext, path_set: set[str]
) -> str | None:
    """The repo file one import resolves to, trying its bindings before its module path."""
    return (
        _bound_source(imported, path_set)
        or _module_source(path, imported, ctx, path_set)
    )


def _bound_source(imported: Any, path_set: set[str]) -> str | None:
    resolved = imported.resolved_file
    if resolved in path_set:
        return resolved
    return _binding_source(imported, path_set)


def _module_source(
    path: str, imported: Any, ctx: ResolverContext, path_set: set[str]
) -> str | None:
    resolved = resolve_import(imported.module_path, path, "python", ctx)
    return resolved if resolved and resolved in path_set else None


def _binding_source(imported: Any, path_set: set[str]) -> str | None:
    return next(
        (
            binding.source_file
            for binding in imported.bindings
            if binding.source_file in path_set
        ),
        None,
    )


def _names(node: ast.AST) -> set[str]:
    return {item.id for item in ast.walk(node) if isinstance(item, ast.Name)}


def _registry_locals(parsed: Any) -> set[str]:
    return {
        binding.local_name
        for imported in parsed.imports
        for binding in imported.bindings
        if _is_registry_name(binding.local_name)
        or (binding.exported_name is not None and _is_registry_name(binding.exported_name))
    }


def _is_registry_name(name: str) -> bool:
    lowered = name.lower()
    if lowered == "registry" or lowered.endswith("_registry"):
        return True
    if not name.isupper():
        return False
    return _is_registry_constant(lowered)


def _is_registry_constant(lowered: str) -> bool:
    return (
        lowered == "_register"
        or lowered in _REGISTRY_NAMES
        or lowered.endswith(_REGISTRY_SUFFIXES)
    )


def _mark_registration(graph: nx.DiGraph, source: str, target: str) -> bool:
    if source == target:
        return False
    edge = graph.get_edge_data(source, target)
    if edge is None:
        _add_registration_edge(graph, source, target)
        return True
    if not _can_mark_registration(edge):
        return False
    edge["hint_source"] = MODULE_REGISTRATION_HINT
    return True


def _add_registration_edge(graph: nx.DiGraph, source: str, target: str) -> None:
    graph.add_edge(
        source,
        target,
        edge_type="framework",
        imported_names=[],
        hint_source=MODULE_REGISTRATION_HINT,
    )


def _can_mark_registration(edge: dict[str, Any]) -> bool:
    """An existing dependency edge that does not already carry a hint."""
    return (
        edge.get("edge_type") in FILE_DEPENDENCY_EDGE_TYPES
        and edge.get("hint_source") is None
    )


def _lazy_target(
    call: ast.Call, path: str, ctx: ResolverContext, path_set: set[str]
) -> str | None:
    module = _lazy_module(call)
    if module is None:
        return None
    target = resolve_import(module, path, "python", ctx)
    if target in path_set:
        return target
    return _module_candidate(module, path_set)


def _lazy_module(call: ast.Call) -> str | None:
    argument = _lazy_argument(call)
    if argument is None:
        return None
    return argument.value.partition(":")[0]


def _lazy_argument(call: ast.Call) -> ast.Constant | None:
    """The string second argument of a ``register_lazy`` call."""
    if not _is_lazy_call(call):
        return None
    return _string_argument(call.args[1])


def _is_lazy_call(call: ast.Call) -> bool:
    return _call_name(call) in _LAZY_CALLS and len(call.args) > 1


def _string_argument(argument: ast.expr) -> ast.Constant | None:
    if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
        return argument
    return None


def _call_name(call: ast.Call) -> str:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return ""


def _module_candidate(module: str, path_set: set[str]) -> str | None:
    """The one repo file a dotted module names, when exactly one matches."""
    module_path = module.replace(".", "/")
    candidates = _exact_module_paths(module_path, path_set)
    if not candidates:
        candidates = _suffixed_module_paths(module_path, path_set)
    if len(candidates) == 1:
        return next(iter(candidates))
    return None


def _exact_module_paths(module_path: str, path_set: set[str]) -> set[str]:
    return {
        candidate
        for candidate in (f"{module_path}.py", f"{module_path}/__init__.py")
        if candidate in path_set
    }


def _suffixed_module_paths(module_path: str, path_set: set[str]) -> set[str]:
    suffixes = (f"/{module_path}.py", f"/{module_path}/__init__.py")
    return {candidate for candidate in path_set if candidate.endswith(suffixes)}


def _registration_relationships(
    path: str, parsed: Any, ctx: ResolverContext, path_set: set[str]
) -> set[tuple[str, str]]:
    imports = _imported_files(path, parsed, ctx, path_set)
    tree = _python_tree(path, parsed, ctx)
    if tree is None:
        return set()

    registry_locals = _registry_locals(parsed)
    relationships, decorator_registries = _decorator_relationships(
        tree, path, imports, registry_locals
    )
    used_registries = registry_locals - decorator_registries
    relationships.update(_used_registry_edges(tree, path, imports, used_registries))
    relationships.update(_call_relationships(tree, path, imports, registry_locals, ctx, path_set))
    relationships.update(_table_relationships(tree, path, imports))
    return relationships


def _python_tree(path: str, parsed: Any, ctx: ResolverContext) -> ast.Module | None:
    source = source_text(path, parsed, ctx.source_map or {})
    if not source:
        return None
    try:
        return ast.parse(source)
    except SyntaxError:
        return None


def _used_registry_edges(
    tree: ast.Module, path: str, imports: dict[str, str], registry_locals: set[str]
) -> set[tuple[str, str]]:
    used = _names(tree) & registry_locals
    return {(path, imports[local]) for local in used if local in imports}


def _decorator_relationships(
    tree: ast.Module, path: str, imports: dict[str, str], registry_locals: set[str]
) -> tuple[set[tuple[str, str]], set[str]]:
    relationships: set[tuple[str, str]] = set()
    decorator_registries: set[str] = set()
    for node in ast.walk(tree):
        for decorator in _decorators(node):
            registry = _decorator_registry(decorator, registry_locals, imports)
            if registry is None:
                continue
            decorator_registries.add(registry)
            relationships.add((imports[registry], path))
    return relationships, decorator_registries


def _decorators(node: ast.AST) -> list[ast.expr]:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.decorator_list
    return []


def _decorator_registry(
    decorator: ast.expr, registry_locals: set[str], imports: dict[str, str]
) -> str | None:
    expression = _decorator_expression(decorator)
    if not _is_registration_attribute(expression):
        return None
    registry = _registry_receiver(expression.value)
    if registry is None:
        return None
    if not _is_known_registry(registry, registry_locals, imports):
        return None
    return registry


def _decorator_expression(decorator: ast.expr) -> ast.expr:
    if isinstance(decorator, ast.Call):
        return decorator.func
    return decorator


def _registry_receiver(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _is_known_registry(
    name: str, registry_locals: set[str], imports: dict[str, str]
) -> bool:
    return name in registry_locals and name in imports


def _is_registration_call(node: ast.Call, registry_locals: set[str]) -> bool:
    if not _is_registration_attribute(node.func):
        return False
    if node.func.attr in _KNOWN_REGISTRATION_METHODS:
        return True
    return _is_registry_receiver(node.func.value, registry_locals)


def _is_registration_attribute(expression: ast.expr) -> bool:
    return isinstance(expression, ast.Attribute) and expression.attr in _REGISTRATION_CALLS


def _is_registry_receiver(receiver: ast.expr, registry_locals: set[str]) -> bool:
    if isinstance(receiver, ast.Name):
        return receiver.id in registry_locals
    if isinstance(receiver, ast.Attribute):
        return _is_registry_attribute(receiver.attr)
    return False


def _is_registry_attribute(name: str) -> bool:
    lowered = name.lower()
    return (
        lowered == "registry"
        or lowered.endswith("_registry")
        or lowered in _REGISTRY_NAMES
    )


def _call_relationships(
    tree: ast.Module,
    path: str,
    imports: dict[str, str],
    registry_locals: set[str],
    ctx: ResolverContext,
    path_set: set[str],
) -> set[tuple[str, str]]:
    relationships: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if _is_registration_call_node(node, registry_locals):
            relationships.update(
                _registration_argument_edges(node, path, imports, ctx, path_set)
            )
    return relationships


def _is_registration_call_node(node: ast.AST, registry_locals: set[str]) -> bool:
    return isinstance(node, ast.Call) and _is_registration_call(node, registry_locals)


def _registration_argument_edges(
    node: ast.Call,
    path: str,
    imports: dict[str, str],
    ctx: ResolverContext,
    path_set: set[str],
) -> set[tuple[str, str]]:
    edges = {
        (path, imports[local])
        for arg in node.args
        for local in _names(arg)
        if local in imports
    }
    lazy = _lazy_target(node, path, ctx, path_set)
    if lazy is not None:
        edges.add((path, lazy))
    return edges


def _table_relationships(
    tree: ast.Module, path: str, imports: dict[str, str]
) -> set[tuple[str, str]]:
    relationships: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        value = _registry_assignment_value(node)
        if value is not None:
            relationships.update((path, imports[name]) for name in _names(value) if name in imports)
    return relationships


def _registry_assignment_value(node: ast.AST) -> ast.expr | None:
    if isinstance(node, ast.Assign):
        return _assignment_value(node)
    if isinstance(node, ast.AnnAssign) and _is_registry_target(node.target):
        return node.value
    return None


def _assignment_value(node: ast.Assign) -> ast.expr | None:
    if any(_is_registry_target(target) for target in node.targets):
        return node.value
    return None


def _is_registry_target(target: ast.AST) -> bool:
    return isinstance(target, ast.Name) and _is_registry_name(target.id)


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
            if parsed.file_info.language != "python" or not self._is_candidate(path, parsed):
                continue
            count += sum(
                _mark_registration(graph, source, target)
                for source, target in sorted(
                    _registration_relationships(path, parsed, ctx, path_set)
                )
            )
        return count


HANDLERS: list[FrameworkHandler] = [_PythonRegistrations()]
