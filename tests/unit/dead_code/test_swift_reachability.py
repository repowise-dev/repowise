"""Swift file reachability: a file is live when a live file uses, names or extends its types.

Shapes from IceCubesApp, where none of 30 judged "unreachable" files was dead:
a view constructed from another package (``TagRowView(tag:)``), a model named
only in a type annotation (``[StatusHistory]``), an ``extension Color`` whose
member is used, and an extension of a live in-repo type. Plus the controls
that keep the rescue from hiding dead code: two dead files naming each other,
a name declared twice or common to the platform, a name in a comment or a
string, and an unused extension of a platform type.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.swift_reachability import (
    build_swift_named_files,
    strip_comments_and_strings,
)
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_DS = "Packages/DesignSystem/Sources/DesignSystem"
_MODELS = "Packages/Models/Sources/Models"
_ACCOUNT = "Packages/Account/Sources/Account"
_ENTRY = f"{_ACCOUNT}/Tags/FollowedTagsListView.swift"


def _package(name: str) -> str:
    return (
        "// swift-tools-version:5.9\nimport PackageDescription\n\n"
        f'let package = Package(name: "{name}", targets: [.target(name: "{name}")])\n'
    )


_FILES = {
    "Packages/DesignSystem/Package.swift": _package("DesignSystem"),
    "Packages/Models/Package.swift": _package("Models"),
    "Packages/Account/Package.swift": _package("Account"),
    f"{_DS}/Views/TagRowView.swift": (
        "import SwiftUI\n\npublic struct TagRowView: View {\n"
        "  let tag: String\n  public init(tag: String) { self.tag = tag }\n"
        "  public var body: some View { Text(tag) }\n}\n"
    ),
    f"{_DS}/Resources/Colors.swift": (
        "import SwiftUI\n\nextension Color {\n  public static let brandTint = Color(.blue)\n}\n"
    ),
    f"{_MODELS}/StatusHistory.swift": (
        "import Foundation\n\npublic struct StatusHistory: Decodable {\n"
        "  public let content: String\n}\n\nextension StatusHistory: Sendable {}\n"
    ),
    f"{_MODELS}/Editor/EditorStore.swift": (
        "import Foundation\n\npublic enum StatusEditor {\n"
        "  public final class EditorStore {}\n}\n"
    ),
    f"{_MODELS}/Editor/EditorMode.swift": (
        "import Foundation\n\nextension StatusEditor.EditorStore {\n"
        "  public enum Mode { case new, edit }\n}\n"
    ),
    f"{_MODELS}/PartialAccountWithAvatar.swift": (
        "import Foundation\n\npublic struct PartialAccountWithAvatar: Codable {\n"
        "  public let id: String\n}\n"
    ),
    f"{_MODELS}/Legacy.swift": (
        "import Foundation\n\npublic struct LegacyThing {}\n\n"
        "extension LegacyThing {\n  func old() {}\n}\n"
    ),
    _ENTRY: (
        "import SwiftUI\nimport DesignSystem\nimport Models\n\n"
        "// PartialAccountWithAvatar is only mentioned here.\n"
        "public struct FollowedTagsListView: View {\n"
        "  @State private var history: [StatusHistory]?\n"
        "  let store = StatusEditor.EditorStore()\n"
        '  let label = "LegacyThing"\n'
        "  public var body: some View {\n"
        '    TagRowView(tag: "swift").foregroundColor(.brandTint)\n  }\n}\n'
    ),
}


def _report(root: Path):
    source_map = {rel: text.encode() for rel, text in _FILES.items()}
    for rel, source in source_map.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(source)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=root)
    for fi in FileTraverser(root).traverse():
        builder.add_file(parser.parse_file(fi, source_map[fi.path]))
    builder.set_source_map(source_map)
    graph = builder.build()
    # Stands in for the app's ``@main`` entry, which reaches the list view.
    graph.nodes[_ENTRY]["is_entry_point"] = True
    analyzer = DeadCodeAnalyzer(
        graph,
        git_meta_map={},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    )
    return analyzer.analyze({"min_confidence": 0.0})


def _unreachable(report) -> set[str]:
    return {
        Path(f.file_path).name
        for f in report.findings
        if f.kind is DeadCodeKind.UNREACHABLE_FILE
    }


def test_files_a_live_file_uses_names_or_extends_are_reachable(tmp_path: Path) -> None:
    unreachable = _unreachable(_report(tmp_path))
    assert unreachable.isdisjoint(
        {
            "TagRowView.swift",  # constructed from another package
            "StatusHistory.swift",  # named in a type annotation
            "Colors.swift",  # a member of its platform-type extension is used
            "EditorStore.swift",
            "EditorMode.swift",  # extends a type declared in a reachable file
        }
    ), unreachable


def test_a_type_only_a_comment_or_string_names_is_still_unreachable(tmp_path: Path) -> None:
    unreachable = _unreachable(_report(tmp_path))
    assert {"PartialAccountWithAvatar.swift", "Legacy.swift"} <= unreachable


def test_no_source_means_unchecked_and_reachable() -> None:
    graph = nx.DiGraph()
    graph.add_node("A.swift", node_type="file", language="swift")
    assert build_swift_named_files(graph, {}) == frozenset({"A.swift"})


def _graph(decls: dict[str, list[tuple[str, str, str | None]]], entry: str) -> nx.DiGraph:
    """Files declaring ``(name, kind, parent)`` symbols, no edge between them."""
    graph = nx.DiGraph()
    for path, symbols in decls.items():
        graph.add_node(path, node_type="file", language="swift", is_entry_point=path == entry)
        for name, kind, parent in symbols:
            sym = f"{path}::{parent + '.' if parent else ''}{name}"
            graph.add_node(
                sym, node_type="symbol", name=name, kind=kind, file_path=path, parent_name=parent
            )
            graph.add_edge(path, sym, edge_type="defines")
    return graph


_ROOT = "App/Main.swift"
_DECLS: dict[str, list[tuple[str, str, str | None]]] = {
    _ROOT: [("Main", "class", None)],
    "Models/StatusHistory.swift": [("StatusHistory", "class", None)],
    "Models/Store.swift": [("Store", "class", None)],
    "Models/Store+Mode.swift": [("Store", "impl", None), ("Mode", "class", "Store")],
    "Views/Colors.swift": [("Color", "impl", None), ("brandTint", "variable", "Color")],
    "Probe/String+Probe.swift": [("String", "impl", None), ("probeTrimmed", "method", "String")],
    "Probe/URL+Id.swift": [("URL", "impl", None), ("id", "variable", "URL")],
    "Probe/Account.swift": [("Account", "class", None), ("id", "variable", "Account")],
    "Probe/DeadA.swift": [("DeadA", "class", None)],
    "Probe/DeadB.swift": [("DeadB", "class", None)],
    "Probe/Text.swift": [("Text", "class", None)],
    "Probe/ItemA.swift": [("Item", "class", None)],
    "Probe/ItemB.swift": [("Item", "class", None)],
    "Probe/ItemView.swift": [("ItemView", "class", None)],
    "Probe/Commented.swift": [("CommentedType", "class", None)],
    "Probe/Quoted.swift": [("QuotedType", "class", None), ("RawQuotedType", "class", None)],
    "Probe/Bare.swift": [("BareOperand", "class", None)],
}
_SOURCE = {
    _ROOT: (
        b"struct Main {\n"
        b"  var h: [StatusHistory]\n  let s = Store()\n"
        b"  let c = Color.blue.opacity(1).brandTint\n"
        b"  let t = Text(\"x\")\n  let i = Item()\n  let ids = urls.map { $0.id }\n"
        b"  /* outer /* CommentedType */ still a comment: CommentedType */\n"
        b'  let q = """\n  QuotedType(\n  """\n'
        b'  let r = #"RawQuotedType( \\"#\n'
        b"  let n = count + BareOperand\n"
        b'  let v = "ItemView"  // ItemView\n'
        b"}\n"
    ),
    "Models/StatusHistory.swift": b"struct StatusHistory {}\n",
    "Models/Store.swift": b"final class Store {}\n",
    "Models/Store+Mode.swift": b"extension Store { enum Mode {} }\n",
    "Views/Colors.swift": b"extension Color { static let brandTint = Color.red }\n",
    "Probe/String+Probe.swift": b"extension String { func probeTrimmed() -> String { self } }\n",
    "Probe/URL+Id.swift": b"extension URL { var id: String { absoluteString } }\n",
    "Probe/Account.swift": b"struct Account { let id: String }\n",
    "Probe/DeadA.swift": b"struct DeadA { let b: DeadB }\n",
    "Probe/DeadB.swift": b"struct DeadB { let a: [DeadA] }\n",
    "Probe/Text.swift": b"struct Text {}\n",
    "Probe/ItemA.swift": b"struct Item {}\n",
    "Probe/ItemB.swift": b"struct Item {}\n",
    "Probe/ItemView.swift": b"struct ItemView { let item: Item }\n",
    "Probe/Commented.swift": b"struct CommentedType {}\n",
    "Probe/Quoted.swift": b"struct QuotedType {}\nstruct RawQuotedType {}\n",
    "Probe/Bare.swift": b"struct BareOperand {}\n",
}


def test_live_uses_in_type_and_member_positions_reach_a_file() -> None:
    named = build_swift_named_files(_graph(_DECLS, _ROOT), _SOURCE)
    assert {
        "Models/StatusHistory.swift",  # [StatusHistory]
        "Models/Store.swift",  # Store()
        "Models/Store+Mode.swift",  # extends a reached type
        "Views/Colors.swift",  # .brandTint, declared by no other file
    } <= named, named


def test_dead_code_the_text_cannot_vouch_for_stays_unreachable() -> None:
    named = build_swift_named_files(_graph(_DECLS, _ROOT), _SOURCE)
    assert not named & {
        "Probe/String+Probe.swift",  # an unused extension of a platform type
        "Probe/URL+Id.swift",  # its only member name is declared elsewhere too
        "Probe/DeadA.swift",  # two dead files naming each other
        "Probe/DeadB.swift",
        "Probe/Text.swift",  # a platform name
        "Probe/ItemA.swift",  # declared by two files
        "Probe/ItemB.swift",
        "Probe/ItemView.swift",  # only in a string and a comment
        "Probe/Commented.swift",  # inside a nested block comment
        "Probe/Quoted.swift",  # inside multi-line and raw strings
        "Probe/Bare.swift",  # an operand, not a type position
    }, named


def test_comment_and_string_stripping() -> None:
    text = 'a /* x /* y */ z */ b "s\\"t" c """\nm\n""" d #"r"q"# e // f\ng'
    assert strip_comments_and_strings(text).split() == ["a", "b", "c", "d", "e", "g"]
