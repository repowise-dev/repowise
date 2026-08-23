"""Jakarta EE / Java EE framework edges.

Covers JAX-RS routing, CDI scope stereotypes, Servlet 3+ web components,
EJB component stereotypes, and JPA entity associations. Both the
``javax.*`` (Java EE) and ``jakarta.*`` (Jakarta EE 9+) namespaces are
recognised — repos in flight between the two will hit both branches and
the handler matches either.
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


# Class-level stereotypes that make the runtime instantiate the class
# (CDI + EJB + Servlet 3+ + JAX-RS provider).
_JAKARTA_STEREOTYPE_ANNOT = (
    "@Path",
    "@Provider",
    "@ApplicationScoped",
    "@RequestScoped",
    "@SessionScoped",
    "@ConversationScoped",
    "@Dependent",
    "@Singleton",
    "@Stateless",
    "@Stateful",
    "@MessageDriven",
    "@WebServlet",
    "@WebFilter",
    "@WebListener",
    "@ServerEndpoint",
    "@ClientEndpoint",
)

# Field / constructor / setter injection annotations.
_JAKARTA_INJECT_FIELD_RE = re.compile(
    r"@(?:Inject|Resource|EJB|PersistenceContext|PersistenceUnit)"
    r"\b[^\n]*\n\s*(?:private|protected|public|final|\s)*\s*"
    r"([A-Z][\w.]*)\s*(?:<[^>]+>)?\s+\w+\s*[;=]"
)

# JPA association annotations declared on a typed field.
_JPA_ASSOC_FIELD_RE = re.compile(
    r"@(?:OneToOne|OneToMany|ManyToOne|ManyToMany)"
    r"\b[^\n]*\n\s*(?:private|protected|public|final|\s)*\s*"
    r"(?:[A-Z][\w.]*\s*<\s*([A-Z][\w.]*)\s*>|([A-Z][\w.]*))"
)

# JAX-RS routing verbs — used for routing detection (currently we mark
# the class file as ``framework_role="jax_rs_resource"``; full ROUTE node
# emission can be layered on top in a follow-up).
_JAX_RS_VERBS = ("@GET", "@POST", "@PUT", "@DELETE",
                 "@PATCH", "@HEAD", "@OPTIONS")


def _has_jakarta_imports(parsed_files: dict[str, Any]) -> bool:
    for parsed in parsed_files.values():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        for imp in parsed.imports:
            mp = imp.module_path
            if mp.startswith(("javax.", "jakarta.")):
                return True
    return False


def _add_jakarta_edges(
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

    for path, parsed in parsed_files.items():
        if parsed.file_info.language not in ("java", "kotlin"):
            continue
        text = read_text(parsed)
        if not text:
            continue

        has_stereotype = any(annot in text for annot in _JAKARTA_STEREOTYPE_ANNOT)
        has_jax_rs = "@Path" in text or any(v in text for v in _JAX_RS_VERBS)
        has_jpa = "@Entity" in text or "@MappedSuperclass" in text or "@Embeddable" in text

        if not (has_stereotype or has_jpa):
            continue

        # Stamp framework role for downstream consumers.
        node = graph.nodes.get(path)
        if node is not None:
            if has_jpa:
                node["framework_role"] = "jpa_entity"
                node["is_entry_point"] = True
            elif has_jax_rs:
                node["framework_role"] = "jax_rs_resource"
                node["is_entry_point"] = True
            else:
                node["framework_role"] = node.get("framework_role") or "cdi_bean"
                node["is_entry_point"] = True

        # @Inject / @Resource / @EJB / @PersistenceContext field injection
        for m in _JAKARTA_INJECT_FIELD_RE.finditer(text):
            type_name = m.group(1)
            for target in _resolve_type(type_name):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1

        # JPA associations — pull the type out of generic args first
        # (``@OneToMany Set<User> users``) then bare (``@ManyToOne User
        # owner``).
        if has_jpa:
            for m in _JPA_ASSOC_FIELD_RE.finditer(text):
                type_name = m.group(1) or m.group(2)
                if not type_name:
                    continue
                for target in _resolve_type(type_name):
                    if target in path_set and _add_edge_if_new(graph, path, target):
                        count += 1

    return count


class _JakartaHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        in_stack = any(
            tok in dctx.stack_lower
            for tok in ("jakarta", "jakartaee", "javaee", "jaxrs", "cdi", "jpa", "ejb")
        )
        return in_stack or _has_jakarta_imports(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_jakarta_edges(graph, parsed_files, path_set)


HANDLERS: list[FrameworkHandler] = [_JakartaHandler()]
