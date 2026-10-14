"""Android manifest reference edges.

``AndroidManifest.xml`` registers Activities, Services, Receivers,
Providers, and the Application class by fully-qualified name. The
manifest is the runtime's hand-off into source — none of those class
references appear in any ``import`` statement, so the dead-code pass
would otherwise flag every Activity that has no in-source caller.

Strategy: read every ``AndroidManifest.xml`` on disk (the traverser does
not index XML), pull the component ``android:name`` attributes, and link
the named class's source file from the ``framework:android`` anchor. A
relative name (``.MainActivity``) is completed with the manifest
``package``, or with the module's Gradle ``namespace`` when the manifest
has none (AGP 7+).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from repowise.core.fs_walk import glob_via

from ..framework_facts import ANDROID
from ..resolvers import ResolverContext
from .base import (
    DetectionContext,
    FrameworkHandler,
    anchor_edge,
)

if TYPE_CHECKING:
    import networkx as nx


# XML allows either quote and spaces around ``=``.
_ANDROID_NAME_RE = re.compile(r"""android:name\s*=\s*['"]([\w.$]+)['"]""")
_ANDROID_COMPONENT_TAGS = ("<activity", "<service", "<receiver",
                           "<provider", "<application")


_MANIFEST_PACKAGE_RE = re.compile(r"""<manifest\b[^>]*?\bpackage\s*=\s*['"]([\w.]+)['"]""")
_GRADLE_NAMESPACE_RE = re.compile(r"""\bnamespace\s*=?\s*['"]([\w.]+)['"]""")


def _has_jvm_sources(parsed_files: dict[str, Any]) -> bool:
    return any(p.endswith((".java", ".kt")) for p in parsed_files)


def _add_android_edges(graph: nx.DiGraph, path_set: set[str], ctx: ResolverContext) -> int:
    if ctx.repo_path is None:
        return 0
    try:
        from ..resolvers.jvm_workspace import get_or_build_jvm_index

        jvm_index = get_or_build_jvm_index(ctx)
    except Exception:
        return 0

    count = 0
    for name in _registered_class_names(ctx.repo_path.resolve(), ctx):
        for target in jvm_index.files_for_fqn(name):
            if target not in path_set:
                continue
            count += anchor_edge(graph, ANDROID, target)
            node = graph.nodes.get(target)
            if node is not None:
                node["is_entry_point"] = True

    return count


def _registered_class_names(root: Path, ctx: ResolverContext) -> Iterator[str]:
    """Fully-qualified component class names from every manifest under *root*."""
    for manifest in glob_via(
        ctx.walk_snapshot, root, "AndroidManifest.xml", prune_nested_git=ctx.prune_nested_git
    ):
        try:
            text = manifest.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        package = _manifest_package(manifest, text)
        for name in _component_class_names(text):
            yield _qualify(name, package)


def _manifest_package(manifest: Path, text: str) -> str:
    """The package relative component names complete against.

    The manifest ``package`` attribute, else the ``namespace`` of the module
    whose ``src/<set>/`` holds the manifest (or of the manifest's own folder,
    the pre-``src`` layout).
    """
    if m := _MANIFEST_PACKAGE_RE.search(text):
        return m.group(1)
    module = manifest.parent.parent.parent if manifest.parent.parent.name == "src" else manifest.parent
    for build_name in ("build.gradle.kts", "build.gradle"):
        try:
            build_text = (module / build_name).read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if m := _GRADLE_NAMESPACE_RE.search(build_text):
            return m.group(1)
    return ""


def _qualify(name: str, package: str) -> str:
    """Android's rule: ``.Foo`` and a bare ``Foo`` live in *package*."""
    if name.startswith(".") or "." not in name:
        return f"{package}.{name.lstrip('.')}" if package else name.lstrip(".")
    return name


def _component_class_names(text: str) -> Iterator[str]:
    """Class names registered by component tags, in document order per tag.

    Only names inside component tags count: the name regex is generous and
    this gate cuts manifest-permission lines.
    """
    for tag in _ANDROID_COMPONENT_TAGS:
        for chunk in text.split(tag)[1:]:
            head = chunk.split(">", 1)[0]
            for m in _ANDROID_NAME_RE.finditer(head):
                if m.group(1).strip("."):
                    yield m.group(1)


class _AndroidManifestHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return _has_jvm_sources(dctx.parsed_files)

    def add_edges(
        self,
        graph: nx.DiGraph,
        parsed_files: dict[str, Any],
        ctx: ResolverContext,
        path_set: set[str],
    ) -> int:
        return _add_android_edges(graph, path_set, ctx)


HANDLERS: list[FrameworkHandler] = [_AndroidManifestHandler()]
