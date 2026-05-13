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

    def test_element_tag_with_prefix_extracted(self) -> None:
        """``<converters:BoolToVisibilityConverter />`` registers ``BoolToVisibilityConverter``."""
        text = '<Page><converters:BoolToVisibilityConverter x:Key="b2v"/></Page>'
        refs = _extract_type_references(text, {"converters": "Acme.Converters"})
        assert "BoolToVisibilityConverter" in refs

    def test_element_tag_property_syntax_skipped(self) -> None:
        """``<Grid.Resources>`` is property-element syntax, not a type reference."""
        text = '<Grid><Grid.Resources/></Grid>'
        refs = _extract_type_references(text, {})
        assert "Resources" not in refs

    def test_bare_xaml_element_not_a_type_reference(self) -> None:
        """Built-in XAML elements (``<Grid>``, ``<TextBlock>``) must not bind."""
        text = '<Page><Grid><TextBlock/></Grid></Page>'
        refs = _extract_type_references(text, {})
        assert "Grid" not in refs
        assert "TextBlock" not in refs

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

    def test_no_csproj_emits_no_type_binding_edges(self, tmp_path: Path) -> None:
        # A repo with XAML but no .csproj has no .NET type index to
        # bind against; type-binding edges silently produce nothing.
        # ResourceDictionary edges remain xaml→xaml and don't need the
        # type map — they're tested separately.
        (tmp_path / "View.xaml").write_text(
            '<Page xmlns:vm="using:Foo" x:DataType="vm:Bar"/>'
        )
        edges = XamlDynamicHints().extract(tmp_path)
        # No binding edges should fire — and with no other xaml in the
        # tree the list is empty.
        assert edges == []


class TestResourceDictionaryEdges:
    """``<ResourceDictionary Source="..."/>`` cross-references between XAML files."""

    def test_relative_source_resolves_to_sibling_xaml(self, tmp_path: Path) -> None:
        (tmp_path / "Themes").mkdir()
        (tmp_path / "Themes" / "Light.xaml").write_text(
            '<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"/>'
        )
        (tmp_path / "Themes" / "App.xaml").write_text(
            '<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation">\n'
            '  <ResourceDictionary.MergedDictionaries>\n'
            '    <ResourceDictionary Source="Light.xaml"/>\n'
            '  </ResourceDictionary.MergedDictionaries>\n'
            '</ResourceDictionary>\n'
        )
        edges = {(e.source, e.target) for e in XamlDynamicHints().extract(tmp_path)}
        assert ("Themes/App.xaml", "Themes/Light.xaml") in edges

    def test_pack_uri_strips_assembly_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "Themes").mkdir()
        (tmp_path / "Themes" / "Dark.xaml").write_text(
            '<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"/>'
        )
        (tmp_path / "Themes" / "Generic.xaml").write_text(
            '<ResourceDictionary xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"\n'
            '  Source="pack://application:,,,/Acme.UI;component/Themes/Dark.xaml"/>\n'
        )
        edges = {(e.source, e.target) for e in XamlDynamicHints().extract(tmp_path)}
        assert ("Themes/Generic.xaml", "Themes/Dark.xaml") in edges

    def test_ms_appx_uri_resolves(self, tmp_path: Path) -> None:
        (tmp_path / "Styles").mkdir()
        (tmp_path / "Styles" / "Buttons.xaml").write_text("<ResourceDictionary/>")
        (tmp_path / "App.xaml").write_text(
            '<ResourceDictionary>\n'
            '  <ResourceDictionary Source="ms-appx:///Styles/Buttons.xaml"/>\n'
            '</ResourceDictionary>'
        )
        edges = {(e.source, e.target) for e in XamlDynamicHints().extract(tmp_path)}
        assert ("App.xaml", "Styles/Buttons.xaml") in edges

    def test_self_reference_dropped(self, tmp_path: Path) -> None:
        (tmp_path / "Self.xaml").write_text(
            '<ResourceDictionary>\n'
            '  <ResourceDictionary Source="Self.xaml"/>\n'
            '</ResourceDictionary>'
        )
        edges = XamlDynamicHints().extract(tmp_path)
        assert edges == []

    def test_no_resource_dictionary_no_edges(self, tmp_path: Path) -> None:
        (tmp_path / "Plain.xaml").write_text(
            '<Page xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"/>'
        )
        edges = XamlDynamicHints().extract(tmp_path)
        assert edges == []


class TestLanguageRegistration:
    """``.xaml``/``.axaml`` must surface as their own LanguageTag so
    the traverser produces file nodes the dynamic edges can attach to.

    Regression guard: before this branch, the extractor emitted edges
    correctly but ``GraphBuilder.add_dynamic_edges`` dropped them
    because ``.xaml`` files were never added as graph nodes.
    """

    def test_xaml_extension_resolves_to_xaml_tag(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        assert REGISTRY.from_extension(".xaml") == "xaml"
        assert REGISTRY.from_extension(".axaml") == "xaml"

    def test_xaml_is_passthrough_not_code(self) -> None:
        from repowise.core.ingestion.languages.registry import REGISTRY

        spec = REGISTRY.get("xaml")
        assert spec is not None
        assert spec.is_passthrough is True
        assert spec.is_code is False
