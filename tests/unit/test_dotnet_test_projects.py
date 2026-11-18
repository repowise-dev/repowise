"""Unit tests for .NET test project detection."""

from __future__ import annotations

import pytest

from repowise.core.dotnet_test_projects import (
    TEST_PACKAGES,
    is_test_project_csproj,
    read_dotnet_test_projects,
)
from repowise.core.test_runners.packages import _declares_tests


@pytest.mark.parametrize(
    ("xml", "expected"),
    [
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n"
            "    <IsTestProject>true</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            True,
        ),
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <istestproject>True</istestproject>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            True,
        ),
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <IsTestProject>false</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            False,
        ),
        # Microsoft.NET.Test.Sdk via PackageReference Include
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.0" />\n'
            '    <PackageReference Include="xunit" Version="2.9.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Microsoft.NET.Test.Sdk via PackageReference Update
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Update="Microsoft.NET.Test.Sdk" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Microsoft.NET.Test.Sdk via PackageVersion Include
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageVersion Include="Microsoft.NET.Test.Sdk" Version="17.11.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Microsoft.NET.Test.Sdk via PackageVersion Update
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageVersion Update="Microsoft.NET.Test.Sdk" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Case-insensitivity in attribute name and package name
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <packagereference include="microsoft.net.test.sdk" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Explicit IsTestProject=false overrides Microsoft.NET.Test.Sdk
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <IsTestProject>false</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="Microsoft.NET.Test.Sdk" Version="17.11.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            False,
        ),
        # Framework-only references: xunit is now in TEST_PACKAGES so it IS a test project
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="MSTest.TestFramework" Version="3.0.0" />\n'
            '    <PackageReference Include="xunit" Version="2.9.0" />\n'
            '    <PackageReference Include="NUnit" Version="4.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # xunit.v3 only — the repro case from issue #5872
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # xunit.v3 + explicit IsTestProject=false — false still wins
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <IsTestProject>false</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            False,
        ),
        # nunit3testadapter only
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="NUnit3TestAdapter" Version="4.6.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # tunit only
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="TUnit" Version="0.3.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Conditioned test package reference does not make it a test project
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <ItemGroup Condition="\'$(Configuration)\' == \'Debug\'">\n'
            '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            False,
        ),
        # Conditioned IsTestProject=false does not override unconditional test package
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <PropertyGroup Condition="\'$(Configuration)\' == \'Release\'">\n'
            "    <IsTestProject>false</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "  <ItemGroup>\n"
            '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
            "  </ItemGroup>\n"
            "</Project>\n",
            True,
        ),
        # Conditioned IsTestProject=true does not make it a test project
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            '  <PropertyGroup Condition="\'$(Configuration)\' == \'Debug\'">\n'
            "    <IsTestProject>true</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            False,
        ),
        # Classic MSBuild XML with xmlns
        (
            '<Project xmlns="http://schemas.microsoft.com/developer/msbuild/2003">\n'
            "  <PropertyGroup>\n"
            "    <IsTestProject>true</IsTestProject>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            True,
        ),
        # Regular non-test project
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <PropertyGroup>\n"
            "    <TargetFramework>net8.0</TargetFramework>\n"
            "  </PropertyGroup>\n"
            "</Project>\n",
            False,
        ),
        # Malformed XML yields None (unknown)
        ("<Project><Unclosed>", None),
        ("", None),
    ],
)
def test_is_test_project_csproj(xml: str, expected: bool | None) -> None:
    assert is_test_project_csproj(xml) is expected


def test_nearest_project_wins_for_nested_directories() -> None:
    projects = read_dotnet_test_projects(
        [
            (
                "src/App/App.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n"
                "</Project>\n",
            ),
            (
                "acceptance/Acme.Acceptance.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup>\n"
                "</Project>\n",
            ),
            (
                "tests/Tests.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup>\n"
                "</Project>\n",
            ),
            (
                "tests/fixtures/sample_app/Sample.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><IsTestProject>false</IsTestProject></PropertyGroup>\n"
                "</Project>\n",
            ),
        ]
    )
    # File in acceptance test project
    assert projects.collects("acceptance/Authentication/AuthSteps.cs") is True
    # File under src/App/App.csproj
    assert projects.collects("src/App/Program.cs") is False
    # File directly under tests/ test project
    assert projects.collects("tests/UnitTest.cs") is True
    # Nested sample app inside tests/ has its own non-test project: nearest project wins
    assert projects.collects("tests/fixtures/sample_app/Program.cs") is False
    assert projects.collects("tests/fixtures/sample_app/Sub/Util.cs") is False
    # File outside any project
    assert projects.collects("other/External.cs") is None


def test_root_project_governs_repository_descendants() -> None:
    projects = read_dotnet_test_projects(
        [
            (
                "Root.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><TargetFramework>net8.0</TargetFramework></PropertyGroup>\n"
                "</Project>\n",
            ),
            (
                "tests/Tests.csproj",
                '<Project Sdk="Microsoft.NET.Sdk">\n'
                "  <PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup>\n"
                "</Project>\n",
            ),
        ]
    )
    assert projects.collects("other/External.cs") is False
    assert projects.collects("tests/MyTest.cs") is True



def test_unreadable_project_yields_unknown() -> None:
    projects = read_dotnet_test_projects(
        [
            ("malformed/Bad.csproj", "<Project><"),
            (
                "outer/Outer.csproj",
                '<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><IsTestProject>true</IsTestProject></PropertyGroup></Project>',
            ),
            ("outer/inner/Inner.csproj", "<Project><broken"),
        ]
    )
    assert projects.collects("malformed/File.cs") is None
    assert projects.collects("outer/Valid.cs") is True
    assert projects.collects("outer/inner/File.cs") is None


# ---------------------------------------------------------------------------
# Issue #5872 — xunit.v3-only project must be classified as a test project
# by the traverser (collects) just as it is by the runner planner.
# ---------------------------------------------------------------------------

_XUNIT_V3_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">\n'
    "  <PropertyGroup>\n"
    "    <TargetFramework>net9.0</TargetFramework>\n"
    "  </PropertyGroup>\n"
    "  <ItemGroup>\n"
    '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
    "  </ItemGroup>\n"
    "</Project>\n"
)

_NAME_ONLY_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">\n'
    "  <PropertyGroup>\n"
    "    <TargetFramework>net9.0</TargetFramework>\n"
    "  </PropertyGroup>\n"
    "</Project>\n"
)

_EXPLICIT_FALSE_CSPROJ = (
    '<Project Sdk="Microsoft.NET.Sdk">\n'
    "  <PropertyGroup>\n"
    "    <IsTestProject>false</IsTestProject>\n"
    "  </PropertyGroup>\n"
    "  <ItemGroup>\n"
    '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
    "  </ItemGroup>\n"
    "</Project>\n"
)


def test_xunit_v3_only_project_collects_as_test() -> None:
    """A project with only xunit.v3 (no Microsoft.NET.Test.Sdk) is a test project."""
    projects = read_dotnet_test_projects(
        [("tests/Shop.Tests/Shop.Tests.csproj", _XUNIT_V3_CSPROJ)]
    )
    assert projects.collects("tests/Shop.Tests/CartTests.cs") is True


def test_xunit_v3_only_is_test_project_csproj_agrees() -> None:
    """is_test_project_csproj and runner _declares_tests agree on xunit.v3-only."""
    assert is_test_project_csproj(_XUNIT_V3_CSPROJ) is True
    assert _declares_tests(None, ["xunit.v3"]) is True
    projects = read_dotnet_test_projects(
        [("tests/Shop.Tests/Shop.Tests.csproj", _XUNIT_V3_CSPROJ)]
    )
    assert projects.collects("tests/Shop.Tests/CartTests.cs") is True


def test_name_only_project_does_not_collect_via_csproj_alone() -> None:
    """A project whose csproj has no test signals is not a test project by itself.

    The name-based rule (``Shop.Tests``) lives in ``test_runners/packages.py``
    and is applied by the *runner planner*, not by the traverser.  The traverser
    relies solely on the csproj verdict.
    """
    assert is_test_project_csproj(_NAME_ONLY_CSPROJ) is False
    assert _declares_tests(None, []) is False
    projects = read_dotnet_test_projects(
        [("tests/Shop.Tests/Shop.Tests.csproj", _NAME_ONLY_CSPROJ)]
    )
    assert projects.collects("tests/Shop.Tests/CartTests.cs") is False


def test_explicit_is_test_project_false_wins_over_xunit_v3() -> None:
    """IsTestProject=false wins even when xunit.v3 is referenced, for both traverser and runner."""
    assert is_test_project_csproj(_EXPLICIT_FALSE_CSPROJ) is False
    assert _declares_tests(False, ["xunit.v3"]) is False
    assert _declares_tests(False, ["microsoft.net.test.sdk"]) is False
    projects = read_dotnet_test_projects(
        [("tests/Shop.Tests/Shop.Tests.csproj", _EXPLICIT_FALSE_CSPROJ)]
    )
    assert projects.collects("tests/Shop.Tests/CartTests.cs") is False


def test_test_packages_is_exported() -> None:
    """TEST_PACKAGES is importable and contains the expected ids."""
    assert "xunit.v3" in TEST_PACKAGES
    assert "microsoft.net.test.sdk" in TEST_PACKAGES
    assert "nunit3testadapter" in TEST_PACKAGES


def test_conditioned_opt_out_is_ignored_by_both() -> None:
    """A conditioned IsTestProject=false does not override an unconditional test package."""
    xml = (
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        '  <PropertyGroup Condition="\'$(Configuration)\' == \'Release\'">\n'
        "    <IsTestProject>false</IsTestProject>\n"
        "  </PropertyGroup>\n"
        "  <ItemGroup>\n"
        '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )
    assert is_test_project_csproj(xml) is True
    assert _declares_tests(None, ["xunit.v3"]) is True
    projects = read_dotnet_test_projects([("tests/Shop.Tests/Shop.Tests.csproj", xml)])
    assert projects.collects("tests/Shop.Tests/CartTests.cs") is True


def test_conditioned_test_package_is_ignored_by_both() -> None:
    """A conditioned PackageReference does not classify a project as a test project."""
    xml = (
        '<Project Sdk="Microsoft.NET.Sdk">\n'
        '  <ItemGroup Condition="\'$(Configuration)\' == \'Debug\'">\n'
        '    <PackageReference Include="xunit.v3" Version="1.0.0" />\n'
        "  </ItemGroup>\n"
        "</Project>\n"
    )
    assert is_test_project_csproj(xml) is False
    assert _declares_tests(None, []) is False
    projects = read_dotnet_test_projects([("src/Shop/Shop.csproj", xml)])
    assert projects.collects("src/Shop/Cart.cs") is False
