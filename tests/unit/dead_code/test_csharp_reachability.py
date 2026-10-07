"""C# file reachability: a file is live when a project that can see it names a type it declares.

C# leaves no file-level edge for ``new T()``, ``throw new T()``, a same-namespace
reference, an extension-method call or a use inside a generated
``*.Designer.cs``. Each test below is one of those shapes, plus the controls
that keep the rescue from hiding a file nothing uses.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.analysis.dead_code.csharp_reachability import (
    build_csharp_named_files,
    build_csharp_named_types,
)
from repowise.core.analysis.dead_code.file_reachability import (
    ReachabilityRescues,
    is_file_reachable,
)
from repowise.core.ingestion.resolvers.dotnet.index import build_index
from repowise.core.ingestion.resolvers.dotnet.msbuild import parse_csproj


def _csproj(refs: tuple[str, ...] = (), compiles: tuple[str, ...] = ()) -> str:
    items = "".join(f'<ProjectReference Include="{r}" />' for r in refs)
    items += "".join(f'<Compile Include="{c}" />' for c in compiles)
    return f'<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>{items}</ItemGroup></Project>'


class _Repo:
    """A tiny C# repository on disk plus the graph ingestion would build for it."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.graph = nx.DiGraph()
        self.source_map: dict[str, bytes] = {}

    def project(self, rel: str, **kwargs: tuple[str, ...]) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_csproj(**kwargs))

    def file(self, rel: str, text: str, *, types=(), extensions=(), indexed=True) -> None:
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        if not indexed:  # a skipped generated file: on disk, not in the graph
            return
        self.source_map[rel] = text.encode()
        self.graph.add_node(rel, node_type="file", language="csharp")
        for name in types:
            self._symbol(rel, name, kind="class", signature=f"class {name}")
        for name in extensions:
            self._symbol(rel, name, kind="method", signature=f"{name}(this int value) -> int")

    def _symbol(self, rel: str, name: str, **attrs: str) -> None:
        sym = f"{rel}::{name}"
        self.graph.add_node(
            sym, node_type="symbol", name=name, file_path=rel, language="csharp", **attrs
        )
        self.graph.add_edge(rel, sym, edge_type="defines")

    def named(self) -> frozenset[str]:
        return build_csharp_named_files(
            self.graph, self.source_map, dotnet_index=build_index(self.root), repo_root=self.root
        )


def _two_projects(tmp_path: Path, *, referenced: bool) -> _Repo:
    repo = _Repo(tmp_path)
    repo.project("Lib/Lib.csproj")
    repo.project("App/App.csproj", refs=(r"..\Lib\Lib.csproj",) if referenced else ())
    repo.file("Lib/Widget.cs", "namespace Lib;\npublic class Widget {}\n", types=("Widget",))
    return repo


def test_new_in_a_referencing_project_names_the_type(tmp_path: Path) -> None:
    repo = _two_projects(tmp_path, referenced=True)
    repo.file(
        "App/Main.cs", "using Lib;\nclass Main { object w = new Widget(); }\n", types=("Main",)
    )
    assert "Lib/Widget.cs" in repo.named()


def test_a_project_that_cannot_see_the_type_does_not_name_it(tmp_path: Path) -> None:
    """Same name, no ProjectReference: that is some other ``Widget``."""
    repo = _two_projects(tmp_path, referenced=False)
    repo.file("App/Main.cs", "class Main { object w = new Widget(); }\n", types=("Main",))
    assert "Lib/Widget.cs" not in repo.named()


def test_throw_in_the_same_project_names_the_exception(tmp_path: Path) -> None:
    """The PowerToys shape: the ``using`` edge went to a sibling file of the namespace."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/Exceptions/NotPhysicalConsoleException.cs",
        "namespace App.Exceptions;\nclass NotPhysicalConsoleException : System.Exception {}\n",
        types=("NotPhysicalConsoleException",),
    )
    repo.file(
        "App/SocketStuff.cs",
        "using App.Exceptions;\nclass SocketStuff { void F() { throw new NotPhysicalConsoleException(); } }\n",
        types=("SocketStuff",),
    )
    assert "App/Exceptions/NotPhysicalConsoleException.cs" in repo.named()


def test_a_comment_mention_is_not_a_use(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/EventRaiser.cs", "class EventRaiser {}\n", types=("EventRaiser",))
    repo.file(
        "App/Other.cs",
        '/// <see cref="EventRaiser"/>\nclass Other { /* EventRaiser */ } // EventRaiser\n',
        types=("Other",),
    )
    assert "App/EventRaiser.cs" not in repo.named()


def test_a_string_mention_counts(tmp_path: Path) -> None:
    """``Type.GetType("App.Plugin")`` loads the type by name."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Plugin.cs", "class Plugin {}\n", types=("Plugin",))
    repo.file(
        "App/Loader.cs",
        'class Loader { object t = System.Type.GetType("App.Plugin"); }\n',
        types=("Loader",),
    )
    assert "App/Plugin.cs" in repo.named()


def test_a_declaration_of_the_same_name_is_not_a_use(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/A/Helper.cs", "namespace A; class Helper {}\n", types=("Helper",))
    repo.file("App/B/Helper.cs", "namespace B; class Helper {}\n", types=("Helper",))
    assert repo.named() == frozenset()


def test_extension_method_call_names_its_holder(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/SizeExtensions.cs",
        "static class SizeExtensions { public static int Fit(this int v) => v; }\n",
        types=("SizeExtensions",),
        extensions=("Fit",),
    )
    repo.file(
        "App/MainWindow.cs",
        "class MainWindow { int F(int s) => s.Fit(); }\n",
        types=("MainWindow",),
    )
    assert "App/SizeExtensions.cs" in repo.named()


def test_extension_name_without_member_access_is_not_a_call(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/SizeExtensions.cs",
        "static class SizeExtensions { public static int Fit(this int v) => v; }\n",
        types=("SizeExtensions",),
        extensions=("Fit",),
    )
    repo.file("App/MainWindow.cs", "class MainWindow { int Fit = 1; }\n", types=("MainWindow",))
    assert "App/SizeExtensions.cs" not in repo.named()


def test_a_skipped_designer_file_names_a_control(tmp_path: Path) -> None:
    """WinForms controls are only ever written in generated ``*.Designer.cs``."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/ImageButton.cs", "class ImageButton {}\n", types=("ImageButton",))
    repo.file("App/Form1.cs", "partial class Form1 {}\n", types=("Form1",))
    repo.file(
        "App/Form1.Designer.cs",
        "partial class Form1 { void Init() { var b = new ImageButton(); } }\n",
        indexed=False,
    )
    assert "App/ImageButton.cs" in repo.named()


def test_a_files_own_designer_half_is_not_a_user(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Form1.cs", "partial class Form1 {}\n", types=("Form1",))
    repo.file("App/Form1.Designer.cs", "partial class Form1 { Form1 self; }\n", indexed=False)
    assert "App/Form1.cs" not in repo.named()


def test_a_file_linked_into_another_project_is_seen_from_it(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("Shared/Shared.csproj")
    repo.project("App/App.csproj", compiles=(r"..\Shared\Strings.cs",))
    repo.file("Shared/Strings.cs", "static class Strings {}\n", types=("Strings",))
    repo.file("App/Main.cs", "class Main { object s = Strings.Empty; }\n", types=("Main",))
    assert "Shared/Strings.cs" in repo.named()


def test_assembly_level_file_is_never_dead(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Properties/AssemblyInfo.cs", '[assembly: InternalsVisibleTo("App.Tests")]\n')
    repo.file("App/GlobalSuppressions.cs", '[assembly: SuppressMessage("Style", "IDE0001")]\n')
    assert repo.named() == {"App/Properties/AssemblyInfo.cs", "App/GlobalSuppressions.cs"}


def test_a_suppression_above_a_class_does_not_keep_it(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/MyKnownBitmap.cs",
        '[module: SuppressMessage("Design", "CA1031")]\nclass MyKnownBitmap {}\n',
        types=("MyKnownBitmap",),
    )
    assert repo.named() == frozenset()


def test_a_delegate_is_a_declared_type(tmp_path: Path) -> None:
    """The parser emits no symbol for a delegate, so the source is read for it."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/GalleryFeedUrlProvider.cs", "public delegate string? GalleryFeedUrlProvider();\n"
    )
    repo.file("App/Service.cs", "class Service { GalleryFeedUrlProvider p; }\n", types=("Service",))
    assert "App/GalleryFeedUrlProvider.cs" in repo.named()


def test_no_source_and_no_index_is_unchecked() -> None:
    g = nx.DiGraph()
    g.add_node("src/Lonely.cs", node_type="file", language="csharp")
    assert build_csharp_named_files(g, {}) == {"src/Lonely.cs"}
    assert is_file_reachable("src/Lonely.cs", g, ReachabilityRescues()) is True


def test_analyzer_reports_only_the_unnamed_file(tmp_path: Path) -> None:
    """End to end: the named file is spared, the unnamed one is still reported."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Used.cs", "class Used {}\n", types=("Used",))
    repo.file("App/Unused.cs", "class Unused {}\n", types=("Unused",))
    repo.file("App/Program.cs", "class Program { object u = new Used(); }\n", types=("Program",))
    analyzer = DeadCodeAnalyzer(
        repo.graph,
        source_map=repo.source_map,
        repo_root=tmp_path,
        dotnet_index=build_index(tmp_path),
    )
    flagged = {f.file_path for f in analyzer._detect_unreachable_files(set())}
    assert "App/Unused.cs" in flagged
    assert "App/Used.cs" not in flagged


def test_csproj_records_compile_includes(tmp_path: Path) -> None:
    path = tmp_path / "App.csproj"
    path.write_text(_csproj(compiles=(r"$(CommonPath)System\Text\X.cs;..\Y.cs", r"**\*.cs")))
    project = parse_csproj(path)
    assert project is not None
    assert project.compile_includes == ["$(CommonPath)System/Text/X.cs", "../Y.cs"]


def test_an_attribute_is_named_without_its_suffix(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/NotNullWhenAttribute.cs",
        "class NotNullWhenAttribute {}\n",
        types=("NotNullWhenAttribute",),
    )
    repo.file(
        "App/Use.cs",
        "class Use { bool F([NotNullWhen(true)] object o) => true; }\n",
        types=("Use",),
    )
    assert "App/NotNullWhenAttribute.cs" in repo.named()


def test_a_wrapped_extension_signature_is_still_an_extension(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/FallbackExtensions.cs",
        "static class FallbackExtensions {}\n",
        types=("FallbackExtensions",),
    )
    sym = "App/FallbackExtensions.cs::AddFallback"
    repo.graph.add_node(
        sym,
        node_type="symbol",
        name="AddFallback",
        kind="method",
        signature="AddFallback(\r\n        this Builder builder) -> Builder",
    )
    repo.graph.add_edge("App/FallbackExtensions.cs", sym, edge_type="defines")
    repo.file(
        "App/Main.cs", "class Main { void F(Builder b) => b.AddFallback(); }\n", types=("Main",)
    )
    assert "App/FallbackExtensions.cs" in repo.named()


# ---- the same question per type, for unused exports ----------------------


def _named_types(repo: _Repo, wanted: dict[str, set[str]]) -> frozenset[tuple[str, str]]:
    return build_csharp_named_types(
        repo.graph,
        repo.source_map,
        wanted,
        dotnet_index=build_index(repo.root),
        repo_root=repo.root,
    )


def test_only_the_named_type_of_a_file_is_named(tmp_path: Path) -> None:
    """Per type, not per file: naming ``Used`` says nothing about ``Unused``."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Pair.cs", "class Used {}\nclass Unused {}\n", types=("Used", "Unused"))
    repo.file("App/Main.cs", "class Main { object u = new Used(); }\n", types=("Main",))
    assert _named_types(repo, {"App/Pair.cs": {"Used", "Unused"}}) == {("App/Pair.cs", "Used")}


def test_a_type_is_not_named_from_a_project_that_cannot_see_it(tmp_path: Path) -> None:
    repo = _two_projects(tmp_path, referenced=False)
    repo.file("App/Main.cs", "class Main { object w = new Widget(); }\n", types=("Main",))
    assert _named_types(repo, {"Lib/Widget.cs": {"Widget"}}) == frozenset()


def test_an_extension_call_names_its_holder_type(tmp_path: Path) -> None:
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/SizeExtensions.cs",
        "static class SizeExtensions { public static int Fit(this int v) => v; }\n",
        types=("SizeExtensions",),
    )
    repo.graph.add_node(
        "App/SizeExtensions.cs::SizeExtensions::Fit",
        node_type="symbol",
        name="Fit",
        kind="method",
        parent_name="SizeExtensions",
        signature="Fit(this int v) -> int",
    )
    repo.graph.add_edge(
        "App/SizeExtensions.cs", "App/SizeExtensions.cs::SizeExtensions::Fit", edge_type="defines"
    )
    repo.file("App/Main.cs", "class Main { int F(int s) => s.Fit(); }\n", types=("Main",))
    wanted = {"App/SizeExtensions.cs": {"SizeExtensions"}}
    assert _named_types(repo, wanted) == {("App/SizeExtensions.cs", "SizeExtensions")}


def test_analyzer_drops_only_the_unnamed_export(tmp_path: Path) -> None:
    """End to end: a type named by its project is no unused export; the other still is."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file("App/Types.cs", "public class Used {}\npublic class Unused {}\n")
    for name in ("Used", "Unused"):
        repo._symbol("App/Types.cs", name, kind="class", visibility="public")
    repo.file("App/Program.cs", "class Program { object u = new Used(); }\n", types=("Program",))
    report = DeadCodeAnalyzer(
        repo.graph,
        source_map=repo.source_map,
        repo_root=tmp_path,
        dotnet_index=build_index(tmp_path),
    ).analyze({"min_confidence": 0.0})
    exports = {f.symbol_name for f in report.findings if f.kind.value == "unused_export"}
    assert "Unused" in exports
    assert "Used" not in exports


def test_an_export_used_only_in_its_own_file_is_demoted_not_dropped(tmp_path: Path) -> None:
    """A request type its endpoint takes is used; at most it need not be public."""
    repo = _Repo(tmp_path)
    repo.project("App/App.csproj")
    repo.file(
        "App/Create.cs",
        "public class CreateRequest {}\npublic class Create { void F(CreateRequest r) {} }\n",
    )
    repo._symbol(
        "App/Create.cs", "CreateRequest", kind="class", visibility="public",
        start_line=1, end_line=1,
    )
    report = DeadCodeAnalyzer(
        repo.graph,
        source_map=repo.source_map,
        repo_root=tmp_path,
        dotnet_index=build_index(tmp_path),
    ).analyze({"min_confidence": 0.0})
    (finding,) = [f for f in report.findings if f.symbol_name == "CreateRequest"]
    assert finding.confidence < 0.4
    assert "Used in its own file at App/Create.cs:2" in finding.evidence[-1]
