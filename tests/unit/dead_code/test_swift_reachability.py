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
        "import Foundation\n\npublic enum StatusEditor {\n  public final class EditorStore {}\n}\n"
    ),
    f"{_MODELS}/Editor/EditorMode.swift": (
        "import Foundation\n\nextension StatusEditor.EditorStore {\n"
        "  public enum Mode { case new, edit }\n}\n"
    ),
    f"{_MODELS}/PartialAccountWithAvatar.swift": (
        "import Foundation\n\npublic struct PartialAccountWithAvatar: Codable {\n"
        "  public let id: String\n}\n"
    ),
    f"{_DS}/Views/DismissGame.swift": (
        "import SwiftUI\n\nextension DependencyValues {\n  public var dismissGame: Int { 0 }\n}\n"
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
        "  @Dependency(\\.dismissGame) var dismissGame\n"
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
        Path(f.file_path).name for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE
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
            "DismissGame.swift",  # `.dismissGame` is also declared in the using file
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
    _ROOT: [
        ("Main", "class", None),
        ("label", "method", "Main"),
        ("subscript", "method", "Main"),
    ],
    "Models/StatusHistory.swift": [("StatusHistory", "class", None)],
    "Models/Store.swift": [("Store", "class", None)],
    "Models/Store+Mode.swift": [("Store", "impl", None), ("Mode", "class", "Store")],
    "Views/Colors.swift": [("Color", "impl", None), ("brandTint", "variable", "Color")],
    "Probe/String+Probe.swift": [("String", "impl", None), ("probeTrimmed", "method", "String")],
    "Probe/URL+Id.swift": [("URL", "impl", None), ("id", "variable", "URL")],
    "Probe/Account.swift": [("Account", "class", None), ("id", "variable", "Account")],
    "Probe/Observer+Store.swift": [("Observer", "impl", None), ("store", "method", "Observer")],
    "Probe/Canceller+Store.swift": [("Canceller", "impl", None), ("store", "method", "Canceller")],
    "Probe/Locale+Label.swift": [("Locale", "impl", None), ("label", "method", "Locale")],
    "Probe/Grid+Cell.swift": [("Grid", "impl", None), ("subscript", "method", "Grid")],
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
        b'  let t = Text("x")\n  let i = Item()\n  let ids = urls.map { $0.id }\n'
        b"  /* outer /* CommentedType */ still a comment: CommentedType */\n"
        b'  let q = """\n  QuotedType(\n  """\n'
        b'  let r = #"RawQuotedType( \\"#\n'
        b"  let n = count + BareOperand\n"
        b"  func label() {}\n  func run() { token.store(in: &bag) }\n"
        b"  subscript(k: Int) -> Int { k }\n"
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
    "Probe/Observer+Store.swift": b"extension Observer { func store(in b: Int) {} }\n",
    "Probe/Canceller+Store.swift": b"extension Canceller { func store(in b: Int) {} }\n",
    "Probe/Locale+Label.swift": b"extension Locale { func label() -> Int { 0 } }\n",
    "Probe/Grid+Cell.swift": b"extension Grid { subscript(i: Int) -> Int { i } }\n",
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
        "Views/Colors.swift",  # .brandTint
        "Probe/URL+Id.swift",  # `$0.id`, though a repo struct has an `id` too
        "Probe/Observer+Store.swift",  # one `.store(in:)` reaches both extensions
        "Probe/Canceller+Store.swift",
    } <= named, named


def test_dead_code_the_text_cannot_vouch_for_stays_unreachable() -> None:
    named = build_swift_named_files(_graph(_DECLS, _ROOT), _SOURCE)
    assert not named & {
        "Probe/String+Probe.swift",  # an unused extension of a platform type
        "Probe/Locale+Label.swift",  # the root only declares `label`, never uses it
        "Probe/Grid+Cell.swift",  # a subscript is declared, never written by name
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


# --- top-level functions used as values, and custom operators (#3857) -------


def _spm(*targets: tuple[str, tuple[str, ...]]) -> str:
    decls = ", ".join(
        f'.target(name: "{name}", dependencies: [{", ".join(chr(34) + d + chr(34) for d in deps)}])'
        for name, deps in targets
    )
    return (
        "// swift-tools-version:5.9\nimport PackageDescription\n\n"
        f'let package = Package(name: "P", targets: [{decls}])\n'
    )


_POINT_FREE = {
    "Package.swift": _spm(("SharedModels", ()), ("SiteMiddleware", ("SharedModels",))),
    "Sources/SharedModels/Score.swift": (
        "public func score(_ word: String) -> Int { word.count * 10 }\n"
    ),
    "Sources/SharedModels/Verification.swift": (
        "public struct Verification {\n  public let total: Int\n"
        "  public init(words: [String]) { self.total = words.map { score($0) }.reduce(0, +) }\n}\n"
    ),
    "Sources/SiteMiddleware/Operators.swift": (
        "infix operator |>: AdditionPrecedence\n"
        "public func |> <A, B>(value: A, transform: (A) -> B) -> B { transform(value) }\n"
    ),
    "Sources/SiteMiddleware/SubmitGame.swift": (
        "import SharedModels\n\n"
        "public func submitGameMiddleware(_ words: [String]) -> Int {\n"
        "  Verification(words: words).total\n}\n"
    ),
    "Sources/SiteMiddleware/Api.swift": (
        "public func apiMiddleware(_ words: [String]) -> Int { words |> submitGameMiddleware }\n"
    ),
    "Sources/Server/main.swift": 'import SiteMiddleware\nprint(apiMiddleware(["cat", "dog"]))\n',
}
_POINT_FREE_ENTRY = "Sources/Server/main.swift"


def _analyze(root: Path, files: dict[str, str], entry: str):
    source_map = {rel: text.encode() for rel, text in files.items()}
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
    graph.nodes[entry]["is_entry_point"] = True
    analyzer = DeadCodeAnalyzer(
        graph,
        git_meta_map={},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    )
    return analyzer.analyze({"min_confidence": 0.0})


def test_function_used_as_a_value_and_a_used_operator_are_reachable(tmp_path: Path) -> None:
    report = _analyze(tmp_path, _POINT_FREE, _POINT_FREE_ENTRY)
    assert _unreachable(report) == set(), _unreachable(report)


def test_a_direct_call_leaves_an_unused_operator_unreachable(tmp_path: Path) -> None:
    files = dict(_POINT_FREE)
    files["Sources/SiteMiddleware/Api.swift"] = (
        "public func apiMiddleware(_ words: [String]) -> Int { submitGameMiddleware(words) }\n"
    )
    report = _analyze(tmp_path, files, _POINT_FREE_ENTRY)
    assert _unreachable(report) == {"Operators.swift"}, _unreachable(report)


def _top_level_graph(
    decls: dict[str, list[tuple[str, str, str | None]]], sources: dict[str, bytes]
) -> frozenset[str]:
    return build_swift_named_files(_graph(decls, _ROOT), sources)


def test_top_level_names_the_text_cannot_vouch_for_stay_unreachable() -> None:
    decls = {
        _ROOT: [("Main", "class", None)],
        "Lib/Dup1.swift": [("helper", "function", None)],
        "Lib/Dup2.swift": [("helper", "function", None)],
        "Lib/Print.swift": [("print", "function", None)],
        "Lib/Unused.swift": [("neverNamed", "function", None)],
        "Lib/Label.swift": [("scoreLabel", "function", None)],
        "Lib/Member.swift": [("memberOnly", "function", None)],
        "Lib/Unop.swift": [("unusedOp", "function", None)],
    }
    sources = {
        _ROOT: (
            b"struct Main {\n  let a = helper(1)\n  let b = print(2)\n"
            b"  let c = f(scoreLabel: 3)\n  let d = thing.memberOnly(4)\n  let e = x.memberOnly\n}\n"
        ),
        "Lib/Dup1.swift": b"func helper(_ x: Int) {}\n",
        "Lib/Dup2.swift": b"func helper(_ x: Int, _ y: Int) {}\n",
        "Lib/Print.swift": b"func print(_ x: Int) {}\n",
        "Lib/Unused.swift": b"func neverNamed() {}\n",
        "Lib/Label.swift": b"func scoreLabel() {}\n",
        "Lib/Member.swift": b"func memberOnly() {}\n",
        "Lib/Unop.swift": b"infix operator <+>: AdditionPrecedence\nfunc <+> (a: Int, b: Int) -> Int { a }\n",
    }
    assert not _top_level_graph(decls, sources), _top_level_graph(decls, sources)


def test_a_top_level_function_sharing_a_method_name_is_still_reached_by_a_bare_use() -> None:
    decls = {
        _ROOT: [("Main", "class", None)],
        "Shared/Score.swift": [("score", "function", None)],
        "Shared/Game.swift": [
            ("InProgressGame", "class", None),
            ("score", "method", "InProgressGame"),
        ],
    }
    sources = {
        _ROOT: b"struct Main {\n  let t = words.map { score($0) }\n}\n",
        "Shared/Score.swift": b"public func score(_ w: String) -> Int { 1 }\n",
        "Shared/Game.swift": b"struct InProgressGame { func score(forPlayerIndex i: Int) -> Int { 0 } }\n",
    }
    assert "Shared/Score.swift" in _top_level_graph(decls, sources)


def test_top_level_let_passed_as_a_value_and_value_positions() -> None:
    decls = {
        _ROOT: [("Main", "class", None)],
        "Lib/Config.swift": [("defaultConfig", "variable", None)],
        "Lib/Handler.swift": [("handler", "function", None)],
        "Lib/Ret.swift": [("makeIt", "function", None)],
    }
    sources = {
        _ROOT: (
            b"struct Main {\n  let c = defaultConfig\n  let h = [1].map(handler)\n"
            b"  func f() -> Int { return makeIt }\n}\n"
        ),
        "Lib/Config.swift": b"let defaultConfig = 1\n",
        "Lib/Handler.swift": b"func handler(_ x: Int) -> Int { x }\n",
        "Lib/Ret.swift": b"func makeIt() -> Int { 1 }\n",
    }
    assert _top_level_graph(decls, sources) == {
        "Lib/Config.swift",
        "Lib/Handler.swift",
        "Lib/Ret.swift",
    }


def test_an_operator_overload_of_a_standard_operator_does_not_rescue_its_file() -> None:
    decls = {_ROOT: [("Main", "class", None)], "Lib/Plus.swift": [("Money", "class", None)]}
    sources = {
        _ROOT: b"struct Main {\n  let t = 1 + 2\n}\n",
        "Lib/Plus.swift": b"struct Money {\n  static func + (a: Money, b: Money) -> Money { a }\n}\n",
    }
    assert not _top_level_graph(decls, sources)


def test_locals_of_a_top_level_closure_and_playground_names_do_not_rescue(tmp_path: Path) -> None:
    files = {
        "Package.swift": _spm(("App", ())),
        "Sources/App/RuntimeWarnings.swift": (
            "private let rw = (\n"
            "  dso: { () -> Int in\n"
            "    let count = 3\n"
            '    let swiftString = "x"\n'
            "    return count + swiftString.count\n"
            "  }(),\n"
            "  log: 1\n"
            ")\n\n"
            "func runtimeWarning() {}\n"
        ),
        "Sources/App/Use.swift": (
            "public func use(_ items: [Int], router: Int) -> Int {\n"
            "  let n = items.count\n  let k = count\n  return router + n + k\n}\n"
        ),
        "Sources/App/main.swift": "print(use([1], router: 2))\n",
    }
    report = _analyze(tmp_path, files, "Sources/App/main.swift")
    assert "RuntimeWarnings.swift" in _unreachable(report), _unreachable(report)


def test_a_playground_file_is_not_rescued_by_a_name_it_declares() -> None:
    playground = "Sources/Router.playground/Contents.swift"
    decls = {
        _ROOT: [("Main", "class", None)],
        playground: [("router", "variable", None)],
        "Lib/Real.swift": [("router", "variable", None), ("Other", "class", None)],
    }
    sources = {
        _ROOT: b"struct Main {\n  let r = router\n}\n",
        playground: b"let router = 1\n",
        "Lib/Real.swift": b"let router = 2\nstruct Other {}\n",
    }
    named = _top_level_graph(decls, sources)
    assert playground not in named
    assert "Lib/Real.swift" in named


def test_a_custom_operator_starting_with_a_dot_is_read_whole() -> None:
    decls = {_ROOT: [("Main", "class", None)], "Lib/Dot.swift": [("Marker", "class", None)]}
    sources = {
        _ROOT: b"struct Main {\n  let t = 1 .+. 2\n  let u = a.b\n}\n",
        "Lib/Dot.swift": (
            b"infix operator .+.: AdditionPrecedence\n"
            b"func .+. (a: Int, b: Int) -> Int { a }\nstruct Marker {}\n"
        ),
    }
    assert "Lib/Dot.swift" in _top_level_graph(decls, sources)


def test_a_member_dot_is_not_an_operator() -> None:
    decls = {_ROOT: [("Main", "class", None)], "Lib/Dot.swift": [("Marker", "class", None)]}
    sources = {
        _ROOT: b"struct Main {\n  let u = a.b + c.d\n}\n",
        "Lib/Dot.swift": (
            b"infix operator .+.: AdditionPrecedence\n"
            b"func .+. (a: Int, b: Int) -> Int { a }\nstruct Marker {}\n"
        ),
    }
    assert "Lib/Dot.swift" not in _top_level_graph(decls, sources)


def test_declaring_a_method_does_not_rescue_a_top_level_function_of_that_name() -> None:
    decls = {
        _ROOT: [("Main", "class", None), ("helper", "method", "Main")],
        "Lib/Helper.swift": [("helper", "function", None)],
    }
    sources = {
        _ROOT: b"struct Main {\n  func helper() {}\n  public static func helper(_ x: Int) {}\n}\n",
        "Lib/Helper.swift": b"func helper() {}\n",
    }
    assert "Lib/Helper.swift" not in _top_level_graph(decls, sources)


def test_a_dead_let_with_a_common_value_name_is_not_rescued_by_a_local_of_that_name() -> None:
    decls = {
        _ROOT: [("Main", "class", None)],
        "Lib/Config.swift": [("config", "variable", None)],
        "Lib/Result.swift": [("result", "variable", None)],
        "Lib/Shared.swift": [("shared", "variable", None)],
    }
    sources = {
        _ROOT: (
            b"struct Main {\n  func run() -> Int {\n    let config = 1\n"
            b"    let result = config\n    return result + shared\n  }\n}\n"
        ),
        "Lib/Config.swift": b"let config = Config()\n",
        "Lib/Result.swift": b"let result = 0\n",
        "Lib/Shared.swift": b"let shared = Store()\n",
    }
    assert not _top_level_graph(decls, sources), _top_level_graph(decls, sources)


def test_a_common_value_name_still_rescues_a_func_and_an_uncommon_let_still_rescues() -> None:
    decls = {
        _ROOT: [("Main", "class", None)],
        "Lib/Config.swift": [("config", "function", None)],
        "Lib/Gate.swift": [("submitGateFlag", "variable", None)],
    }
    sources = {
        _ROOT: b"struct Main {\n  let a = config()\n  let b = submitGateFlag\n}\n",
        "Lib/Config.swift": b"func config() -> Int { 1 }\n",
        "Lib/Gate.swift": b"let submitGateFlag = true\n",
    }
    assert _top_level_graph(decls, sources) == {"Lib/Config.swift", "Lib/Gate.swift"}


def test_real_source_dead_let_config_stays_unreachable(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.analysis.dead_code import swift_reachability as swift_mod

    dead_path = "Sources/SiteMiddleware/Settings.swift"
    dead = Path(dead_path).name
    files = dict(_POINT_FREE)
    files[dead_path] = "let config = Config()\n"
    files["Sources/SiteMiddleware/Api.swift"] = (
        "public func apiMiddleware(_ words: [String]) -> Int {\n"
        "  let config = words.count\n"
        "  return config\n"
        "}\n"
    )
    # Control: without the guard, the live file's local ``config`` rescues the dead
    # ``let config`` file. If this fails the fixture no longer reproduces the bug.
    with monkeypatch.context() as patch:
        patch.setattr(swift_mod, "_SWIFT_COMMON_VALUE_NAMES", frozenset())
        control = _analyze(tmp_path / "control", files, _POINT_FREE_ENTRY)
    assert dead not in _unreachable(control), _unreachable(control)
    # With the guard, the dead file stays unreachable.
    report = _analyze(tmp_path / "guarded", files, _POINT_FREE_ENTRY)
    assert dead in _unreachable(report), _unreachable(report)
