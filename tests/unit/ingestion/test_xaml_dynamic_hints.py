"""Unit tests for the XAML dynamic-hint extractor.

Covers WinUI 3 ``using:`` and WPF ``clr-namespace:`` xmlns shapes,
``x:DataType`` extraction, and end-to-end edge emission against a
mini workspace with a .csproj on disk (so the C# type_map gets built).
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion.dynamic_hints.xaml import (
    XamlDynamicHints,
    _collect_prefix_namespaces,
    _extract_type_references,
)


_CSPROJ = """<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup>
    <TargetFramework>net8.0-windows10.0.19041.0</TargetFramework>
    <UseWPF>true</UseWPF>
  </PropertyGroup>
</Project>
"""


class TestPrefixCollection:
    def test_winui_using_prefix(self) -> None:
        text = '<Page xmlns:vm="using:Acme.ViewModels">'
        assert _collect_prefix_namespaces(text) == {"vm": "Acme.ViewModels"}

    def test_wpf_clr_namespace(self) -> None:
        text = '<Window xmlns:m="clr-namespace:Acme.Models">'
        assert _collect_prefix_namespaces(text) == {"m": "Acme.Models"}

    def test_wpf_cross_assembly(self) -> None:
        text = '<Window xmlns:m="clr-namespace:Acme.Models;assembly=Acme.Domain">'
        assert _collect_prefix_namespaces(text) == {"m": "Acme.Models"}

    def test_multiple(self) -> None:
        text = (
            '<Page xmlns:vm="using:Acme.ViewModels" '
            'xmlns:m="clr-namespace:Acme.Models">'
        )
        result = _collect_prefix_namespaces(text)
        assert result["vm"] == "Acme.ViewModels"
        assert result["m"] == "Acme.Models"


class TestTypeReferenceExtraction:
    def test_x_datatype(self) -> None:
        text = '<Page><Grid x:DataType="vm:GeneralViewModel"/></Page>'
        refs = _extract_type_references(text, {"vm": "Acme.ViewModels"})
        assert "GeneralViewModel" in refs

    def test_xtype_markup_extension(self) -> None:
        text = '<Page DataContext="{x:Type vm:SettingsViewModel}"/>'
        refs = _extract_type_references(text, {"vm": "Acme.ViewModels"})
        assert "SettingsViewModel" in refs

    def test_lowercase_attribute_value_skipped(self) -> None:
        # Attribute values that don't start with an uppercase letter
        # (e.g. property names accidentally captured) are skipped.
        text = '<Page TargetType="control"/>'
        refs = _extract_type_references(text, {})
        assert "control" not in refs


class TestEndToEnd:
    def test_emits_edge_from_xaml_to_viewmodel_file(self, tmp_path: Path) -> None:
        (tmp_path / "App").mkdir()
        (tmp_path / "App" / "App.csproj").write_text(_CSPROJ)
        (tmp_path / "App" / "ViewModels").mkdir()
        (tmp_path / "App" / "ViewModels" / "GeneralViewModel.cs").write_text(
            "namespace App.ViewModels;\npublic class GeneralViewModel {}\n"
        )
        (tmp_path / "App" / "Views").mkdir()
        (tmp_path / "App" / "Views" / "GeneralPage.xaml").write_text(
            '<Page xmlns:vm="using:App.ViewModels" x:DataType="vm:GeneralViewModel">\n'
            "</Page>\n"
        )

        edges = XamlDynamicHints().extract(tmp_path)
        sources = {(e.source, e.target) for e in edges}
        assert (
            "App/Views/GeneralPage.xaml",
            "App/ViewModels/GeneralViewModel.cs",
        ) in sources

    def test_no_csproj_emits_nothing(self, tmp_path: Path) -> None:
        # A repo with XAML but no .csproj has no .NET type index to
        # bind against; the extractor should silently produce nothing
        # rather than spuriously create external edges.
        (tmp_path / "View.xaml").write_text(
            '<Page xmlns:vm="using:Foo" x:DataType="vm:Bar"/>'
        )
        assert XamlDynamicHints().extract(tmp_path) == []
