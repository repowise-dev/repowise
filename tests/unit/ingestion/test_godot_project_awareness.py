"""Unit tests for Godot project awareness.

Four mechanisms, none of which needs the ``tree_sitter_gdscript`` grammar
except where a test parses real GDScript:

1. ``lightweight_imports/godot.py``: ``[ext_resource]`` / ``[autoload]`` /
   ``run/main_scene`` / ``plugin.cfg`` ``script`` extraction.
2. ``graph_warmups._warmup_godot``: engine-invoked entry points, and the
   ``addons/`` vendoring rule.
3. ``framework_edges/godot.py``: a scene's ``[connection]`` blocks, resolved
   through the node tree to the handler method they name.
4. ``framework_edges/godot.py``: ``class_name`` global-registry edges.

The dead-code side (Godot engine callbacks) is covered at the bottom against
:func:`is_contract_method` directly, matching how the JVM and C++ contract
sets are tested.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.core.analysis.dead_code.contract_methods import is_contract_method
from repowise.core.ingestion.framework_edges import DetectionContext, add_framework_edges
from repowise.core.ingestion.framework_edges import godot as godot_edges
from repowise.core.ingestion.graph_warmups import _warmup_godot
from repowise.core.ingestion.languages.registry import REGISTRY
from repowise.core.ingestion.lightweight_imports import extract_lightweight_imports
from repowise.core.ingestion.lightweight_imports.godot import extract_godot_imports
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

SCENE = """\
[gd_scene load_steps=4 format=3 uid="uid://cahno8aso5net"]

[ext_resource type="Script" path="res://scenes/battle/battle.gd" id="1_4s751"]
[ext_resource type="Texture2D" uid="uid://cp4iq" path="res://art/background.png" id="1_ybkl6"]
[ext_resource type="PackedScene" path="res://scenes/enemy/enemy.tscn" id="2_02s5s"]
[ext_resource type="Resource" path="res://characters/warrior.tres" id="4_fwb8a"]

[node name="Battle" type="Node2D"]
script = ExtResource("1_4s751")
"""

PROJECT_GODOT = """\
; Engine configuration file.
config_version=5

[application]

config/name="Deck Builder"
run/main_scene="res://scenes/run/run.tscn"
config/icon="res://icon.png"

[autoload]

Events="*res://global/events.gd"
MusicPlayer="*res://global/music_player.tscn"
Keychain="*uid://dgiia2xg7fsud"

[display]

window/size/viewport_width=256
"""

PLUGIN_CFG = """\
[plugin]

name="Dialogic"
description="Create dialogs and characters."
author="Jowan"
version="2.0"
script="plugin.gd"
"""


def _paths(imports) -> list[str]:
    return [imp.module_path for imp in imports]


def _file_info(rel: str, language: str, abs_path: str = "") -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=abs_path or rel,
        language=language,  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


def _ctx(paths: set[str], repo_path: Path | None = None, **kwargs) -> ResolverContext:
    graph = kwargs.pop("graph", None) or nx.DiGraph()
    return ResolverContext(
        path_set=paths, stem_map={}, graph=graph, repo_path=repo_path, **kwargs
    )


# ---------------------------------------------------------------------------
# 1. Extraction
# ---------------------------------------------------------------------------


class TestSceneExtraction:
    def test_ext_resource_scripts_scenes_and_resources(self) -> None:
        assert _paths(extract_godot_imports(SCENE)) == [
            "res://scenes/battle/battle.gd",
            "res://scenes/enemy/enemy.tscn",
            "res://characters/warrior.tres",
        ]

    def test_art_assets_are_dropped(self) -> None:
        # background.png sits in the same [ext_resource] list and is not code.
        assert "res://art/background.png" not in _paths(extract_godot_imports(SCENE))

    def test_godot_3_attribute_order(self) -> None:
        line = '[ext_resource path="res://a.gd" type="Script" id=1]\n'
        assert _paths(extract_godot_imports(line)) == ["res://a.gd"]

    def test_single_quoted_path(self) -> None:
        # The editor writes double quotes; a hand-edited or tool-generated
        # scene may use single ones.
        line = "[ext_resource type='Script' path='res://a.gd' id='2']\n"
        assert _paths(extract_godot_imports(line)) == ["res://a.gd"]

    def test_uid_only_ext_resource_yields_nothing(self) -> None:
        # Godot 4.4 may omit path=. Documented ceiling, recorded as no edge
        # rather than a guess.
        line = '[ext_resource type="Script" uid="uid://bxyz" id="1_a"]\n'
        assert extract_godot_imports(line) == []

    def test_script_extresource_reference_is_not_a_second_edge(self) -> None:
        # `script = ExtResource("1_4s751")` dereferences the header above it.
        assert _paths(extract_godot_imports(SCENE)).count("res://scenes/battle/battle.gd") == 1


class TestProjectGodotExtraction:
    def test_autoloads_and_main_scene(self) -> None:
        assert _paths(extract_godot_imports(PROJECT_GODOT)) == [
            "res://scenes/run/run.tscn",
            "res://global/events.gd",
            "res://global/music_player.tscn",
        ]

    def test_singleton_star_prefix_is_stripped(self) -> None:
        assert not any(p.startswith("*") for p in _paths(extract_godot_imports(PROJECT_GODOT)))

    def test_uid_autoload_is_skipped_by_the_suffix_filter(self) -> None:
        # `Keychain="*uid://…"` carries no suffix we recognise as code, so it
        # produces no import. Documented ceiling.
        assert not any("uid://" in p for p in _paths(extract_godot_imports(PROJECT_GODOT)))

    def test_only_main_scene_is_read_from_the_application_section(self) -> None:
        # `config/icon` and `config/name` sit in the same section; the key
        # match, not the suffix filter, is what excludes them.
        assert _paths(extract_godot_imports(PROJECT_GODOT)) == [
            "res://scenes/run/run.tscn",
            "res://global/events.gd",
            "res://global/music_player.tscn",
        ]

    def test_a_section_we_do_not_read_contributes_nothing(self) -> None:
        # [display]'s `window/size/viewport_width=256` is key=value shaped and
        # would match _AUTOLOAD_RE if the section gate were not doing its job.
        with_display = PROJECT_GODOT.replace(
            '[display]', '[display]\n\nfake/path="res://sneaky.gd"'
        )
        assert "res://sneaky.gd" not in _paths(extract_godot_imports(with_display))


class TestPluginCfgExtraction:
    def test_script_key_only(self) -> None:
        assert _paths(extract_godot_imports(PLUGIN_CFG)) == ["plugin.gd"]

    def test_a_multiline_description_does_not_lose_the_script_key(self) -> None:
        # dialogic's real plugin.cfg: the description runs onto a second line.
        # `script=` is conventionally last, so a parser that mishandles the
        # continuation loses the addon's only entry point.
        multiline = (
            '[plugin]\n\n'
            'name="Dialogic"\n'
            'description="Create dialogs and characters.\n'
            'https://github.com/dialogic-godot/dialogic"\n'
            'author="Jowan"\n'
            'script="plugin.gd"\n'
        )
        assert _paths(extract_godot_imports(multiline)) == ["plugin.gd"]

    def test_bbcode_in_a_description_cannot_close_the_plugin_section(self) -> None:
        # `[b]` on its own line is legal BBCode inside a description and is a
        # perfect bare-section-header match.
        bbcode = (
            '[plugin]\n\n'
            'description="Bold:\n'
            '[b]\n'
            'done."\n'
            'script="plugin.gd"\n'
        )
        assert _paths(extract_godot_imports(bbcode)) == ["plugin.gd"]

    def test_a_utf8_bom_does_not_hide_the_first_section(self) -> None:
        assert _paths(extract_godot_imports("﻿" + PLUGIN_CFG)) == ["plugin.gd"]

    def test_crlf_line_endings(self) -> None:
        assert _paths(extract_godot_imports(PLUGIN_CFG.replace("\n", "\r\n"))) == [
            "plugin.gd"
        ]


class TestDispatch:
    def test_registered_for_the_godot_resource_tag(self) -> None:
        imports = extract_lightweight_imports(
            _file_info("scenes/battle.tscn", "godot_resource"), SCENE.encode()
        )
        assert _paths(imports) == [
            "res://scenes/battle/battle.gd",
            "res://scenes/enemy/enemy.tscn",
            "res://characters/warrior.tres",
        ]

    def test_extensions_and_special_filenames_map_to_the_tag(self) -> None:
        for ext in (".tscn", ".tres", ".escn"):
            assert REGISTRY.from_extension(ext) == "godot_resource"
        assert REGISTRY.from_filename("project.godot") == "godot_resource"
        assert REGISTRY.from_filename("plugin.cfg") == "godot_resource"

    def test_scenes_share_the_gdscript_resolver(self, tmp_path: Path) -> None:
        (tmp_path / "project.godot").write_text("config_version=5\n", encoding="utf-8")
        ctx = _ctx({"scenes/battle.tscn", "scenes/battle/battle.gd"}, repo_path=tmp_path)
        got = resolve_import(
            "res://scenes/battle/battle.gd", "scenes/battle.tscn", "godot_resource", ctx
        )
        assert got == "scenes/battle/battle.gd"

    def test_plugin_cfg_script_resolves_relative_to_its_own_directory(
        self, tmp_path: Path
    ) -> None:
        ctx = _ctx({"addons/dialogic/plugin.cfg", "addons/dialogic/plugin.gd"}, repo_path=tmp_path)
        got = resolve_import("plugin.gd", "addons/dialogic/plugin.cfg", "godot_resource", ctx)
        assert got == "addons/dialogic/plugin.gd"

    def test_asset_paths_yield_no_edge_at_all(self, tmp_path: Path) -> None:
        # Not an external node either: a .png in the dependency graph says
        # nothing about how the code fits together.
        ctx = _ctx({"actors/player.gd"}, repo_path=tmp_path)
        assert resolve_import("res://art/icon.png", "actors/player.gd", "gdscript", ctx) is None
        assert not list(ctx.graph.nodes())

    def test_an_indexed_data_file_still_gets_its_edge(self, tmp_path: Path) -> None:
        # `.json` is on the asset list AND is a language repowise indexes. The
        # asset filter decides what a *miss* becomes, so it must not fire
        # before the path_set lookup.
        ctx = _ctx({"actors/player.gd", "data/cards.json"}, repo_path=tmp_path)
        got = resolve_import("res://data/cards.json", "actors/player.gd", "gdscript", ctx)
        assert got == "data/cards.json"

    def test_an_unrecognised_suffix_stays_visible_as_an_external_node(
        self, tmp_path: Path
    ) -> None:
        # .gdshader is out of scope but is a real dependency, unlike art.
        ctx = _ctx({"actors/player.gd"}, repo_path=tmp_path)
        got = resolve_import("res://fx/wave.gdshader", "actors/player.gd", "gdscript", ctx)
        assert got == "external:res://fx/wave.gdshader"


# ---------------------------------------------------------------------------
# 2. Warmup: entry points and addons/ vendoring
# ---------------------------------------------------------------------------


def _warmup_ctx(tmp_path: Path, files: dict[str, str]) -> ResolverContext:
    """Write *files*, build a file-node graph, and return a wired context."""
    parsed = {}
    graph = nx.DiGraph()
    for rel, text in files.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        language = (
            "godot_resource"
            if rel.endswith((".tscn", ".tres", ".escn", "project.godot", "plugin.cfg"))
            else "gdscript"
        )
        fi = _file_info(rel, language, str(abs_path))
        parsed[rel] = SimpleNamespace(
            file_info=fi,
            imports=extract_godot_imports(text) if language == "godot_resource" else [],
        )
        graph.add_node(rel, node_type="file")
    return _ctx(set(files), repo_path=tmp_path, graph=graph, parsed_files=parsed)


class TestWarmupEntryPoints:
    def test_autoloads_and_main_scene_are_entry_points(self, tmp_path: Path) -> None:
        ctx = _warmup_ctx(
            tmp_path,
            {
                "project.godot": PROJECT_GODOT,
                "global/events.gd": "extends Node\n",
                "global/music_player.tscn": "[gd_scene format=3]\n",
                "scenes/run/run.tscn": "[gd_scene format=3]\n",
                "other/helper.gd": "extends Node\n",
            },
        )
        _warmup_godot(ctx)

        stamped = {n for n, d in ctx.graph.nodes(data=True) if d.get("is_entry_point")}
        assert stamped == {
            "global/events.gd",
            "global/music_player.tscn",
            "scenes/run/run.tscn",
        }

    def test_plugin_cfg_script_is_an_entry_point(self, tmp_path: Path) -> None:
        # A repo that *publishes* an addon has no project.godot at all; the
        # EditorPlugin is then its only declared entry point.
        ctx = _warmup_ctx(
            tmp_path,
            {
                "addons/dialogic/plugin.cfg": PLUGIN_CFG,
                "addons/dialogic/plugin.gd": "extends EditorPlugin\n",
            },
        )
        _warmup_godot(ctx)

        assert ctx.graph.nodes["addons/dialogic/plugin.gd"].get("is_entry_point")

    def test_no_manifest_is_a_no_op(self, tmp_path: Path) -> None:
        ctx = _warmup_ctx(tmp_path, {"a.gd": "extends Node\n"})
        _warmup_godot(ctx)
        assert not any(d.get("is_entry_point") for _, d in ctx.graph.nodes(data=True))

    def test_file_info_is_stamped_too(self, tmp_path: Path) -> None:
        # The graph attribute drives dead code; FileInfo drives the wiki's
        # entry-point list, the tour and page selection. Both, or a repo still
        # reports no entry point where a reader looks.
        ctx = _warmup_ctx(
            tmp_path,
            {
                "addons/dialogic/plugin.cfg": PLUGIN_CFG,
                "addons/dialogic/plugin.gd": "extends EditorPlugin\n",
            },
        )
        _warmup_godot(ctx)
        assert ctx.parsed_files["addons/dialogic/plugin.gd"].file_info.is_entry_point

    def test_tolerates_a_context_with_no_parsed_files(self, tmp_path: Path) -> None:
        ctx = _warmup_ctx(tmp_path, {"project.godot": PROJECT_GODOT})
        object.__setattr__(ctx, "parsed_files", None)
        _warmup_godot(ctx)  # must not raise


class TestAddonsVendoring:
    def test_addons_under_a_project_are_vendored(self, tmp_path: Path) -> None:
        # The consumer case: Pixelorama's shape. addons/ is a checked-in
        # node_modules, and its scripts are a third party's public API.
        ctx = _warmup_ctx(
            tmp_path,
            {
                "project.godot": "config_version=5\n",
                "addons/keychain/plugin.gd": "extends EditorPlugin\n",
                "src/Main.gd": "extends Node\n",
            },
        )
        _warmup_godot(ctx)

        assert ctx.graph.nodes["addons/keychain/plugin.gd"].get("is_never_flag")
        assert not ctx.graph.nodes["src/Main.gd"].get("is_never_flag")

    def test_addons_with_no_enclosing_project_are_first_party(self, tmp_path: Path) -> None:
        # The publisher case: dialogic's shape, where 97% of the source lives
        # under addons/ and blanket-vendoring would erase the whole repo.
        ctx = _warmup_ctx(
            tmp_path,
            {
                "addons/dialogic/plugin.cfg": PLUGIN_CFG,
                "addons/dialogic/plugin.gd": "extends EditorPlugin\n",
            },
        )
        _warmup_godot(ctx)

        assert not ctx.graph.nodes["addons/dialogic/plugin.gd"].get("is_never_flag")

    def test_a_project_in_a_sibling_directory_does_not_vendor(self, tmp_path: Path) -> None:
        # The rule is pure ancestry, not a "is this a CI fixture" judgement.
        # dialogic's only project.godot sits under .github/workflows/resources/,
        # which encloses nothing, so its own addons/ stays first-party.
        ctx = _warmup_ctx(
            tmp_path,
            {
                ".github/workflows/resources/project.godot": "config_version=5\n",
                "addons/dialogic/plugin.gd": "extends EditorPlugin\n",
            },
        )
        _warmup_godot(ctx)

        assert not ctx.graph.nodes["addons/dialogic/plugin.gd"].get("is_never_flag")

    def test_a_nested_project_vendors_only_its_own_addons(self, tmp_path: Path) -> None:
        # godot-gamejam keeps its project under godot/.
        ctx = _warmup_ctx(
            tmp_path,
            {
                "godot/project.godot": "config_version=5\n",
                "godot/addons/tool/tool.gd": "extends Node\n",
                "addons/unrelated/other.gd": "extends Node\n",
            },
        )
        _warmup_godot(ctx)

        assert ctx.graph.nodes["godot/addons/tool/tool.gd"].get("is_never_flag")
        assert not ctx.graph.nodes["addons/unrelated/other.gd"].get("is_never_flag")


# ---------------------------------------------------------------------------
# 3. Scene signal connections
# ---------------------------------------------------------------------------


def _connection_edges(tmp_path: Path, files: dict[str, str]) -> nx.DiGraph:
    """Build the graph a scene's ``[connection]`` blocks should wire up.

    Scripts get one symbol node per ``func``, the way the parser emits them,
    because ``add_symbol_edge`` refuses an end that is not already a symbol.
    """
    parsed = {}
    graph = nx.DiGraph()
    for rel, text in files.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        language = "gdscript" if rel.endswith(".gd") else "godot_resource"
        symbols = []
        if language == "gdscript":
            for line in text.splitlines():
                if line.startswith("func "):
                    name = line[5:].split("(", 1)[0].strip()
                    sym_id = f"{rel}::{name}"
                    symbols.append(
                        SimpleNamespace(id=sym_id, name=name, kind="function")
                    )
                    graph.add_node(sym_id, node_type="symbol")
        parsed[rel] = SimpleNamespace(
            file_info=_file_info(rel, language, str(abs_path)),
            imports=[],
            symbols=symbols,
        )
        graph.add_node(rel, node_type="file")
        graph.add_node(f"{rel}::__module__", node_type="symbol")
    ctx = _ctx(set(files), repo_path=tmp_path, graph=graph, parsed_files=parsed)
    add_framework_edges(graph, parsed, ctx)
    return graph


ROOT_SCRIPT_SCENE = """\
[gd_scene format=3]

[ext_resource type="Script" path="res://hud.gd" id="1"]

[node name="HUD" type="CanvasLayer"]
script = ExtResource("1")
[node name="StartButton" type="Button" parent="."]

[connection signal="pressed" from="StartButton" to="." method="_on_start_pressed"]
"""

CHILD_SCRIPT_SCENE = """\
[gd_scene format=3]

[ext_resource type="Script" path="res://hud.gd" id="1"]
[ext_resource type="Script" path="res://player.gd" id="2"]

[node name="HUD" type="CanvasLayer"]
script = ExtResource("1")
[node name="Player" type="Area2D" parent="."]
script = ExtResource("2")
[node name="Shape" type="CollisionShape2D" parent="Player"]

[connection signal="hit" from="Shape" to="Player" method="_on_hit"]
"""

INHERITED_SCRIPT_SCENE = """\
[gd_scene format=3]

[ext_resource type="Script" path="res://hud.gd" id="1"]

[node name="HUD" type="CanvasLayer"]
script = ExtResource("1")
[node name="Panel" type="Panel" parent="."]
[node name="Deep" type="Button" parent="Panel"]

[connection signal="pressed" from="Deep" to="Panel/Deep" method="_on_start_pressed"]
"""

INSTANCED_ROOT_WITH_SCRIPT_SCENE = """\
[gd_scene format=3]

[ext_resource type="PackedScene" path="res://chat.tscn" id="1"]
[ext_resource type="Script" path="res://hud.gd" id="2"]

[node name="Client" instance=ExtResource("1")]
script = ExtResource("2")

[connection signal="pressed" from="Btn" to="." method="_on_start_pressed"]
"""

INSTANCED_ROOT_SCENE = """\
[gd_scene format=3]

[ext_resource type="PackedScene" path="res://player.tscn" id="1"]

[node name="Player" instance=ExtResource("1")]

[connection signal="hit" from="." to="." method="_on_start_pressed"]
"""

HUD_GD = "extends CanvasLayer\n\nfunc _on_start_pressed() -> void:\n\tpass\n"
PLAYER_GD = "extends Area2D\n\nfunc _on_hit() -> void:\n\tpass\n"


class TestSceneParsing:
    def test_node_paths_resources_and_connections(self) -> None:
        resources, nodes, connections = godot_edges.parse_scene(CHILD_SCRIPT_SCENE)
        assert resources == {"1": "res://hud.gd", "2": "res://player.gd"}
        assert set(nodes) == {".", "Player", "Player/Shape"}
        assert nodes["."].script_id == "1"
        assert nodes["Player"].script_id == "2"
        assert nodes["Player/Shape"].script_id is None
        assert connections[0]["method"] == "_on_hit"

    def test_godot_3_unquoted_resource_ids(self) -> None:
        text = (
            '[ext_resource path="res://a.gd" type="Script" id=1]\n'
            '[node name="Root" type="Node"]\n'
            "script = ExtResource( 1 )\n"
        )
        resources, nodes, _ = godot_edges.parse_scene(text)
        assert resources == {"1": "res://a.gd"}
        assert nodes["."].script_id == "1"

    def test_the_walk_climbs_to_the_nearest_scripted_ancestor(self) -> None:
        _, nodes, _ = godot_edges.parse_scene(INHERITED_SCRIPT_SCENE)
        assert godot_edges.resolve_handler_node("Panel/Deep", nodes) == ("1", "ok")

    def test_a_missing_node_is_refused(self) -> None:
        _, nodes, _ = godot_edges.parse_scene(ROOT_SCRIPT_SCENE)
        assert godot_edges.resolve_handler_node("Ghost", nodes) == (
            None,
            "node_not_found",
        )

    def test_a_local_script_wins_over_an_instance(self) -> None:
        # An instanced root that overrides the instanced scene's script with
        # its own is the common shape for a scene that customises a reusable
        # one (networking/websocket_chat/client.tscn). The script is right
        # here in this file, so there is nothing to refuse.
        _, nodes, _ = godot_edges.parse_scene(INSTANCED_ROOT_WITH_SCRIPT_SCENE)
        assert nodes["."].instance_id == "1"
        assert godot_edges.resolve_handler_node(".", nodes) == ("2", "ok")

    def test_an_instanced_root_is_refused(self) -> None:
        _, nodes, _ = godot_edges.parse_scene(INSTANCED_ROOT_SCENE)
        assert godot_edges.resolve_handler_node(".", nodes) == (
            None,
            "instanced_scene",
        )

    def test_a_scene_with_no_script_anywhere_is_refused(self) -> None:
        text = '[node name="Root" type="Node"]\n[node name="Kid" parent="."]\n'
        _, nodes, _ = godot_edges.parse_scene(text)
        assert godot_edges.resolve_handler_node("Kid", nodes) == (None, "no_script")


class TestSceneConnectionEdges:
    def test_root_handler(self, tmp_path: Path) -> None:
        graph = _connection_edges(
            tmp_path, {"hud.tscn": ROOT_SCRIPT_SCENE, "hud.gd": HUD_GD}
        )
        assert graph.has_edge("hud.tscn::__module__", "hud.gd::_on_start_pressed")
        assert (
            graph["hud.tscn::__module__"]["hud.gd::_on_start_pressed"]["edge_type"]
            == "framework_binds"
        )

    def test_child_node_with_its_own_script(self, tmp_path: Path) -> None:
        graph = _connection_edges(
            tmp_path,
            {
                "hud.tscn": CHILD_SCRIPT_SCENE,
                "hud.gd": HUD_GD,
                "player.gd": PLAYER_GD,
            },
        )
        # The handler belongs to the node named by `to`, not to the scene root.
        assert graph.has_edge("hud.tscn::__module__", "player.gd::_on_hit")
        assert not graph.has_edge("hud.tscn::__module__", "hud.gd::_on_start_pressed")

    def test_child_without_a_script_inherits_the_root(self, tmp_path: Path) -> None:
        graph = _connection_edges(
            tmp_path, {"hud.tscn": INHERITED_SCRIPT_SCENE, "hud.gd": HUD_GD}
        )
        assert graph.has_edge("hud.tscn::__module__", "hud.gd::_on_start_pressed")

    def test_a_to_node_that_does_not_exist_gets_no_edge(self, tmp_path: Path) -> None:
        scene = ROOT_SCRIPT_SCENE.replace('to="."', 'to="Ghost"')
        graph = _connection_edges(tmp_path, {"hud.tscn": scene, "hud.gd": HUD_GD})
        assert not graph.has_edge("hud.tscn::__module__", "hud.gd::_on_start_pressed")

    def test_a_method_absent_from_the_script_gets_no_edge(self, tmp_path: Path) -> None:
        scene = ROOT_SCRIPT_SCENE.replace('method="_on_start_pressed"', 'method="_gone"')
        graph = _connection_edges(tmp_path, {"hud.tscn": scene, "hud.gd": HUD_GD})
        # Never matched by name alone: the script has a handler, just not this
        # one, and no other file's `_gone` may stand in for it.
        assert not any(
            t.endswith("::_gone") for _, t in graph.out_edges("hud.tscn::__module__")
        )

    def test_an_instanced_root_with_its_own_script_binds(self, tmp_path: Path) -> None:
        graph = _connection_edges(
            tmp_path,
            {"client.tscn": INSTANCED_ROOT_WITH_SCRIPT_SCENE, "hud.gd": HUD_GD},
        )
        assert graph.has_edge("client.tscn::__module__", "hud.gd::_on_start_pressed")

    def test_an_instanced_root_gets_no_edge(self, tmp_path: Path) -> None:
        graph = _connection_edges(
            tmp_path,
            {
                "main.tscn": INSTANCED_ROOT_SCENE,
                "player.tscn": ROOT_SCRIPT_SCENE,
                "hud.gd": HUD_GD,
            },
        )
        assert not graph.has_edge("main.tscn::__module__", "hud.gd::_on_start_pressed")

    def test_the_handler_needs_a_scene(self, tmp_path: Path) -> None:
        parsed = {
            "a.gd": SimpleNamespace(
                file_info=_file_info("a.gd", "gdscript"), imports=[], symbols=[]
            )
        }
        graph = nx.DiGraph()
        graph.add_node("a.gd", node_type="file")
        ctx = _ctx({"a.gd"}, repo_path=tmp_path, graph=graph, parsed_files=parsed)
        dctx = DetectionContext(
            stack_lower=set(), parsed_files=parsed, ctx=ctx, path_set={"a.gd"}
        )
        assert godot_edges.HANDLERS[1].detect(dctx) is False


# ---------------------------------------------------------------------------
# 4. class_name global-registry edges
# ---------------------------------------------------------------------------


def _class_name_edges(tmp_path: Path, files: dict[str, str]) -> nx.DiGraph:
    parsed = {}
    graph = nx.DiGraph()
    for rel, text in files.items():
        abs_path = tmp_path / rel
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        abs_path.write_text(text, encoding="utf-8")
        parsed[rel] = SimpleNamespace(
            file_info=_file_info(rel, "gdscript", str(abs_path)), imports=[], symbols=[]
        )
        graph.add_node(rel, node_type="file")
    ctx = _ctx(set(files), repo_path=tmp_path, graph=graph, parsed_files=parsed)
    add_framework_edges(graph, parsed, ctx)
    return graph


class TestClassNameEdges:
    def test_global_class_use_creates_a_dependency_edge(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path,
            {
                "custom_resources/effect.gd": "class_name Effect\nextends RefCounted\n",
                "cards/slash.gd": "extends Card\n\nfunc apply() -> void:\n\tvar e := Effect.new()\n",
            },
        )
        assert graph.has_edge("cards/slash.gd", "custom_resources/effect.gd")

    def test_extends_form_with_a_trailing_clause(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path,
            {
                "a.gd": "class_name DamageEffect extends Effect\n",
                "b.gd": "func f():\n\tDamageEffect.new()\n",
            },
        )
        assert graph.has_edge("b.gd", "a.gd")

    def test_an_unused_global_class_gets_no_edge(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path,
            {
                "a.gd": "class_name Unused\nextends Node\n",
                "b.gd": "extends Node\n\nfunc f() -> void:\n\tpass\n",
            },
        )
        assert not graph.has_edge("b.gd", "a.gd")

    def test_the_declaring_file_gets_no_self_edge(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path, {"a.gd": "class_name Effect\n\nfunc f() -> Effect:\n\treturn self\n"}
        )
        assert not graph.has_edge("a.gd", "a.gd")

    def test_an_inner_class_is_not_globally_registered(self, tmp_path: Path) -> None:
        # `class Inner:` is script-local; Godot registers only `class_name`.
        graph = _class_name_edges(
            tmp_path,
            {
                "a.gd": "extends Node\n\nclass Inner:\n\tvar x := 1\n",
                "b.gd": "func f():\n\tvar y = Inner.new()\n",
            },
        )
        assert not graph.has_edge("b.gd", "a.gd")

    def test_class_name_must_start_the_line(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path,
            {
                "a.gd": '\tvar s = "class_name Sneaky"\n',
                "b.gd": "func f():\n\tSneaky.new()\n",
            },
        )
        assert not graph.has_edge("b.gd", "a.gd")

    def test_the_handler_does_not_fire_without_gdscript(self, tmp_path: Path) -> None:
        # Asserting a 0 edge count would pass even if the handler ran, since
        # every handler returns 0 on a one-file Python repo. Assert on detect()
        # itself, which is the gate that actually matters.
        parsed = {
            "a.py": SimpleNamespace(
                file_info=_file_info("a.py", "python"), imports=[], symbols=[]
            )
        }
        graph = nx.DiGraph()
        graph.add_node("a.py", node_type="file")
        ctx = _ctx({"a.py"}, repo_path=tmp_path, graph=graph, parsed_files=parsed)
        dctx = DetectionContext(
            stack_lower=set(), parsed_files=parsed, ctx=ctx, path_set={"a.py"}
        )
        assert godot_edges.HANDLERS[0].detect(dctx) is False

    def test_two_projects_do_not_share_one_global_class_table(self, tmp_path: Path) -> None:
        # Godot's class_name table is per *project*, and one repo can hold
        # many -- godot-demo-projects is dozens side by side. Two projects
        # each declaring `class_name Player` must not wire to each other.
        for project in ("alpha", "beta"):
            (tmp_path / project).mkdir(parents=True, exist_ok=True)
            (tmp_path / project / "project.godot").write_text(
                "config_version=5\n", encoding="utf-8"
            )
        graph = _class_name_edges(
            tmp_path,
            {
                "alpha/player.gd": "class_name Player\nextends Node\n",
                "alpha/game.gd": "func f():\n\tPlayer.new()\n",
                "beta/player.gd": "class_name Player\nextends Node\n",
                "beta/game.gd": "func f():\n\tPlayer.new()\n",
            },
        )
        assert graph.has_edge("alpha/game.gd", "alpha/player.gd")
        assert graph.has_edge("beta/game.gd", "beta/player.gd")
        assert not graph.has_edge("alpha/game.gd", "beta/player.gd")
        assert not graph.has_edge("beta/game.gd", "alpha/player.gd")

    def test_a_bom_does_not_hide_a_line_one_declaration(self, tmp_path: Path) -> None:
        graph = _class_name_edges(
            tmp_path,
            {
                "a.gd": "﻿class_name Effect\nextends Node\n",
                "b.gd": "func f():\n\tEffect.new()\n",
            },
        )
        assert graph.has_edge("b.gd", "a.gd")


# ---------------------------------------------------------------------------
# 5. Godot engine callbacks are not dead code
# ---------------------------------------------------------------------------


class TestEngineCallbacks:
    def test_node_lifecycle_callbacks(self) -> None:
        for name in ("_ready", "_process", "_physics_process", "_enter_tree", "_exit_tree"):
            assert is_contract_method(name, "function", "gdscript"), name

    def test_input_and_drawing_callbacks(self) -> None:
        for name in ("_input", "_unhandled_input", "_gui_input", "_draw", "_notification"):
            assert is_contract_method(name, "function", "gdscript"), name

    def test_editor_plugin_interface(self) -> None:
        for name in ("_enable_plugin", "_get_plugin_name", "_handles", "_edit"):
            assert is_contract_method(name, "function", "gdscript"), name

    def test_a_project_helper_is_still_eligible(self) -> None:
        assert not is_contract_method("_recalculate_damage", "function", "gdscript")

    def test_scoped_to_gdscript(self) -> None:
        # `_process` is an ordinary private helper name in Python.
        assert not is_contract_method("_process", "function", "python")
        assert not is_contract_method("_ready", "method", "typescript")
