"""Rails routes / ActiveRecord convention edges.

Split out of ``framework_edges.py`` (PR 3.5) — behaviour-preserving move.
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


_RAILS_RESOURCES_RE = re.compile(r"\bresources?\s+:(\w+)")
_RAILS_GET_TO_RE = re.compile(
    r"\b(?:get|post|put|patch|delete|match)\s+[^\n]+?(?:to:\s*|=>\s*)['\"]([\w/]+)#\w+['\"]"
)
_RAILS_NAMESPACE_RE = re.compile(r"\bnamespace\s+:(\w+)\b")
_RAILS_AR_RELATION_RE = re.compile(
    r"\b(?:belongs_to|has_many|has_one|has_and_belongs_to_many)\s+:(\w+)"
)


def _singularize(word: str) -> str:
    """Very rough Rails inflector — sufficient for routes/AR lookups."""
    if word.endswith("ies") and len(word) > 3:
        return word[:-3] + "y"
    if word.endswith("ses") or word.endswith("xes") or word.endswith("zes"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def _camelize(word: str) -> str:
    return "".join(part.capitalize() for part in word.split("_"))


def _add_rails_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    count = 0

    # ---- routes.rb → controller files ----
    routes_path = "config/routes.rb"
    if routes_path in path_set:
        text = read_text(parsed_files[routes_path])
        if text:
            # Parse line-by-line to track namespace nesting (indent-agnostic; we
            # use the order of opening keywords vs `end`).
            namespace_stack: list[str] = []
            for raw_line in text.splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                ns_match = _RAILS_NAMESPACE_RE.search(line)
                if ns_match and ("do" in line or line.endswith("do")):
                    namespace_stack.append(ns_match.group(1))
                    continue
                if line == "end" and namespace_stack:
                    namespace_stack.pop()
                    continue
                # resources :users → users_controller
                for m in _RAILS_RESOURCES_RE.finditer(line):
                    resource = m.group(1)
                    target = _resolve_rails_controller(ctx, namespace_stack, resource, path_set)
                    if target and _add_edge_if_new(graph, routes_path, target):
                        count += 1
                # get "/foo", to: "users#index"
                for m in _RAILS_GET_TO_RE.finditer(line):
                    ctrl_path = m.group(1)
                    target = _resolve_rails_controller_path(
                        ctx, namespace_stack, ctrl_path, path_set
                    )
                    if target and _add_edge_if_new(graph, routes_path, target):
                        count += 1

    # ---- ActiveRecord relationships: model → model ----
    for path, parsed in parsed_files.items():
        if parsed.file_info.language != "ruby":
            continue
        if "/models/" not in path and not path.startswith("app/models/"):
            continue
        text = read_text(parsed)
        if not text:
            continue
        for m in _RAILS_AR_RELATION_RE.finditer(text):
            assoc_name = m.group(1)
            target = _resolve_rails_relation(ctx, assoc_name, path_set)
            if target and _add_edge_if_new(graph, path, target):
                count += 1

    return count


def _resolve_rails_controller(
    ctx: ResolverContext, namespace_stack: list[str], resource: str, path_set: set[str]
) -> str | None:
    """`resources :users` (with optional `namespace :admin do`) → controller path."""
    namespace_segs = [seg for seg in namespace_stack]
    candidate_path = "/".join([*namespace_segs, f"{resource}_controller"])
    expected = f"app/controllers/{candidate_path}.rb"
    if expected in path_set:
        return expected
    # Try via Rails autoload index (heritage)
    constant = "::".join(_camelize(seg) for seg in [*namespace_segs, f"{resource}_controller"])
    return ctx.rails_lookup(constant)


def _resolve_rails_controller_path(
    ctx: ResolverContext, namespace_stack: list[str], controller_token: str, path_set: set[str]
) -> str | None:
    """`to: "users#index"` or `to: "admin/users#index"` → controller path."""
    expected = f"app/controllers/{controller_token}_controller.rb"
    if expected in path_set:
        return expected
    parts = controller_token.split("/")
    constant = "::".join(_camelize(p) for p in [*parts[:-1], f"{parts[-1]}_controller"])
    return ctx.rails_lookup(constant)


def _resolve_rails_relation(
    ctx: ResolverContext, assoc_name: str, path_set: set[str]
) -> str | None:
    """`belongs_to :user` / `has_many :orders` → model file."""
    singular = _singularize(assoc_name)
    expected = f"app/models/{singular}.rb"
    if expected in path_set:
        return expected
    return ctx.rails_lookup(_camelize(singular))


class _RailsHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return "rails" in dctx.stack_lower or "config/application.rb" in dctx.path_set

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_rails_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_RailsHandler()]
