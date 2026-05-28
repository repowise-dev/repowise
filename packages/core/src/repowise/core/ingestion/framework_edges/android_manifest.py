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
    return any(p.endswith("AndroidManifest.xml") for p in parsed_files.keys())


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
        try:
            text = Path(parsed_files[path].file_info.abs_path).read_text(
                encoding="utf-8", errors="ignore"
            )
        except (OSError, KeyError, AttributeError):
            continue

        # Only collect names from inside component tags. The regex below
        # is generous; the gate cuts manifest-permission lines.
        for tag in _ANDROID_COMPONENT_TAGS:
            for chunk in text.split(tag)[1:]:
                head = chunk.split(">", 1)[0]
                for m in _ANDROID_NAME_RE.finditer(head):
                    fqn = m.group(1).lstrip(".")
                    if not fqn:
                        continue
                    targets: tuple[str, ...] = ()
                    if jvm_index is not None:
                        targets = jvm_index.files_for_fqn(fqn)
                    for target in targets:
                        if target in path_set and _add_edge_if_new(graph, path, target):
                            count += 1
                            node = graph.nodes.get(target)
                            if node is not None:
                                node["is_entry_point"] = True

    return count


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
