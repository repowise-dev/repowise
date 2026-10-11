"""Tests for cargo, go, and nuget manifest parsers."""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.external_systems import cargo, go, nuget


def _write(tmp_path: Path, rel: str, content: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_cargo_parses_sections_and_skips_path_deps(tmp_path):
    p = _write(
        tmp_path,
        "Cargo.toml",
        """
[dependencies]
serde = "1.0"
tokio = {version = "1.0", features = ["full"]}
local = {path = "../local"}

[dev-dependencies]
pretty_assertions = "1.0"
""",
    )
    records = cargo.parse(p, tmp_path)
    names = {r.name for r in records}
    assert names == {"serde", "tokio", "pretty_assertions"}
    by_name = {r.name: r for r in records}
    assert by_name["tokio"].version == "1.0"
    assert by_name["pretty_assertions"].is_dev_dep is True


def test_go_parses_block_and_drops_local_replacements(tmp_path):
    p = _write(
        tmp_path,
        "go.mod",
        """
module github.com/example/app

go 1.22

require (
    github.com/gin-gonic/gin v1.10.0
    github.com/google/uuid v1.6.0 // indirect
)

require github.com/sirupsen/logrus v1.9.3

replace github.com/internal/lib => ../lib
""",
    )
    records = go.parse(p, tmp_path)
    by_name = {r.name: r for r in records}
    assert "github.com/gin-gonic/gin" in by_name
    assert "github.com/sirupsen/logrus" in by_name
    assert by_name["github.com/google/uuid"].is_dev_dep is True  # // indirect → dev
    assert by_name["github.com/gin-gonic/gin"].version == "v1.10.0"


def test_go_drops_local_path_replacements(tmp_path):
    p = _write(
        tmp_path,
        "go.mod",
        """
module example
require github.com/internal/lib v0.1.0
replace github.com/internal/lib => ./internal/lib
""",
    )
    records = go.parse(p, tmp_path)
    assert records == []


def test_nuget_parses_package_references(tmp_path):
    p = _write(
        tmp_path,
        "App.csproj",
        """<?xml version="1.0"?>
<Project Sdk="Microsoft.NET.Sdk">
  <ItemGroup>
    <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    <PackageReference Include="Serilog">
      <Version>3.1.1</Version>
    </PackageReference>
    <ProjectReference Include="..\\Other\\Other.csproj" />
  </ItemGroup>
</Project>
""",
    )
    records = nuget.parse(p, tmp_path)
    by_name = {r.name: r for r in records}
    assert set(by_name) == {"Newtonsoft.Json", "Serilog"}
    assert by_name["Newtonsoft.Json"].version == "13.0.3"
    assert by_name["Serilog"].version == "3.1.1"


def test_malformed_inputs_return_empty(tmp_path):
    bad_cargo = _write(tmp_path, "Cargo.toml", "this isn't toml [[")
    assert cargo.parse(bad_cargo, tmp_path) == []
    bad_xml = _write(tmp_path, "Bad.csproj", "<Project><not closed")
    assert nuget.parse(bad_xml, tmp_path) == []
