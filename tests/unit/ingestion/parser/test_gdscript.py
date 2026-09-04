"""Unit tests for the GDScript (Godot) language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Covers
symbols (both GDScript 3 and GDScript 4 spellings), heritage in all three
positions it can appear, and imports from ``preload`` / ``load`` /
``extends "res://..."`` -- see docs/architecture/language-support.md's
"one .scm file + one LanguageConfig" recipe.

Import *resolution* is covered separately in
tests/unit/ingestion/test_gdscript_resolver.py, which needs no grammar.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# tree-sitter-gdscript is a pinned dependency, so an absent grammar is a broken
# environment and must fail the suite rather than quietly skip it.
import tree_sitter_gdscript  # noqa: F401

from repowise.core.ingestion.parser import ASTParser
from tests.unit.ingestion.parser._helpers import _make_file_info


def _gd(path: str = "actors/player.gd") -> object:
    return _make_file_info(path, "gdscript")


# GDScript 4 spelling: `@export`/`@onready` annotations on a plain `var`.
PLAYER_SOURCE = b'''\
extends "res://actors/base_actor.gd"
class_name Player

signal health_changed(old_value, new_value)

const MAX_HEALTH := 100
const Bullet = preload("res://weapons/bullet.gd")

enum State { IDLE, RUNNING, JUMPING }

@export var speed: float = 300.0
@onready var sprite = $Sprite2D
var health := MAX_HEALTH

class Inventory extends Resource:
\tvar slots := []

\tfunc add_item(item):
\t\tslots.append(item)

func _init():
\thealth = MAX_HEALTH

func _ready():
\tvar greeting = "hello"
\tprint(greeting)
\ttake_damage(0)

func take_damage(amount: int) -> void:
\thealth -= amount
\themit_signal("health_changed", health + amount, health)

static func clamp_health(value: int) -> int:
\treturn clamp(value, 0, MAX_HEALTH)
'''


# GDScript 3 spelling: bare `export`/`onready` keywords, `.method()` super
# call, and `class_name X extends Y` folded onto one line.
LEGACY_SOURCE = b'''\
class_name Enemy extends KinematicBody2D

export var damage = 10
onready var timer = get_node("Timer")

func _ready():
\t.ready()
\tspawn()

func spawn():
\tpass
'''


class TestGDScriptSymbols:
    def test_script_level_class_name(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert ("Player", "class") in {(s.name, s.kind) for s in result.symbols}

    def test_inner_class(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert ("Inventory", "class") in {(s.name, s.kind) for s in result.symbols}

    def test_functions_including_static(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"_ready", "take_damage", "clamp_health"} <= names

    def test_constructor_is_captured_despite_having_no_name_field(
        self, parser: ASTParser
    ) -> None:
        # `func _init()` parses as `constructor_definition`, which carries no
        # `name` field at all -- the name is an anonymous "_init" token. If
        # the .scm ever stops capturing anonymous nodes this is the canary.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert "_init" in {s.name for s in result.symbols}

    def test_constants_variables_signals_and_enums(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        kinds = {(s.name, s.kind) for s in result.symbols}
        assert ("MAX_HEALTH", "constant") in kinds
        assert ("Bullet", "constant") in kinds
        assert ("State", "enum") in kinds
        # Enumerators are real constants in the enclosing scope.
        assert ("IDLE", "constant") in kinds
        assert ("health", "variable") in kinds
        # No "signal" member in the SymbolKind literal -- signals share the
        # "variable" bucket with Pascal properties and C# events.
        assert ("health_changed", "variable") in kinds

    def test_anonymous_enum_members_are_still_symbols(self, parser: ASTParser) -> None:
        # `enum {A, B}` has no enum symbol of its own, but its members are
        # the idiomatic Godot spelling for state/flag constants.
        result = parser.parse_file(_gd(), b"enum { IDLE, RUNNING }\n")
        assert {"IDLE", "RUNNING"} <= {s.name for s in result.symbols}

    def test_annotated_export_and_onready_vars(self, parser: ASTParser) -> None:
        # GDScript 4 `@export var` is an ordinary variable_statement carrying
        # an `annotations` child, not a distinct node type.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"speed", "sprite"} <= names

    def test_gdscript_3_export_and_onready_keywords(self, parser: ASTParser) -> None:
        # The bare-keyword forms ARE distinct node types
        # (export_variable_statement / onready_variable_statement).
        result = parser.parse_file(_gd("actors/enemy.gd"), LEGACY_SOURCE)
        names = {s.name for s in result.symbols}
        assert {"damage", "timer"} <= names

    def test_function_local_variables_are_not_top_level_symbols(
        self, parser: ASTParser
    ) -> None:
        # `var greeting` lives inside _ready's body. A `var` is a
        # variable_statement wherever it appears, so without the source /
        # class_body anchor in the .scm every loop counter would surface.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert "greeting" not in {s.name for s in result.symbols}

    def test_inner_class_members_attribute_to_the_inner_class(
        self, parser: ASTParser
    ) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        add_item = next(s for s in result.symbols if s.name == "add_item")
        assert add_item.parent_name == "Inventory"

    def test_script_level_functions_have_no_parent(self, parser: ASTParser) -> None:
        # `class_name Player` is a *sibling* of the script's functions, not
        # their ancestor, so nesting-based parent extraction finds nothing --
        # which is the honest answer: they belong to the file.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        take_damage = next(s for s in result.symbols if s.name == "take_damage")
        assert take_damage.parent_name is None

    def test_underscore_prefixed_names_read_as_private(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        ready = next(s for s in result.symbols if s.name == "_ready")
        take_damage = next(s for s in result.symbols if s.name == "take_damage")
        assert ready.visibility == "private"
        assert take_damage.visibility == "public"


class TestGDScriptHeritage:
    # A project type, not an engine one: engine roots like Node2D are in the
    # spec's builtin_parents and are filtered out before heritage is returned
    # (asserted separately below), which would mask the ordering behaviour.
    def test_standalone_extends_above_class_name(self, parser: ASTParser) -> None:
        # `extends X` on its own line is a SIBLING of class_name_statement,
        # not a child -- the extractor has to walk the file's top level to
        # find it. This ordering is the common one in real Godot code.
        result = parser.parse_file(_gd("actors/thing.gd"), b"extends BaseThing\nclass_name Thing\n")
        rels = {(r.child_name, r.parent_name, r.kind) for r in result.heritage}
        assert ("Thing", "BaseThing", "extends") in rels

    def test_extends_below_class_name(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd("actors/thing.gd"), b"class_name Thing\nextends BaseThing\n")
        rels = {(r.child_name, r.parent_name, r.kind) for r in result.heritage}
        assert ("Thing", "BaseThing", "extends") in rels

    def test_engine_root_parents_are_filtered_as_builtins(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd("actors/thing.gd"), b"extends Node2D\nclass_name Thing\n")
        assert result.heritage == []

    def test_inline_class_name_extends(self, parser: ASTParser) -> None:
        # `class_name Enemy extends KinematicBody2D` -- here the
        # extends_statement IS a child, via class_name_statement's field.
        result = parser.parse_file(_gd("actors/enemy.gd"), LEGACY_SOURCE)
        rels = {(r.child_name, r.parent_name, r.kind) for r in result.heritage}
        assert ("Enemy", "KinematicBody2D", "extends") in rels

    def test_inner_class_engine_parent_is_filtered(self, parser: ASTParser) -> None:
        # Named for what it actually checks: `Resource` is in builtin_parents,
        # so the relation is filtered rather than left dangling. The positive
        # inner-class case is the next test.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        rels = {(r.child_name, r.parent_name) for r in result.heritage}
        assert ("Inventory", "Resource") not in rels

    def test_inner_class_extends_written_inside_the_body(self, parser: ASTParser) -> None:
        # The grammar admits `extends` as a statement in the class body, not
        # only in the `class Inner extends X:` header.
        src = b"class_name Outer\n\nclass Inner:\n\textends SomeProjectType\n\tpass\n"
        result = parser.parse_file(_gd("a.gd"), src)
        rels = {(r.child_name, r.parent_name, r.kind) for r in result.heritage}
        assert ("Inner", "SomeProjectType", "extends") in rels

    def test_qualified_parent_keeps_its_last_segment(self, parser: ASTParser) -> None:
        # `extends Inventory.BaseSlot` must record `BaseSlot`; HeritageResolver
        # matches bare symbol names, so the dotted form would never resolve.
        src = b"class_name Slot\nextends Inventory.BaseSlot\n"
        result = parser.parse_file(_gd("a.gd"), src)
        assert {r.parent_name for r in result.heritage} == {"BaseSlot"}

    def test_inner_class_heritage_on_a_project_type_survives(
        self, parser: ASTParser
    ) -> None:
        src = b"class_name Outer\n\nclass Inner extends SomeProjectType:\n\tpass\n"
        result = parser.parse_file(_gd("a.gd"), src)
        rels = {(r.child_name, r.parent_name, r.kind) for r in result.heritage}
        assert ("Inner", "SomeProjectType", "extends") in rels

    def test_res_path_extends_is_an_import_not_a_heritage_relation(
        self, parser: ASTParser
    ) -> None:
        # A res:// path is not a type NAME, and HeritageResolver only ever
        # matches parent_name against symbol names -- emitting one would
        # create a relation that can never resolve. The dependency is
        # carried by the import edge instead.
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert all("res://" not in r.parent_name for r in result.heritage)
        assert "res://actors/base_actor.gd" in {i.module_path for i in result.imports}

    def test_script_without_class_name_yields_no_heritage(self, parser: ASTParser) -> None:
        # Documented ceiling: an anonymous script has no class symbol, so
        # there is no child name to hang a symbol-level edge on.
        result = parser.parse_file(_gd("a.gd"), b"extends Node2D\n\nfunc _ready():\n\tpass\n")
        assert result.heritage == []


class TestGDScriptImports:
    def test_preload_load_and_extends_paths(self, parser: ASTParser) -> None:
        src = (
            b'extends "res://actors/base_actor.gd"\n'
            b'const Bullet = preload("res://weapons/bullet.gd")\n'
            b'var menu = load("res://ui/menu.tscn")\n'
        )
        result = parser.parse_file(_gd(), src)
        assert {i.module_path for i in result.imports} == {
            "res://actors/base_actor.gd",
            "res://weapons/bullet.gd",
            "res://ui/menu.tscn",
        }

    def test_quotes_are_stripped_before_the_resolver_sees_the_path(
        self, parser: ASTParser
    ) -> None:
        result = parser.parse_file(_gd(), b'const B = preload("res://b.gd")\n')
        assert result.imports[0].module_path == "res://b.gd"

    def test_non_literal_load_argument_is_not_an_import(self, parser: ASTParser) -> None:
        # `load(path_var)` needs dataflow to resolve; emitting the variable
        # name as a module path would manufacture a wrong edge.
        result = parser.parse_file(_gd(), b'var path = "res://x.gd"\nvar r = load(path)\n')
        assert result.imports == []

    def test_resourceloader_load_is_an_import(self, parser: ASTParser) -> None:
        # Parses as attribute -> attribute_call, never as a bare `call`, so
        # it needs its own pattern. The standard runtime-fetch idiom.
        result = parser.parse_file(_gd(), b'var s = ResourceLoader.load("res://a.gd")\n')
        assert {i.module_path for i in result.imports} == {"res://a.gd"}

    def test_type_hint_argument_is_not_mistaken_for_a_path(self, parser: ASTParser) -> None:
        # Real case from dialogic: ResourceLoader.load(style, "DialogicStyle")
        # passes the path as a variable and a TYPE HINT as the second
        # argument. Only the first argument may be read as a module path.
        result = parser.parse_file(
            _gd(), b'func f(style):\n\treturn ResourceLoader.load(style, "DialogicStyle")\n'
        )
        assert result.imports == []

    def test_load_as_a_method_name_still_makes_a_call_edge(self, parser: ASTParser) -> None:
        # The builtin filter is receiver-blind, so listing `load` in
        # builtin_calls would silently delete this edge.
        result = parser.parse_file(_gd(), b"func f():\n\tsave_manager.load(0)\n")
        assert ("load", "save_manager") in {
            (c.target_name, c.receiver_name) for c in result.calls
        }

    def test_preload_is_also_a_constant_symbol(self, parser: ASTParser) -> None:
        # `const X = preload(...)` is legitimately both a constant and an
        # import; the two captures are independent.
        result = parser.parse_file(_gd(), b'const Bullet = preload("res://weapons/bullet.gd")\n')
        assert ("Bullet", "constant") in {(s.name, s.kind) for s in result.symbols}
        assert len(result.imports) == 1


class TestGDScriptCalls:
    def test_project_calls_are_recorded(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        assert "take_damage" in {c.target_name for c in result.calls}

    def test_godot_globals_are_excluded_as_builtins(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        targets = {c.target_name for c in result.calls}
        assert "print" not in targets
        assert "clamp" not in targets
        # preload is an import, and must not double as a call node.
        assert "preload" not in targets

    def test_gdscript_3_parent_call_is_recorded(self, parser: ASTParser) -> None:
        # `.ready()` in LEGACY_SOURCE -- the base_call pattern. Previously
        # advertised in the fixture header but asserted nowhere.
        result = parser.parse_file(_gd("actors/enemy.gd"), LEGACY_SOURCE)
        assert "ready" in {c.target_name for c in result.calls}

    def test_super_method_call_records_super_as_receiver(self, parser: ASTParser) -> None:
        src = b"func _ready():\n\tsuper._ready()\n\tsuper()\n"
        result = parser.parse_file(_gd("a.gd"), src)
        calls = {(c.target_name, c.receiver_name) for c in result.calls}
        assert ("_ready", "super") in calls
        # Bare `super()` can never resolve to a project symbol, so it is
        # filtered rather than left as a dangling call target.
        assert "super" not in {c.target_name for c in result.calls}

    def test_method_call_records_its_receiver(self, parser: ASTParser) -> None:
        result = parser.parse_file(_gd(), PLAYER_SOURCE)
        append = next((c for c in result.calls if c.target_name == "append"), None)
        assert append is not None
        assert append.receiver_name == "slots"


class TestKnownGrammarGap:
    """Sentinel for the one upstream gap, so a grammar bump tells us it is fixed.

    tree-sitter-gdscript still reserves `export` / `onready` at statement
    position for the GDScript 3 `export var` / `onready var` forms. GDScript 4
    respells those `@export` / `@onready`, so both are ordinary identifiers
    again and `export()` is a legal call -- but a *bare* call statement fails
    to parse. Hit once in 651 corpus files (Pixelorama ExportDialog.gd:469).
    """

    @pytest.mark.parametrize("word", ["export", "onready"])
    def test_bare_call_statement_still_fails(self, parser: ASTParser, word: str) -> None:
        src = f"func f():\n\t{word}()\n".encode()
        result = parser.parse_file(_gd("a.gd"), src)
        assert result.parse_errors, (
            f"`{word}()` now parses -- the upstream grammar gap looks fixed. "
            "Drop this test and the matching bullet in LANGUAGE_SUPPORT.md."
        )

    @pytest.mark.parametrize(
        "src",
        [
            b"func export() -> void:\n\tpass\n",
            b"func f():\n\tself.export()\n",
            b"func f():\n\tvar x = export()\n",
        ],
    )
    def test_only_the_bare_statement_form_is_affected(
        self, parser: ASTParser, src: bytes
    ) -> None:
        assert parser.parse_file(_gd("a.gd"), src).parse_errors == []


class TestGDScriptParsesCleanly:
    @pytest.mark.parametrize("source", [PLAYER_SOURCE, LEGACY_SOURCE])
    def test_no_parse_errors(self, parser: ASTParser, source: bytes) -> None:
        # The corpus gate is "no parse errors"; this is its unit-level
        # sentinel over both dialects.
        result = parser.parse_file(_gd(), source)
        assert result.parse_errors == []


def _build_graph(repo: Path):
    """Parse every file under *repo* and return the built graph."""
    from repowise.core.ingestion import FileTraverser, GraphBuilder

    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _call_targets(graph, source_file: str) -> set[str]:
    return {
        target
        for src, target, data in graph.edges(data=True)
        if data.get("edge_type") == "calls" and src.startswith(f"{source_file}::")
    }


class TestEngineMethodsAreNotGuessedAt:
    """The bare-name tier must not answer an engine call with a project symbol.

    That tier binds a name declared exactly once anywhere in the repo, at 0.50
    confidence. Every implicit-`self` engine call is a bare name, so without
    ``builtin_methods`` a script's `load(...)` or `queue_free()` lands on
    whatever lone function shares the spelling.
    """

    def test_bare_load_does_not_bind_to_a_project_function_named_load(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "save_manager.gd").write_bytes(
            b"extends Node\nclass_name SaveManager\n\n"
            b"func load(path):\n\treturn path\n"
        )
        (tmp_path / "player.gd").write_bytes(
            b"extends Node\n\nfunc respawn():\n\tload(\"res://x.tscn\")\n"
        )
        graph = _build_graph(tmp_path)
        assert "save_manager.gd::load" in graph
        assert "save_manager.gd::load" not in _call_targets(graph, "player.gd")

    @pytest.mark.parametrize("name", ["queue_free", "emit_signal", "get_parent"])
    def test_other_engine_names_are_refused_too(self, tmp_path: Path, name: str) -> None:
        (tmp_path / "helper.gd").write_bytes(
            f"extends Node\n\nfunc {name}():\n\tpass\n".encode()
        )
        (tmp_path / "caller.gd").write_bytes(
            f"extends Node\n\nfunc act():\n\t{name}()\n".encode()
        )
        graph = _build_graph(tmp_path)
        assert f"helper.gd::{name}" not in _call_targets(graph, "caller.gd")

    def test_a_unique_project_function_still_resolves(self, tmp_path: Path) -> None:
        # The control: the tier is a guess, not a wrong one by construction,
        # and a name the engine does not own must still bind.
        (tmp_path / "damage.gd").write_bytes(
            b"extends Node\n\nfunc apply_falloff_damage(amount):\n\treturn amount\n"
        )
        (tmp_path / "caller.gd").write_bytes(
            b"extends Node\n\nfunc hit():\n\tapply_falloff_damage(5)\n"
        )
        graph = _build_graph(tmp_path)
        assert "damage.gd::apply_falloff_damage" in _call_targets(graph, "caller.gd")
