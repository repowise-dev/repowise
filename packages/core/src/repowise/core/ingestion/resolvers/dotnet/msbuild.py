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

from repowise.core.fs_walk import iter_glob

log = structlog.get_logger(__name__)


# Directory basenames the .NET / Unity resolver should never scan for
# projects or source. These are intentionally scoped to the dotnet path,
# not shared global fs_walk pruning, because names like Library/ or Logs/
# can be legitimate source trees in non-Unity repos.
DOTNET_SCAN_SKIP_DIRS: frozenset[str] = frozenset(
    {
        "bin",
        "obj",
        ".vs",
        "node_modules",
        ".git",
        "packages",
        "TestResults",
        "Library",
        "Temp",
        "Logs",
        "UserSettings",
        "MemoryCaptures",
        "Builds",
    }
)
_DOTNET_SCAN_SKIP_DIRS_CASEFOLDED = frozenset(part.casefold() for part in DOTNET_SCAN_SKIP_DIRS)


@dataclass
class MSBuildProject:
    """Parsed MSBuild project file (.csproj or .vbproj — same XML schema)."""

    path: Path  # absolute path to the .csproj / .vbproj
    project_dir: Path  # directory containing the project file
    root_namespace: str | None = None
    assembly_name: str | None = None
    implicit_usings: bool = False
    project_references: list[Path] = field(
        default_factory=list
    )  # absolute paths to referenced .csproj
    package_references: set[str] = field(default_factory=set)  # NuGet package ids
    # Project-level implicit imports: C#'s <Using Include="X"/> ItemGroup
    # entries and VB's <Import Include="X"/> ItemGroup entries both land
    # here — same semantics (a namespace implicitly available to every
    # file in the project), just a different element name per language.
    project_usings: set[str] = field(default_factory=set)

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
        elif tag == "Import":
            # VB's project-level root-import ItemGroup entry
            # (<Import Include="System.Linq"/>) uses the SAME element name
            # MSBuild overloads for file-include directives
            # (<Import Project="...targets"/>). Discriminate on the
            # attribute, not position: only `Include` means a namespace.
            ns = elem.get("Include")
            if ns:
                project.project_usings.add(ns.strip())

    return project


def _find_project_files(
    repo_path: Path, pattern: str, *, prune_nested_git: bool = True
) -> list[Path]:
    """Shared glob + skip-dir filter behind ``find_csproj_files``/``find_vbproj_files``."""
    out: list[Path] = []
    for proj in iter_glob(repo_path, pattern, prune_nested_git=prune_nested_git):
        if path_has_dotnet_scan_skip_dir(proj, repo_path):
            continue
        out.append(proj)
    return out


def find_csproj_files(repo_path: Path, *, prune_nested_git: bool = True) -> list[Path]:
    """Return all .csproj files under *repo_path*, skipping bin/obj output."""
    return _find_project_files(repo_path, "*.csproj", prune_nested_git=prune_nested_git)


def find_vbproj_files(repo_path: Path, *, prune_nested_git: bool = True) -> list[Path]:
    """Return all .vbproj files under *repo_path*, skipping bin/obj output."""
    return _find_project_files(repo_path, "*.vbproj", prune_nested_git=prune_nested_git)


# `parse_csproj` is pure MSBuild-XML-schema parsing (no C#-specific logic) —
# .vbproj files use the identical schema, so this alias documents that the
# same function is the correct, intentional call for both project kinds.
parse_vbproj = parse_csproj


def path_has_dotnet_scan_skip_dir(path: Path, repo_root: Path) -> bool:
    """Return True when *path* lives under a dotnet-skip directory in *repo_root*."""
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        parts = path.parts
    return any(part.casefold() in _DOTNET_SCAN_SKIP_DIRS_CASEFOLDED for part in parts)
