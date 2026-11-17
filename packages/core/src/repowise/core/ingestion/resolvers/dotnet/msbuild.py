"""Parse MSBuild project files (.csproj, .vbproj, Directory.Build.props/targets).

Only the fields repowise actually uses are extracted: ProjectReference,
PackageReference, RootNamespace, AssemblyName, ImplicitUsings, and
project-level <Using Include=...> / <Import Include=...> items. The parser
tolerates both SDK-style and legacy XML (``<Project ToolsVersion="...">``).

.vbproj carries the same MSBuild schema as .csproj, so one parser serves both.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import structlog

from repowise.core.fs_walk import glob_via

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
    """Parsed MSBuild project file."""

    path: Path  # absolute path to the .csproj
    project_dir: Path  # directory containing the .csproj
    root_namespace: str | None = None
    assembly_name: str | None = None
    implicit_usings: bool = False
    project_references: list[Path] = field(default_factory=list)  # absolute paths to referenced .csproj
    package_references: set[str] = field(default_factory=set)  # NuGet package ids
    project_usings: set[str] = field(default_factory=set)  # <Using Include="X"/> namespaces
    #: Explicit ``<Compile Include>`` items, ``/``-separated and unevaluated. A
    #: file linked in from outside the project directory (``..\Shared\X.cs``,
    #: ``$(CommonPath)X.cs``) belongs to this project too, which the directory
    #: walk cannot see.
    compile_includes: list[str] = field(default_factory=list)
    package_id: str | None = None  # <PackageId>, the id this project publishes under
    #: Tri-state on purpose: None = the project says nothing, which is not the
    #: same as an explicit <IsPackable>false</IsPackable>.
    is_packable: bool | None = None
    generate_package_on_build: bool = False
    #: A literal ``<IsTestProject>true</IsTestProject>`` outside any
    #: ``Condition``, or the MSTest SDK, which sets it.
    is_test_project: bool = False
    #: The ``package_references`` declared outside any ``Condition``: a shared
    #: build file that adds test packages only ``Condition="'$(IsTestProject)'
    #: == 'true'"`` does not make every project below it a test project.
    unconditional_package_references: set[str] = field(default_factory=set)
    #: Literal ``<PropertyGroup>`` values outside any ``Condition``, keyed
    #: lowercase as MSBuild compares them, for a shared build file's conditions.
    properties: dict[str, str] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """Display name — the .csproj filename without extension."""
        return self.path.stem

    @property
    def published_id(self) -> str | None:
        """The id this project publishes a package under, or None when it does not.

        Packability is opt-in, never assumed: an SDK-style project is packable
        by default, so treating silence as yes would make every internal
        project in a solution a published library.
        """
        if self.is_packable is False:
            return None
        if not (self.is_packable or self.generate_package_on_build or self.package_id is not None):
            return None
        return self.package_id or self.assembly_name or self.path.stem


# Strip XML namespace prefix from a tag — MSBuild docs say the namespace
# is optional in SDK-style projects but legacy projects use
# ``http://schemas.microsoft.com/developer/msbuild/2003``.
def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") else tag


def _bool(value: str | None) -> bool:
    return (value or "").strip().lower() in ("true", "enable", "1")


def _tristate(value: str | None) -> bool | None:
    """None for anything that is not a literal boolean.

    ``<IsPackable>$(PublishLibraries)</IsPackable>`` is the normal way to
    centralise the flag; reading an unevaluated property as ``false`` would
    record an unknown as an explicit no.
    """
    text = (value or "").strip().lower()
    if text in ("true", "enable", "1"):
        return True
    return False if text in ("false", "disable", "0") else None


def _compile_items(include: str | None) -> list[str]:
    """The ``;``-separated items of a ``<Compile Include>``, wildcards dropped."""
    items = (item.strip().replace("\\", "/") for item in (include or "").split(";"))
    return [item for item in items if item and "*" not in item]


def _conditioned(root: ET.Element) -> set[int]:
    """Ids of the elements a ``Condition`` applies to, itself or an ancestor's."""
    return {
        id(element)
        for scope in root.iter()
        if scope.get("Condition") is not None
        for element in scope.iter()
    }


def parse_csproj(csproj_path: Path) -> MSBuildProject | None:
    """Parse a single .csproj file. Returns None on parse failure."""
    try:
        tree = ET.parse(csproj_path)
    except (ET.ParseError, OSError) as exc:
        log.debug("Failed to parse csproj", path=str(csproj_path), error=str(exc))
        return None

    project = MSBuildProject(path=csproj_path.resolve(), project_dir=csproj_path.parent.resolve())
    root = tree.getroot()
    project.is_test_project = "mstest.sdk" in (root.get("Sdk") or "").lower()
    conditioned = _conditioned(root)

    for elem in root.iter():
        tag = _local(elem.tag)

        if tag == "RootNamespace" and elem.text:
            project.root_namespace = elem.text.strip()
        elif tag == "AssemblyName" and elem.text:
            project.assembly_name = elem.text.strip()
        elif tag == "ImplicitUsings" and elem.text:
            project.implicit_usings = _bool(elem.text)
        elif tag == "PackageId" and elem.text:
            project.package_id = elem.text.strip()
        elif tag == "IsPackable" and elem.text:
            # Last literal wins; a conditional PropertyGroup is not evaluated.
            packable = _tristate(elem.text)
            if packable is not None:
                project.is_packable = packable
        elif tag == "GeneratePackageOnBuild" and elem.text:
            project.generate_package_on_build = _bool(elem.text)
        elif tag == "IsTestProject" and elem.text and id(elem) not in conditioned:
            project.is_test_project = _bool(elem.text)
        elif tag == "ProjectReference":
            include = elem.get("Include")
            if include:
                # ProjectReference paths use Windows-style backslashes by
                # convention; normalise and resolve relative to the .csproj.
                rel = include.replace("\\", "/")
                target = (project.project_dir / rel).resolve()
                project.project_references.append(target)
        elif tag == "Compile":
            project.compile_includes.extend(_compile_items(elem.get("Include")))
        elif tag == "PackageReference":
            pkg = elem.get("Include")
            if pkg:
                project.package_references.add(pkg.strip())
                if id(elem) not in conditioned:
                    project.unconditional_package_references.add(pkg.strip())
        elif tag in ("Using", "Import"):
            # <Using Include="X"/> is the C# form, <Import Include="X"/> the VB
            # one; MSBuild's own <Import Project="..."/> carries no Include.
            ns = elem.get("Include")
            if ns:
                project.project_usings.add(ns.strip())
        elif tag == "PropertyGroup" and id(elem) not in conditioned:
            for prop in elem:
                if prop.text and id(prop) not in conditioned:
                    project.properties[_local(prop.tag).lower()] = prop.text.strip()

    return project


_SHARED_BUILD_FILES = ("Directory.Build.props", "Directory.Build.targets")

# ``'$(Name)' == 'value'``, the one condition shape read; any other is unknown.
_PROPERTY_CONDITION_RE = re.compile(r"^\s*'\$\((\w+)\)'\s*(==|!=)\s*'([^'$]*)'\s*$")


def _condition_holds(condition: str, properties: dict[str, str]) -> bool:
    """Whether *condition* holds for *properties*; False when that is unknown.

    Unknown is any shape but a literal comparison, or a property the project
    file does not set: it may come from the SDK or another imported file.
    """
    match = _PROPERTY_CONDITION_RE.match(condition)
    if match is None or match.group(1).lower() not in properties:
        return False
    name, op, value = match.groups()
    equal = properties[name.lower()].lower() == value.lower()
    return equal if op == "==" else not equal


# {shared build file: each ``<Using Include>`` item with the conditions over it}
SharedUsings = dict[Path, tuple[tuple[str, tuple[str, ...]], ...]]


def shared_build_usings(project: MSBuildProject, repo_root: Path, cache: SharedUsings) -> set[str]:
    """``<Using Include>`` items the nearest ``Directory.Build.props`` and
    ``.targets`` above *project* give it, each file read once per *cache*.

    MSBuild imports only the nearest of each name. An item under a condition
    counts when the condition compares a property the project file sets
    literally (``'$(IncludeUsings)' == 'true'``); any other condition drops it.
    Ceiling: a ``<Using Remove>`` and a nearest file importing its parent are
    not read.
    """
    usings: set[str] = set()
    for name in _SHARED_BUILD_FILES:
        path = _nearest_above(project.project_dir, repo_root, name)
        if path is None:
            continue
        if path not in cache:
            cache[path] = _using_items(path)
        for ns, conditions in cache[path]:
            if all(_condition_holds(c, project.properties) for c in conditions):
                usings.add(ns)
    return usings


def _using_items(path: Path) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Each ``<Using Include>`` (C#) or ``<Import Include>`` (VB) item in *path*,
    with the ``Condition`` on it and on each element around it."""
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return ()
    parents = {child: parent for parent in root.iter() for child in parent}
    items = []
    for elem in root.iter():
        ns = elem.get("Include") if _local(elem.tag) in ("Using", "Import") else None
        if not ns:
            continue
        conditions = []
        node: ET.Element | None = elem
        while node is not None:
            if node.get("Condition") is not None:
                conditions.append(node.get("Condition"))
            node = parents.get(node)
        items.append((ns.strip(), tuple(conditions)))
    return tuple(items)


def _nearest_above(start: Path, repo_root: Path, name: str) -> Path | None:
    """The first *name* in *start* or a directory above it, up to *repo_root*."""
    if not start.is_relative_to(repo_root):
        return None
    for directory in (start, *start.parents):
        candidate = directory / name
        if candidate.is_file():
            return candidate
        if directory == repo_root:
            return None
    return None


def _find_project_files(
    repo_path: Path,
    pattern: str,
    *,
    prune_nested_git: bool = True,
    snapshot: Any | None = None,
) -> list[Path]:
    out: list[Path] = []
    for proj in glob_via(snapshot, repo_path, pattern, prune_nested_git=prune_nested_git):
        if path_has_dotnet_scan_skip_dir(proj, repo_path):
            continue
        out.append(proj)
    return out


def find_csproj_files(
    repo_path: Path, *, prune_nested_git: bool = True, snapshot: Any | None = None
) -> list[Path]:
    """Return all .csproj files under *repo_path*, skipping bin/obj output."""
    return _find_project_files(
        repo_path, "*.csproj", prune_nested_git=prune_nested_git, snapshot=snapshot
    )


def find_vbproj_files(
    repo_path: Path, *, prune_nested_git: bool = True, snapshot: Any | None = None
) -> list[Path]:
    """Return all .vbproj files under *repo_path*, skipping bin/obj output."""
    return _find_project_files(
        repo_path, "*.vbproj", prune_nested_git=prune_nested_git, snapshot=snapshot
    )


def path_has_dotnet_scan_skip_dir(path: Path, repo_root: Path) -> bool:
    """Return True when *path* lives under a dotnet-skip directory in *repo_root*."""
    try:
        parts = path.relative_to(repo_root).parts
    except ValueError:
        parts = path.parts
    return any(part.casefold() in _DOTNET_SCAN_SKIP_DIRS_CASEFOLDED for part in parts)
