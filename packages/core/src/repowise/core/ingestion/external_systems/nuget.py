"""Parse .NET ``.csproj`` files for ``<PackageReference>`` entries.

We deliberately use ``xml.etree.ElementTree`` rather than a real XML parser
to keep the dependency footprint small; .csproj files are simple enough.
``<ProjectReference>`` entries point at sibling projects and are skipped —
those are containers, not external systems.
"""

from __future__ import annotations

from pathlib import Path
from xml.etree import ElementTree as ET

from .base import ExternalSystemRecord
from .classifier import classify, display_name_for
from .io_kind import classify_io_kind

# .csproj filenames are repo-specific; the discovery layer matches "*.csproj".
filenames: tuple[str, ...] = ()
ecosystem: str = "nuget"


def parse(manifest_path: Path, repo_root: Path) -> list[ExternalSystemRecord]:
    try:
        tree = ET.parse(manifest_path)
    except (OSError, ET.ParseError):
        return []
    root = tree.getroot()

    declared_in = manifest_path.relative_to(repo_root).as_posix()
    records: list[ExternalSystemRecord] = []
    seen: set[str] = set()

    for elem in root.iter():
        # Strip XML namespace if present
        tag = elem.tag.split("}", 1)[-1] if "}" in elem.tag else elem.tag
        if tag != "PackageReference":
            continue
        name = (elem.get("Include") or elem.get("Update") or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        version = elem.get("Version") or _child_text(elem, "Version")
        records.append(
            ExternalSystemRecord(
                name=name,
                ecosystem=ecosystem,
                declared_in=declared_in,
                version=version.strip() if version else None,
                display_name=display_name_for(name.split(".")[-1]),
                category=classify(name),
                io_kind=classify_io_kind(name),
                is_dev_dep=False,
            )
        )
    return records


def _child_text(elem: ET.Element, child_tag: str) -> str | None:
    for child in elem:
        tag = child.tag.split("}", 1)[-1] if "}" in child.tag else child.tag
        if tag == child_tag and child.text:
            return child.text
    return None
