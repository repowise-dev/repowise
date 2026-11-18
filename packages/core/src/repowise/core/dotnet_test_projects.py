"""Where .NET test projects live, read from .csproj files.

A C# test project sets `<IsTestProject>true</IsTestProject>` or references any
of the well-known NuGet test packages (the Test SDK, xunit.v3, NUnit adapters,
MSTest, TUnit …). Files inside such a project (step definitions, fakes, helpers)
are test material even if their filename or directory does not say Test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

#: NuGet ids (lower-cased) that make a .csproj a test project.  Single source
#: of truth — ``test_runners/packages.py`` imports this instead of keeping its
#: own copy.  Extend here when a new test framework arrives; both the traverser
#: and the runner planner will pick up the change automatically.
TEST_PACKAGES: frozenset[str] = frozenset(
    {
        "microsoft.net.test.sdk",
        "mstest",
        "mstest.testadapter",
        "nunit3testadapter",
        "tunit",
        "xunit",
        "xunit.runner.visualstudio",
        "xunit.v3",
        "xunit.v3.mtp-v1",
        "xunit.v3.mtp-v2",
    }
)


@dataclass(frozen=True, slots=True)
class DotnetTestProjects:
    """Directory-level verdict on whether .csproj files mark a project as a test project."""

    by_dir: Mapping[str, bool | None] = field(default_factory=dict)

    def collects(self, path: str) -> bool | None:
        """Whether the nearest .csproj governing *path* is a test project.

        Returns:
            True: nearest project is a test project.
            False: nearest project is not a test project.
            None: no project governs *path*, or nearest project is unreadable.
        """
        p = PurePosixPath(path.replace("\\", "/"))
        for parent in p.parents:
            key = "" if str(parent) == "." else str(parent)
            if key in self.by_dir:
                return self.by_dir[key]
        return None


def _conditioned(root: ET.Element) -> set[int]:
    """Ids of the elements a ``Condition`` applies to, itself or an ancestor's."""
    return {
        id(element)
        for scope in root.iter()
        if scope.get("Condition") is not None
        for element in scope.iter()
    }


def is_test_project_csproj(text: str) -> bool | None:
    """Whether a .csproj XML string describes a test project.

    A project is a test project when ``<IsTestProject>true</IsTestProject>`` is
    set, or when it references any package in :data:`TEST_PACKAGES`.  An
    explicit ``<IsTestProject>false</IsTestProject>`` wins regardless of which
    packages are present.  Elements under a ``Condition`` are ignored because
    an unevaluated condition cannot be judged.

    Returns:
        True: is a test project.
        False: is not a test project.
        None: XML parse error (unknown).
    """
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None

    explicit_is_test: bool | None = (
        True if "mstest.sdk" in (root.get("Sdk") or "").lower() else None
    )
    has_test_package = False
    conditioned = _conditioned(root)

    for elem in root.iter():
        if id(elem) in conditioned:
            continue
        local_tag = elem.tag.split("}")[-1].lower()
        if local_tag == "istestproject":
            val = (elem.text or "").strip().lower()
            if val in ("true", "enable", "1"):
                explicit_is_test = True
            elif val in ("false", "disable", "0"):
                explicit_is_test = False
        elif local_tag in ("packagereference", "packageversion"):
            for attr_name, attr_val in elem.attrib.items():
                if (
                    attr_name.split("}")[-1].lower() in ("include", "update")
                    and attr_val.strip().lower() in TEST_PACKAGES
                ):
                    has_test_package = True

    if explicit_is_test is False:
        return False
    if explicit_is_test is True:
        return True
    return has_test_package


def read_dotnet_test_projects(files: Iterable[tuple[str, str]]) -> DotnetTestProjects:
    """Build DotnetTestProjects from iterable of (rel_path, text) pairs."""
    by_dir: dict[str, bool | None] = {}
    for path, text in files:
        p = PurePosixPath(path.replace("\\", "/"))
        key = "" if str(p.parent) == "." else str(p.parent)
        verdict = is_test_project_csproj(text)
        if key not in by_dir:
            by_dir[key] = verdict
        else:
            existing = by_dir[key]
            if verdict is True or existing is True:
                by_dir[key] = True
            elif verdict is False or existing is False:
                by_dir[key] = False
            else:
                by_dir[key] = None
    return DotnetTestProjects(by_dir)
