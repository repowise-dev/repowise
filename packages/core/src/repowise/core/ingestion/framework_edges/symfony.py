"""Symfony convention edges: console commands and event subscribers/listeners.

Symfony Console commands (extending ``Symfony\\Component\\Console\\Command\\Command``
or annotated with ``#[AsCommand]``) and event subscribers / listeners (implementing
``Symfony\\Component\\EventDispatcher\\EventSubscriberInterface`` or annotated with
``#[AsEventListener]``) are loaded dynamically by Symfony's runtime and dependency
injection container, never explicitly imported by other application modules.

Without framework edges, static dead-code analysis reports them as unused exports
and unreachable files. We anchor them to ``framework:symfony`` and mark them as
reachability roots.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..framework_facts import SYMFONY
from ..languages.php_same_namespace import (
    blank_php_comments,
    file_namespace,
    php_use_aliases,
    qualify_php_name,
)
from .base import DetectionContext, FrameworkHandler, anchor_edge, source_text

if TYPE_CHECKING:
    import networkx as nx

    from ..resolvers import ResolverContext


SYMFONY_COMMAND_BASE = "Symfony\\Component\\Console\\Command\\Command"
SYMFONY_AS_COMMAND_ATTR = "Symfony\\Component\\Console\\Attribute\\AsCommand"
SYMFONY_EVENT_SUBSCRIBER = "Symfony\\Component\\EventDispatcher\\EventSubscriberInterface"
SYMFONY_AS_EVENT_LISTENER_ATTR = "Symfony\\Component\\EventDispatcher\\Attribute\\AsEventListener"

_CLASS_DECL_RE = re.compile(
    r"\bclass\s+(?P<name>[A-Za-z_]\w*)"
    r"(?:\s+extends\s+(?P<extends>\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*))?"
    r"(?:\s+implements\s+(?P<implements>[^{]+))?",
    re.MULTILINE,
)

_AS_COMMAND_ATTR_RE = re.compile(
    r"""#\[(?:[^\]]*,)?\s*(?P<name>\\?(?:Symfony\\Component\\Console\\Attribute\\)?AsCommand)\b"""
)
_AS_EVENT_LISTENER_ATTR_RE = re.compile(
    r"""#\[(?:[^\]]*,)?\s*(?P<name>\\?(?:Symfony\\Component\\EventDispatcher\\Attribute\\)?AsEventListener)\b"""
)


class _PhpFile:
    """One PHP file's text and namespace/use-alias environment."""

    __slots__ = ("aliases", "namespace", "path", "text")

    def __init__(self, path: str, parsed: Any, source_map: dict[str, bytes]) -> None:
        self.path = path
        self.text = source_text(path, parsed, source_map)
        self.namespace = file_namespace(self.text) if self.text else None
        self.aliases = php_use_aliases(parsed)

    def qualify(self, name: str) -> str:
        return qualify_php_name(name, self.namespace, self.aliases)


def _has_symfony_attribute(f: _PhpFile, blanked: str) -> bool:
    """True if *f* uses #[AsCommand] or #[AsEventListener] resolving to Symfony."""
    if "AsCommand" in blanked:
        for m in _AS_COMMAND_ATTR_RE.finditer(blanked):
            if f.qualify(m.group("name")) == SYMFONY_AS_COMMAND_ATTR:
                return True
        for local, fqn in f.aliases.items():
            if fqn == SYMFONY_AS_COMMAND_ATTR and re.search(
                rf"#\[(?:[^\]]*,)?\s*{re.escape(local)}\b", blanked
            ):
                return True

    if "AsEventListener" in blanked:
        for m in _AS_EVENT_LISTENER_ATTR_RE.finditer(blanked):
            if f.qualify(m.group("name")) == SYMFONY_AS_EVENT_LISTENER_ATTR:
                return True
        for local, fqn in f.aliases.items():
            if fqn == SYMFONY_AS_EVENT_LISTENER_ATTR and re.search(
                rf"#\[(?:[^\]]*,)?\s*{re.escape(local)}\b", blanked
            ):
                return True

    return False


def _add_symfony_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    ctx: ResolverContext,
    path_set: set[str],
) -> int:
    source_map = getattr(ctx, "source_map", None) or {}
    php_files = [
        f
        for path, parsed in parsed_files.items()
        if parsed.file_info.language == "php"
        and (f := _PhpFile(path, parsed, source_map)).text
    ]
    if not php_files:
        return 0

    class_to_file: dict[str, str] = {}
    class_to_parent: dict[str, str] = {}
    class_to_interfaces: dict[str, list[str]] = {}
    attributed_files: set[str] = set()

    for f in php_files:
        blanked = blank_php_comments(f.text)
        if _has_symfony_attribute(f, blanked):
            attributed_files.add(f.path)

        for m in _CLASS_DECL_RE.finditer(blanked):
            cls_name = m.group("name")
            cls_fqn = f"{f.namespace}\\{cls_name}" if f.namespace else cls_name
            class_to_file[cls_fqn] = f.path

            extends = m.group("extends")
            if extends:
                class_to_parent[cls_fqn] = f.qualify(extends.strip())

            implements = m.group("implements")
            if implements:
                class_to_interfaces[cls_fqn] = [
                    f.qualify(iface.strip())
                    for iface in implements.split(",")
                    if iface.strip()
                ]

    # Resolve command class hierarchy transitively
    command_fqns = {SYMFONY_COMMAND_BASE}
    changed = True
    while changed:
        changed = False
        for cls_fqn, parent_fqn in class_to_parent.items():
            if parent_fqn in command_fqns and cls_fqn not in command_fqns:
                command_fqns.add(cls_fqn)
                changed = True

    # Resolve subscriber class hierarchy transitively
    subscriber_fqns = {SYMFONY_EVENT_SUBSCRIBER}
    changed = True
    while changed:
        changed = False
        for cls_fqn, ifaces in class_to_interfaces.items():
            if cls_fqn not in subscriber_fqns and any(i in subscriber_fqns for i in ifaces):
                subscriber_fqns.add(cls_fqn)
                changed = True
        for cls_fqn, parent_fqn in class_to_parent.items():
            if parent_fqn in subscriber_fqns and cls_fqn not in subscriber_fqns:
                subscriber_fqns.add(cls_fqn)
                changed = True

    target_files: set[str] = set(attributed_files)
    for cls_fqn in command_fqns:
        if cls_fqn in class_to_file:
            target_files.add(class_to_file[cls_fqn])
    for cls_fqn in subscriber_fqns:
        if cls_fqn in class_to_file:
            target_files.add(class_to_file[cls_fqn])

    count = 0
    for target in sorted(target_files):
        if anchor_edge(graph, SYMFONY, target):
            if target in graph:
                graph.nodes[target]["is_reachability_root"] = True
            count += 1

    return count


class _SymfonyHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language == "php" for p in dctx.parsed_files.values())

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_symfony_edges(graph, parsed_files, ctx, path_set)


HANDLERS: list[FrameworkHandler] = [_SymfonyHandler()]
