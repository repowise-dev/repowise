"""Go import-binding extraction."""

from __future__ import annotations

import posixpath
import re
from typing import Any

from tree_sitter import Node

from ...models import NamedBinding
from ..helpers import node_text


def extract_go_bindings(stmt_node: Node, src: str) -> tuple[list[str], list[NamedBinding]]:
    """Extract bindings from Go import specs."""
    alias_node = stmt_node.child_by_field_name("name")
    path_node = stmt_node.child_by_field_name("path")

    if path_node is None:
        for child in stmt_node.children:
            if child.type == "interpreted_string_literal":
                path_node = child
                break
    if path_node is None:
        return [], []

    path_text = node_text(path_node, src).strip("\"'` ")
    # For an external import path, strip /vN and .vN from the path element.
    # The strip only applies when a dot-free identifier element remains before the version element;
    # otherwise fall back to the original last element (e.g. "gopkg.in/v2" -> "v2").
    trimmed = path_text.rstrip("/")
    if "/" in trimmed:
        candidate = re.sub(r"/v\d+$", "", trimmed)
        if candidate:
            candidate_last = candidate.rsplit("/", 1)[-1]
            if "." not in candidate_last and candidate_last.isidentifier():
                trimmed = candidate
    default_name = trimmed.rsplit("/", 1)[-1]
    default_name = re.sub(r"\.v\d+$", "", default_name)

    if alias_node:
        alias = node_text(alias_node, src)
        if alias == ".":
            return ["*"], [NamedBinding(local_name="*", exported_name=None, source_file=None)]
        if alias == "_":
            return [], []
        b = NamedBinding(
            local_name=alias,
            exported_name=None,
            source_file=None,
            is_module_alias=True,
        )
        b.is_explicit_alias = True
        return [alias], [b]

    b = NamedBinding(
        local_name=default_name,
        exported_name=None,
        source_file=None,
        is_module_alias=True,
    )
    b.is_explicit_alias = False
    return [default_name], [b]


def _majority_package_name(candidates: list[str]) -> str:
    from collections import Counter

    counts = Counter(candidates)
    non_main = [c for c in candidates if c != "main"]
    return Counter(non_main).most_common(1)[0][0] if non_main else counts.most_common(1)[0][0]


def build_go_directory_package_map(parsed_files: Any) -> dict[str, str]:
    """Map each Go source directory to its declared package name in one pass."""
    candidates: dict[str, list[str]] = {}
    if hasattr(parsed_files, "items"):
        for p, pf in parsed_files.items():
            pkg_name = getattr(pf, "module_name", None)
            if pkg_name and not p.endswith("_test.go"):
                pkg_dir = posixpath.dirname(p)
                candidates.setdefault(pkg_dir, []).append(pkg_name)
    return {
        pkg_dir: _majority_package_name(pkg_candidates)
        for pkg_dir, pkg_candidates in candidates.items()
    }


def _resolve_dir_package_name(
    pkg_dir: str,
    parsed_files: Any,
    resolved_file: str,
    dir_to_pkg: dict[str, str] | None = None,
) -> str | None:
    if dir_to_pkg is not None:
        return dir_to_pkg.get(pkg_dir)
    dir_to_pkg = build_go_directory_package_map(parsed_files)
    pkg_name = dir_to_pkg.get(pkg_dir)
    if pkg_name:
        return pkg_name
    target_pf = parsed_files.get(resolved_file) if hasattr(parsed_files, "get") else None
    return getattr(target_pf, "module_name", None) if target_pf is not None else None


def _apply_go_binding_name(imp: Any, pkg_name: str) -> None:
    has_explicit = False
    for b in imp.bindings:
        if getattr(b, "is_explicit_alias", False) or b.local_name in ("*", "_"):
            has_explicit = True
            continue
        if b.local_name != pkg_name:
            b.local_name = pkg_name

    if (
        not has_explicit
        and imp.imported_names
        and imp.imported_names != [pkg_name]
        and "*" not in imp.imported_names
    ):
        imp.imported_names = [pkg_name]


def sync_go_import_binding(
    imp: Any,
    resolved_file: str,
    parsed_files: Any,
    dir_to_pkg: dict[str, str] | None = None,
) -> None:
    """Sync a Go import's binding and local names with the declared package name.

    For an in-repo import, name the binding from the ``package`` clause of the
    resolved directory's files. An explicit alias still wins; ``_`` and ``.``
    are unchanged. Must not change: imports whose path element already matches.
    """
    if not resolved_file or resolved_file.startswith("external:"):
        return

    raw = getattr(imp, "raw_statement", "").strip()
    if raw and not raw.startswith(('"', "'", "`")):
        return

    pkg_dir = posixpath.dirname(resolved_file)
    if dir_to_pkg is not None and pkg_dir in dir_to_pkg:
        pkg_name = dir_to_pkg[pkg_dir]
    else:
        if dir_to_pkg is None:
            dir_to_pkg = build_go_directory_package_map(parsed_files)
        pkg_name = _resolve_dir_package_name(pkg_dir, parsed_files, resolved_file, dir_to_pkg)

    if not pkg_name:
        return

    _apply_go_binding_name(imp, pkg_name)


