"""Unit tests for .NET test project detection."""

from __future__ import annotations

import pytest

from repowise.core.dotnet_test_projects import (
    is_test_project_csproj,
    read_dotnet_test_projects,
)


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
        # Framework-only references without SDK are NOT test projects
        (
            '<Project Sdk="Microsoft.NET.Sdk">\n'
            "  <ItemGroup>\n"
            '    <PackageReference Include="MSTest.TestFramework" Version="3.0.0" />\n'
            '    <PackageReference Include="xunit" Version="2.9.0" />\n'
            '    <PackageReference Include="NUnit" Version="4.0.0" />\n'
            "  </ItemGroup>\n"
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
