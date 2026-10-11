"""Unit tests for Swift intra-module type-reference edges."""

from __future__ import annotations

import networkx as nx

from repowise.core.ingestion.languages.swift_same_module import (
    resolve_swift_same_module_refs,
)


def _graph(paths: list[str]) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p, node_type="file")
    return g


class TestSwiftSameModule:
    def test_same_target_type_reference(self) -> None:
        texts = {
            "Sources/Core/Engine.swift": "public final class Engine {}\n",
            "Sources/Core/Runner.swift": (
                "struct Runner {\n  let engine = Engine()\n}\n"
            ),
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        assert added == 1
        edge = g["Sources/Core/Runner.swift"]["Sources/Core/Engine.swift"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "same_module"
        assert edge["imported_names"] == ["Engine"]

    def test_cross_target_requires_real_import(self) -> None:
        # Engine lives in Core; App files cannot see it without an import,
        # so the same-module pass must not invent an edge across targets.
        texts = {
            "Sources/Core/Engine.swift": "public class Engine {}\n",
            "Sources/App/Main.swift": "let engine = Engine()\n",
            "Sources/App/Helper.swift": "struct Helper {}\n",
        }
        g = _graph(list(texts))
        targets = {"Core": "Sources/Core", "App": "Sources/App"}
        added = resolve_swift_same_module_refs(g, targets, texts)
        assert added == 0

    def test_ambiguous_type_no_edge(self) -> None:
        texts = {
            "Sources/Core/A.swift": "struct Config {}\n",
            "Sources/Core/B.swift": "struct Config {}\n",
            "Sources/Core/C.swift": "let c = Config()\n",
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        assert added == 0

    def test_stdlib_name_skipped(self) -> None:
        texts = {
            "Sources/Core/Data.swift": "struct Data {}\n",
            "Sources/Core/Use.swift": "let d: Data? = nil\n",
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        assert added == 0

    def test_protocol_enum_actor_declarations_resolve(self) -> None:
        texts = {
            "Sources/Core/Shapes.swift": (
                "protocol Drawable {}\nenum Mode { case a }\nactor Store {}\n"
            ),
            "Sources/Core/Use.swift": (
                "struct Canvas: Drawable {\n"
                "  var mode: Mode = .a\n  let store = Store()\n}\n"
            ),
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        assert added == 1
        edge = g["Sources/Core/Use.swift"]["Sources/Core/Shapes.swift"]
        assert sorted(edge["imported_names"]) == ["Drawable", "Mode", "Store"]

    def test_no_spm_manifest_implicit_single_module(self) -> None:
        # Xcode-style repo: no Package.swift → all files form one module.
        texts = {
            "MyApp/Model.swift": "struct Model {}\n",
            "MyApp/ViewController.swift": "let m = Model()\n",
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {}, texts)
        assert added == 1
        assert g.has_edge("MyApp/ViewController.swift", "MyApp/Model.swift")

    def test_existing_edge_wins(self) -> None:
        texts = {
            "Sources/Core/Engine.swift": "class Engine {}\n",
            "Sources/Core/Use.swift": "let e = Engine()\n",
        }
        g = _graph(list(texts))
        g.add_edge(
            "Sources/Core/Use.swift",
            "Sources/Core/Engine.swift",
            edge_type="imports",
            imported_names=["Engine"],
        )
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        assert added == 0
        assert "hint_source" not in g["Sources/Core/Use.swift"]["Sources/Core/Engine.swift"]

    def test_extension_declares_nothing(self) -> None:
        # An extension of Engine in another file must not claim the name.
        texts = {
            "Sources/Core/Engine.swift": "class Engine {}\n",
            "Sources/Core/Engine+Extras.swift": "extension Engine {\n  func go() {}\n}\n",
            "Sources/Core/Use.swift": "let e = Engine()\n",
        }
        g = _graph(list(texts))
        added = resolve_swift_same_module_refs(g, {"Core": "Sources/Core"}, texts)
        # Use → Engine (unique declaration), and the extension file itself
        # references Engine too — also → Engine.
        assert added == 2
        assert g.has_edge("Sources/Core/Use.swift", "Sources/Core/Engine.swift")
        assert g.has_edge(
            "Sources/Core/Engine+Extras.swift", "Sources/Core/Engine.swift"
        )


class TestSwiftEntryWarmup:
    def test_main_attribute_flags_entry_point(self, tmp_path) -> None:
        from types import SimpleNamespace

        from repowise.core.ingestion.graph_warmups import _warmup_swift

        app = tmp_path / "App.swift"
        app.write_text("@main\nstruct MyApp {\n}\n")
        other = tmp_path / "Helper.swift"
        other.write_text("struct H {}\n")

        g = nx.DiGraph()
        g.add_node("App.swift", node_type="file")
        g.add_node("Helper.swift", node_type="file")
        ctx = SimpleNamespace(
            graph=g,
            parsed_files={
                "App.swift": SimpleNamespace(
                    file_info=SimpleNamespace(language="swift", abs_path=str(app))
                ),
                "Helper.swift": SimpleNamespace(
                    file_info=SimpleNamespace(language="swift", abs_path=str(other))
                ),
            },
        )
        _warmup_swift(ctx)
        assert g.nodes["App.swift"].get("is_entry_point") is True
        assert g.nodes["Helper.swift"].get("is_entry_point") is not True

    def test_uiapplicationmain_flags_entry_point(self, tmp_path) -> None:
        from types import SimpleNamespace

        from repowise.core.ingestion.graph_warmups import _warmup_swift

        app = tmp_path / "AppDelegate.swift"
        app.write_text("@UIApplicationMain\nclass AppDelegate: UIResponder {\n}\n")
        g = nx.DiGraph()
        g.add_node("AppDelegate.swift", node_type="file")
        ctx = SimpleNamespace(
            graph=g,
            parsed_files={
                "AppDelegate.swift": SimpleNamespace(
                    file_info=SimpleNamespace(language="swift", abs_path=str(app))
                ),
            },
        )
        _warmup_swift(ctx)
        assert g.nodes["AppDelegate.swift"].get("is_entry_point") is True
