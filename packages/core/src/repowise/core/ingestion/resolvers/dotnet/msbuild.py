"""Parse MSBuild project files (.csproj, Directory.Build.props/targets).

Only the fields repowise actually uses are extracted: ProjectReference,
PackageReference, RootNamespace, AssemblyName, ImplicitUsings, and
project-level <Using Include=...> items. The parser tolerates both
SDK-style and legacy XML (``<Project ToolsVersion="...">``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

import structlog

log = structlog.get_logger(__name__)


@dataclass
class MSBuildProject:
    """Parsed MSBuild project file."""

    path: Path  # absolute path to the .csproj
    project_dir: Path  # directory containing the .csproj
    root_namespace: str | None = None
    assembly_name: str | None = None
    implicit_usings: bool = False
    project_references: list[Path] = field(default_factory=list)  # absolute paths to referenced .csproj
    package_references: set[str] = field(default_factory=set)  # NuGet package ids
    project_usings: set[str] = field(default_factory=set)  # <Using Include="X"/> namespaces

    @property
    def name(self) -> str:
        """Display name — the .csproj filename without extension."""
        return self.path.stem


# Strip XML namespace prefix from a tag — MSBuild docs say the namespace
# is optional in SDK-style projects but legacy projects use
# ``http://schemas.microsoft.com/developer/msbuild/2003``.
def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "enable", "1")


def parse_csproj(csproj_path: Path) -> MSBuildProject | None:
    """Parse a single .csproj file. Returns None on parse failure."""
    try:
        tree = ET.parse(csproj_path)
    except (ET.ParseError, OSError) as exc:
        log.debug("Failed to parse csproj", path=str(csproj_path), error=str(exc))
        return None

    project = MSBuildProject(path=csproj_path.resolve(), project_dir=csproj_path.parent.resolve())
    root = tree.getroot()

    for elem in root.iter():
        tag = _local(elem.tag)

        if tag == "RootNamespace" and elem.text:
            project.root_namespace = elem.text.strip()
        elif tag == "AssemblyName" and elem.text:
            project.assembly_name = elem.text.strip()
        elif tag == "ImplicitUsings" and elem.text:
            project.implicit_usings = _bool(elem.text)
        elif tag == "ProjectReference":
            include = elem.get("Include")
            if include:
                # ProjectReference paths use Windows-style backslashes by
                # convention; normalise and resolve relative to the .csproj.
                rel = include.replace("\\", "/")
                target = (project.project_dir / rel).resolve()
                project.project_references.append(target)
        elif tag == "PackageReference":
            pkg = elem.get("Include")
            if pkg:
                project.package_references.add(pkg.strip())
        elif tag == "Using":
            ns = elem.get("Include")
            if ns:
                project.project_usings.add(ns.strip())

    return project


def find_csproj_files(repo_path: Path) -> list[Path]:
    """Return all .csproj files under *repo_path*, skipping bin/obj output."""
    skip = {"bin", "obj", ".vs", "node_modules", ".git", "packages", "TestResults"}
    out: list[Path] = []
    for csproj in repo_path.rglob("*.csproj"):
        if any(part in skip for part in csproj.parts):
            continue
        out.append(csproj)
    return out


def find_directory_build_props(start: Path, repo_root: Path) -> list[Path]:
    """Walk from *start* up to *repo_root* collecting Directory.Build.props.

    The first match closer to start "wins" but MSBuild semantics merge them
    bottom-up. Repowise just collects them so the resolver can union the
    implicit-using flags and project usings.
    """
    out: list[Path] = []
    cur = start.resolve()
    repo_root = repo_root.resolve()
    while True:
        for name in ("Directory.Build.props", "Directory.Build.targets", "Directory.Packages.props"):
            cand = cur / name
            if cand.exists():
                out.append(cand)
        if cur == repo_root or cur.parent == cur:
            break
        cur = cur.parent
    return out
