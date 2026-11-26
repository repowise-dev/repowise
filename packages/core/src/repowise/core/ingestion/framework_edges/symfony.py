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


SYMFONY_COMMAND_BASE_LOWER = "symfony\\component\\console\\command\\command"
SYMFONY_AS_COMMAND_ATTR_LOWER = "symfony\\component\\console\\attribute\\ascommand"
SYMFONY_EVENT_SUBSCRIBER_LOWER = (
    "symfony\\component\\eventdispatcher\\eventsubscriberinterface"
)
SYMFONY_AS_EVENT_LISTENER_ATTR_LOWER = (
    "symfony\\component\\eventdispatcher\\attribute\\aseventlistener"
)

_DECLARATION_PREFIX = (
    r"^[ \t]*(?:#\[(?:[^\]\[]|\[[^\]]*\])*\]\s*)*(?:(?:final|abstract|readonly)\s+)*"
)

_TYPE_DECL_RE = re.compile(
    _DECLARATION_PREFIX
    + r"(?P<kind>class|interface)\s+(?P<name>[A-Za-z_]\w*)"
    + r"(?:\s+extends\s+(?P<extends>\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*(?:\s*,\s*\\?[A-Za-z_]\w*(?:\\[A-Za-z_]\w*)*)*))?"
    + r"(?:\s+implements\s+(?P<implements>[^{]+))?",
    re.MULTILINE,
)

_AS_COMMAND_ATTR_RE = re.compile(
    r"""#\[(?:[^\]]*,)?\s*(?P<name>\\?(?:[A-Za-z_]\w*\\)*AsCommand)\b""",
    re.IGNORECASE,
)
_AS_EVENT_LISTENER_ATTR_RE = re.compile(
    r"""#\[(?:[^\]]*,)?\s*(?P<name>\\?(?:[A-Za-z_]\w*\\)*AsEventListener)\b""",
    re.IGNORECASE,
)


class _PhpFile:
    """One PHP file's text and namespace/use-alias environment."""

    __slots__ = ("aliases", "blanked", "lower_aliases", "namespace", "path", "text")

    def __init__(self, path: str, parsed: Any, source_map: dict[str, bytes]) -> None:
        self.path = path
        self.text = source_text(path, parsed, source_map)
        self.blanked = blank_php_comments(self.text) if self.text else ""
        self.namespace = file_namespace(self.blanked) if self.blanked else None
        self.aliases = php_use_aliases(parsed)
        self.lower_aliases = {k.lower(): v for k, v in self.aliases.items()}

    def qualify(self, name: str) -> str:
        name_clean = name.lstrip("\\")
        first, sep, rest = name_clean.partition("\\")
        if not name.startswith("\\") and first.lower() in self.lower_aliases:
            target = self.lower_aliases[first.lower()]
            return f"{target}\\{rest}" if sep else target
        return qualify_php_name(name, self.namespace, self.aliases)


def _has_attribute(
    f: _PhpFile,
    blanked: str,
    keyword: str,
    attr_re: re.Pattern[str],
    expected_lower: str,
) -> bool:
    """Check whether *blanked* applies an attribute matching *expected_lower*."""
    if keyword not in blanked.lower():
        return False
    for m in attr_re.finditer(blanked):
        if f.qualify(m.group("name")).lower() == expected_lower:
            return True
    for local, fqn in f.lower_aliases.items():
        if fqn.lower() == expected_lower and re.search(
            rf"#\[(?:[^\]]*,)?\s*\\?{re.escape(local)}\b", blanked, re.IGNORECASE
        ):
            return True
    return False


def _has_symfony_attribute(f: _PhpFile, blanked: str) -> bool:
    """True if *f* uses #[AsCommand] or #[AsEventListener] resolving to Symfony."""
    return _has_attribute(
        f, blanked, "ascommand", _AS_COMMAND_ATTR_RE, SYMFONY_AS_COMMAND_ATTR_LOWER
    ) or _has_attribute(
        f,
        blanked,
        "aseventlistener",
        _AS_EVENT_LISTENER_ATTR_RE,
        SYMFONY_AS_EVENT_LISTENER_ATTR_LOWER,
    )


def _record_declarations(
    f: _PhpFile,
    blanked: str,
    class_to_files: dict[str, set[str]],
    class_to_parent: dict[str, str],
    class_to_interfaces: dict[str, list[str]],
    interface_to_parents: dict[str, list[str]],
) -> None:
    """Extract class/interface declarations, inheritance, and implementations."""
    for m in _TYPE_DECL_RE.finditer(blanked):
        name = m.group("name")
        fqn = f"{f.namespace}\\{name}" if f.namespace else name
        fqn_lower = fqn.lower()
        class_to_files.setdefault(fqn_lower, set()).add(f.path)

        kind = m.group("kind")
        extends = m.group("extends")
        implements = m.group("implements")

        if kind == "interface":
            if extends:
                interface_to_parents[fqn_lower] = [
                    f.qualify(p.strip()).lower()
                    for p in extends.split(",")
                    if p.strip()
                ]
        else:
            if extends:
                class_to_parent[fqn_lower] = f.qualify(extends.strip()).lower()
            if implements:
                class_to_interfaces[fqn_lower] = [
                    f.qualify(iface.strip()).lower()
                    for iface in implements.split(",")
                    if iface.strip()
                ]


def _resolve_commands(class_to_parent: dict[str, str]) -> set[str]:
    """Find all classes extending Symfony Command transitively."""
    command_fqns = {SYMFONY_COMMAND_BASE_LOWER}
    changed = True
    while changed:
        changed = False
        for cls_fqn, parent_fqn in class_to_parent.items():
            if parent_fqn in command_fqns and cls_fqn not in command_fqns:
                command_fqns.add(cls_fqn)
                changed = True
    return command_fqns


def _resolve_subscribers(
    class_to_parent: dict[str, str],
    class_to_interfaces: dict[str, list[str]],
    interface_to_parents: dict[str, list[str]],
) -> set[str]:
    """Find all classes implementing Symfony EventSubscriberInterface transitively."""
    subscriber_ifaces = {SYMFONY_EVENT_SUBSCRIBER_LOWER}
    changed = True
    while changed:
        changed = False
        for iface_fqn, parents in interface_to_parents.items():
            if iface_fqn not in subscriber_ifaces and any(
                p in subscriber_ifaces for p in parents
            ):
                subscriber_ifaces.add(iface_fqn)
                changed = True

    subscriber_classes: set[str] = set()
    changed = True
    while changed:
        changed = False
        for cls_fqn, ifaces in class_to_interfaces.items():
            if cls_fqn not in subscriber_classes and any(
                i in subscriber_ifaces for i in ifaces
            ):
                subscriber_classes.add(cls_fqn)
                changed = True
        for cls_fqn, parent in class_to_parent.items():
            if parent in subscriber_classes and cls_fqn not in subscriber_classes:
                subscriber_classes.add(cls_fqn)
                changed = True
    return subscriber_classes


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

    class_to_files: dict[str, set[str]] = {}
    class_to_parent: dict[str, str] = {}
    class_to_interfaces: dict[str, list[str]] = {}
    interface_to_parents: dict[str, list[str]] = {}
    attributed_files: set[str] = set()

    for f in php_files:
        blanked = f.blanked
        if _has_symfony_attribute(f, blanked):
            attributed_files.add(f.path)
        _record_declarations(
            f,
            blanked,
            class_to_files,
            class_to_parent,
            class_to_interfaces,
            interface_to_parents,
        )

    command_fqns = _resolve_commands(class_to_parent)
    subscriber_fqns = _resolve_subscribers(
        class_to_parent, class_to_interfaces, interface_to_parents
    )

    target_files: set[str] = set(attributed_files)
    for cls_fqn in command_fqns:
        target_files.update(class_to_files.get(cls_fqn, ()))
    for cls_fqn in subscriber_fqns:
        target_files.update(class_to_files.get(cls_fqn, ()))

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
