"""Roots the Apple runtime instantiates by class name from XML metadata.

Metadata is read through the ingestion walk, not indexed as source. Class
declarations, rather than file names, resolve references; ambiguous names
are deliberately left unrooted.
"""

from __future__ import annotations

import plistlib
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import structlog

from repowise.core.fs_walk import glob_via

from ..framework_facts import APPLE
from ..type_names import bare_type_name
from .base import DetectionContext, FrameworkHandler, anchor_edge

log = structlog.get_logger(__name__)
_LANGUAGES = {"swift", "objectivec"}
_CLASS_KEYS = {
    "NSExtensionPrincipalClass", "NSPrincipalClass", "UISceneDelegateClassName", "NSExtensionClass"
}


def _plist_classes(value: Any) -> Iterator[str]:
    if isinstance(value, dict):
        for key, item in value.items():
            if key in _CLASS_KEYS and isinstance(item, str):
                yield item
            elif isinstance(item, (dict, list)):
                yield from _plist_classes(item)
    elif isinstance(value, list):
        for item in value:
            yield from _plist_classes(item)


def _references(metadata: Path) -> Iterator[tuple[str, str | None]]:
    try:
        raw = metadata.read_bytes()
        if metadata.suffix == ".plist":
            # Binary plists are deliberately outside this XML-only handler.
            if raw.startswith(b"bplist"):
                return
            for name in _plist_classes(plistlib.loads(raw)):
                yield name, None
        else:
            for node in ElementTree.fromstring(raw).iter():
                if name := node.get("customClass"):
                    yield name, node.get("customModule")
    except (OSError, ValueError, ElementTree.ParseError, plistlib.InvalidFileException):
        log.debug("apple_metadata_unreadable", path=str(metadata))


def _resolve(
    candidates: set[str], metadata: Path, root: Path, module: str | None
) -> str | None:
    if module and not module.startswith("$("):
        local = {p for p in candidates if module in Path(p).parts[:-1]}
        if local:
            return next(iter(local)) if len(local) == 1 else None
    # Resources and localization subfolders are not targets. Ascend only
    # within this repo, choosing the nearest folder containing declarations.
    for folder in (metadata.parent, *metadata.parent.parents):
        if folder == root:
            break
        prefix = folder.relative_to(root)
        local = {p for p in candidates if Path(p).is_relative_to(prefix)}
        if local:
            return next(iter(local)) if len(local) == 1 else None
    return next(iter(candidates)) if len(candidates) == 1 else None


class _AppleMetadataHandler:
    def detect(self, dctx: DetectionContext) -> bool:
        return any(p.file_info.language in _LANGUAGES for p in dctx.parsed_files.values())

    def add_edges(self, graph, parsed_files, ctx, path_set) -> int:
        if ctx.repo_path is None:
            return 0
        root = ctx.repo_path.resolve()
        declarations: dict[str, set[str]] = defaultdict(set)
        for path, parsed in parsed_files.items():
            if parsed.file_info.language in _LANGUAGES:
                for symbol in parsed.symbols:
                    if symbol.kind == "class" and not symbol.parent_name:
                        declarations[symbol.name].add(path)
        # An Objective-C interface and its implementation repeat the same
        # declaration. Use the existing header/source pairing proof, not a
        # guessed class-to-filename mapping, to prefer the implementation.
        for name, paths in declarations.items():
            declarations[name] = {
                path for path in paths
                if not (
                    Path(path).suffix == ".h" and any(
                        target in paths
                        and Path(target).suffix in {".m", ".mm"}
                        and data.get("hint_source") == "header_source_pair"
                        for _, target, data in graph.out_edges(path, data=True)
                    )
                )
            }
        count = 0
        for pattern in ("Info.plist", "*-Info.plist", "*.storyboard", "*.xib"):
            for metadata in glob_via(
                ctx.walk_snapshot, root, pattern, prune_nested_git=ctx.prune_nested_git
            ):
                for raw_name, module in _references(metadata):
                    name = bare_type_name(raw_name)
                    candidates = declarations.get(name, set())
                    target = _resolve(candidates, metadata, root, module)
                    if target is not None and target in path_set:
                        count += anchor_edge(graph, APPLE, target)
                        graph.nodes[target]["is_entry_point"] = True
                    elif len(candidates) > 1:
                        log.info("apple_metadata_ambiguous_class", path=str(metadata), name=name)
        return count


HANDLERS: list[FrameworkHandler] = [_AppleMetadataHandler()]
