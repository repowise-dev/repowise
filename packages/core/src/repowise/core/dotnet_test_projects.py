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

from .ingestion.resolvers.dotnet.msbuild import conditioned_ids, tristate

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


def _is_test_package_elem(elem: ET.Element) -> bool:
    """Whether *elem* is an Include or Update of a test package."""
    tag = elem.tag.split("}")[-1].lower()
    if tag not in ("packagereference", "packageversion"):
        return False
    for attr_name, attr_val in elem.attrib.items():
        if (
            attr_name.split("}")[-1].lower() in ("include", "update")
            and attr_val.strip().lower() in TEST_PACKAGES
        ):
            return True
    return False


def is_test_project_csproj(text: str) -> bool | None:
    """Whether a .csproj XML string describes a test project.

    A project is a test project when ``<IsTestProject>true</IsTestProject>`` is
    set, or when it references any package in :data:`TEST_PACKAGES`.  An
    explicit ``<IsTestProject>false</IsTestProject>`` wins regardless of which
    packages are present.  A conditioned ``IsTestProject`` is ignored because an
    unevaluated condition cannot be judged; a conditioned test package still
    counts, since the project runs tests for at least one target.

    Returns:
        True: is a test project.
        False: is not a test project.
        None: XML parse error (unknown).
    """
    try:
        root = ET.fromstring(text)
    except (ET.ParseError, ValueError):
        return None

    explicit_is_test: bool | None = None
    if "mstest.sdk" in (root.get("Sdk") or "").lower():
        explicit_is_test = True

    has_test_package = False
    conditioned = conditioned_ids(root)

    for elem in root.iter():
        tag = elem.tag.split("}")[-1].lower()
        if tag == "istestproject":
            if id(elem) not in conditioned and (flag := tristate(elem.text)) is not None:
                explicit_is_test = flag
        elif _is_test_package_elem(elem):
            # Whatever its Condition: per-TFM groups still mean tests for some target.
            has_test_package = True

    if explicit_is_test is not None:
        return explicit_is_test
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
