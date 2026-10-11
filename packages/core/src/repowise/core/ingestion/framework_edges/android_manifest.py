"""Android manifest reference edges.

``AndroidManifest.xml`` registers Activities, Services, Receivers,
Providers, and the Application class by fully-qualified name. The
manifest is the runtime's hand-off into source — none of those class
references appear in any ``import`` statement, so the dead-code pass
would otherwise flag every Activity that has no in-source caller.

Strategy: parse every ``AndroidManifest.xml`` we can find, pull the
``android:name`` attributes, and emit a synthetic ``framework`` edge
from the manifest file to the named class's source file.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    _add_edge_if_new,
)

if TYPE_CHECKING:
    import networkx as nx


_ANDROID_NAME_RE = re.compile(r"android:name=\"([\w.$]+)\"")
_ANDROID_COMPONENT_TAGS = ("<activity", "<service", "<receiver",
                           "<provider", "<application")


def _has_android_manifests(parsed_files: dict[str, Any]) -> bool:
    return any(p.endswith("AndroidManifest.xml") for p in parsed_files)


def _add_android_edges(
    graph: nx.DiGraph,
    parsed_files: dict[str, Any],
    path_set: set[str],
    ctx: ResolverContext,
) -> int:
    count = 0

    try:
        from ..resolvers.jvm_workspace import get_or_build_jvm_index

        jvm_index = get_or_build_jvm_index(ctx)
    except Exception:
        jvm_index = None

    for path in list(path_set):
        if not path.endswith("AndroidManifest.xml"):
            continue
        text = _read_manifest(parsed_files, path)
        if text is None or jvm_index is None:
            continue
        for fqn in _component_class_names(text):
            for target in jvm_index.files_for_fqn(fqn):
                if target in path_set and _add_edge_if_new(graph, path, target):
                    count += 1
                    node = graph.nodes.get(target)
                    if node is not None:
                        node["is_entry_point"] = True

    return count


def _read_manifest(parsed_files: dict[str, Any], path: str) -> str | None:
    try:
        return Path(parsed_files[path].file_info.abs_path).read_text(
            encoding="utf-8", errors="ignore"
        )
    except (OSError, KeyError, AttributeError):
        return None


def _component_class_names(text: str) -> Iterator[str]:
    """Class names registered by component tags, in document order per tag.

    Only names inside component tags count: the name regex is generous and
    this gate cuts manifest-permission lines.
    """
    for tag in _ANDROID_COMPONENT_TAGS:
        for chunk in text.split(tag)[1:]:
            head = chunk.split(">", 1)[0]
            for m in _ANDROID_NAME_RE.finditer(head):
                fqn = m.group(1).lstrip(".")
                if fqn:
                    yield fqn


class _AndroidManifestHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _has_android_manifests(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_android_edges(graph, parsed_files, path_set, ctx)


HANDLERS: list[FrameworkHandler] = [_AndroidManifestHandler()]
