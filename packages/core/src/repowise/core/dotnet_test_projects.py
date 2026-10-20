"""Where .NET test projects live, read from .csproj files.

A C# test project sets `<IsTestProject>true</IsTestProject>` or references
`Microsoft.NET.Test.Sdk`. Files inside such a project (step definitions, fakes,
helpers) are test material even if their filename or directory does not say Test.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import PurePosixPath

_TEST_SDK_PACKAGE = "microsoft.net.test.sdk"


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


def is_test_project_csproj(text: str) -> bool | None:
    """Whether a .csproj XML string describes a test project.

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
    has_test_sdk = False

    for elem in root.iter():
        local_tag = elem.tag.split("}")[-1].lower()
        if local_tag == "istestproject":
            val = (elem.text or "").strip().lower()
            if val == "true":
                explicit_is_test = True
            elif val == "false":
                explicit_is_test = False
        elif local_tag in ("packagereference", "packageversion"):
            for attr_name, attr_val in elem.attrib.items():
                if (
                    attr_name.split("}")[-1].lower() in ("include", "update")
                    and attr_val.strip().lower() == _TEST_SDK_PACKAGE
                ):
                    has_test_sdk = True

    if explicit_is_test is False:
        return False
    if explicit_is_test is True:
        return True
    return has_test_sdk


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
