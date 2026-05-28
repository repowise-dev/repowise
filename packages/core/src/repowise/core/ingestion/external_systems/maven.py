"""Parse Maven ``pom.xml`` manifests.

Reads ``<dependency>`` entries from ``<dependencies>`` blocks and handles
``<modules>`` for reactor discovery. Properties (``${...}``) are resolved
when declared in the same POM or a parent POM within the same repo.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for

filenames: tuple[str, ...] = ("pom.xml",)
ecosystem: str = "maven"

_NS = re.compile(r"\{[^}]*\}")


def _strip_ns(tag: str) -> str:
    return _NS.sub("", tag)


def _find(el: ET.Element, local_name: str) -> ET.Element | None:
    for child in el:
        if _strip_ns(child.tag) == local_name:
            return child
    return None


def _find_text(el: ET.Element, local_name: str) -> str:
    child = _find(el, local_name)
    return (child.text or "").strip() if child is not None else ""


def _find_all(el: ET.Element, local_name: str) -> list[ET.Element]:
    return [child for child in el if _strip_ns(child.tag) == local_name]


def _collect_properties(root: ET.Element) -> dict[str, str]:
    props: dict[str, str] = {}
    props_el = _find(root, "properties")
    if props_el is not None:
        for child in props_el:
            key = _strip_ns(child.tag)
            if child.text:
                props[key] = child.text.strip()
    gid = _find_text(root, "groupId")
    aid = _find_text(root, "artifactId")
    ver = _find_text(root, "version")
    if gid:
        props.setdefault("project.groupId", gid)
    if aid:
        props.setdefault("project.artifactId", aid)
    if ver:
        props.setdefault("project.version", ver)
    return props


_PROP_RE = re.compile(r"\$\{([^}]+)\}")


def _resolve_props(value: str, props: dict[str, str]) -> str:
    def _repl(m: re.Match) -> str:
        return props.get(m.group(1), m.group(0))

    resolved = _PROP_RE.sub(_repl, value)
    if resolved != value and "${" in resolved:
        resolved = _PROP_RE.sub(_repl, resolved)
    return resolved


_TEST_SCOPES = frozenset({"test", "provided", "system"})


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        tree = ET.parse(manifest_path)  # noqa: S314
    except Exception:
        return []

    root = tree.getroot()
    declared_in = manifest_path.relative_to(repo_root).as_posix()
    props = _collect_properties(root)

    # Inherit parent properties
    parent = _find(root, "parent")
    if parent is not None:
        parent_ver = _find_text(parent, "version")
        if parent_ver:
            props.setdefault("project.parent.version", parent_ver)
            props.setdefault("project.version", parent_ver)

    # Collect managed dependency versions
    dep_mgmt = _find(root, "dependencyManagement")
    managed: dict[str, str] = {}
    if dep_mgmt is not None:
        deps_el = _find(dep_mgmt, "dependencies")
        if deps_el is not None:
            for dep in _find_all(deps_el, "dependency"):
                g = _resolve_props(_find_text(dep, "groupId"), props)
                a = _resolve_props(_find_text(dep, "artifactId"), props)
                v = _resolve_props(_find_text(dep, "version"), props)
                if g and a and v:
                    managed[f"{g}:{a}"] = v

    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()

    for deps_el in _find_all(root, "dependencies"):
        for dep in _find_all(deps_el, "dependency"):
            group_id = _resolve_props(_find_text(dep, "groupId"), props)
            artifact_id = _resolve_props(_find_text(dep, "artifactId"), props)
            if not group_id or not artifact_id:
                continue
            name = f"{group_id}:{artifact_id}"
            if name in seen:
                continue
            seen.add(name)

            version = _resolve_props(_find_text(dep, "version"), props)
            if not version or "${" in version:
                version = managed.get(name, version or None)

            scope = _find_text(dep, "scope").lower()
            is_dev = scope in _TEST_SCOPES

            records.append(
                ExternalSystemRecord(
                    name=name,
                    ecosystem=ecosystem,
                    declared_in=declared_in,
                    version=version if version and "${" not in version else None,
                    display_name=display_name_for(artifact_id),
                    category=classify(artifact_id),
                    is_dev_dep=is_dev,
                )
            )

    return records
