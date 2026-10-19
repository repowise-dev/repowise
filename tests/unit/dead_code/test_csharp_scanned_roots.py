"""C# classes a plugin host or container finds by type are not dead.

Shapes from jellyfin: ``ApplicationHost.GetExports<IScheduledTask>()``
instantiates every loaded type assignable to the interface, so no file names
``DeleteLogFileTask``. Its metadata providers implement a generic interface
(``IRemoteMetadataProvider<Episode, EpisodeInfo>``) whose name also has generic
and plain declarations, which the heritage resolver leaves unresolved. Plus the
Scrutor, reflection-loop and MEF spellings, and the controls that keep the
rescue from hiding a class nothing discovers.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.csharp_reachability import (
    _scanned_type_names,
    _simple_type_name,
)
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_FILES = {
    "Model/IScheduledTask.cs": "namespace Model;\npublic interface IScheduledTask { void Run(); }\n",
    "Model/IMetadataProvider.cs": (
        "namespace Model;\n"
        "public interface IMetadataProvider { string Name { get; } }\n"
        "public interface IMetadataProvider<TItem> : IMetadataProvider {}\n"
    ),
    "Model/IRemoteMetadataProvider.cs": (
        "namespace Model;\n"
        "public interface IRemoteMetadataProvider : IMetadataProvider {}\n"
        "public interface IRemoteMetadataProvider<TItem, TInfo> : IMetadataProvider<TItem>, "
        "IRemoteMetadataProvider {}\n"
    ),
    "Model/IUnscanned.cs": "namespace Model;\npublic interface IUnscanned { void Go(); }\n",
    "Host/ApplicationHost.cs": (
        "namespace Host;\nusing Model;\n"
        "public class ApplicationHost {\n"
        "    public void Start() {\n"
        "        var tasks = GetExports<IScheduledTask>(false);\n"
        "        var providers = GetExports<Model.IMetadataProvider>();\n"
        "    }\n"
        "    public T[] GetExports<T>(bool manage = true) => new T[0];\n"
        "}\n"
    ),
    "Tasks/DeleteLogFileTask.cs": (
        "namespace Tasks;\nusing Model;\n"
        "public class DeleteLogFileTask : IScheduledTask { public void Run() {} }\n"
    ),
    "Tasks/BaseTask.cs": (
        "namespace Tasks;\nusing Model;\n"
        "public abstract class BaseTask : IScheduledTask { public abstract void Run(); }\n"
    ),
    "Tasks/OptimizeTask.cs": (
        "namespace Tasks;\npublic class OptimizeTask : BaseTask { public override void Run() {} }\n"
    ),
    "Providers/TmdbEpisodeProvider.cs": (
        "namespace Providers;\nusing Model;\n"
        "public class TmdbEpisodeProvider : IRemoteMetadataProvider<Episode, EpisodeInfo>\n"
        "{\n    public string Name => \"Tmdb\";\n}\n"
    ),
    "Plugins/Scan.cs": (
        "namespace Plugins;\n"
        "public interface IHandler {}\npublic interface IPlugin {}\n"
        "public static class Scan {\n"
        "    public static void Wire(object services, System.Type[] types) {\n"
        "        services.Scan(s => s.AddClasses(c => c.AssignableTo<IHandler>()));\n"
        "        foreach (var t in types) { if (typeof(IPlugin).IsAssignableFrom(t)) {} }\n"
        "    }\n"
        "}\n"
    ),
    "Plugins/OrderHandler.cs": "namespace Plugins;\npublic class OrderHandler : IHandler {}\n",
    "Plugins/AudioPlugin.cs": "namespace Plugins;\npublic class AudioPlugin : IPlugin {}\n",
    "Plugins/ExportedPart.cs": (
        "namespace Plugins;\n[System.ComponentModel.Composition.Export(typeof(object))]\n"
        "public class ExportedPart {}\n"
    ),
    "Unused/OrphanTask.cs": (
        "namespace Unused;\nusing Model;\npublic class OrphanTask : IUnscanned { public void Go() {} }\n"
    ),
    # Same simple name as the discovered task, implementing nothing.
    "Other/DeleteLogFileTask.cs": "namespace Other;\npublic class DeleteLogFileTask {}\n",
    # Scans over framework types say nothing about plugins.
    "Host/Cleanup.cs": (
        "namespace Host;\npublic static class Cleanup {\n"
        "    public static void Run(System.Type t, object c) {\n"
        "        if (typeof(IDisposable).IsAssignableFrom(t)) {}\n"
        "        var all = c.GetExports<System.IComparable>();\n"
        "    }\n}\n"
    ),
    "Unused/DisposableThing.cs": (
        "namespace Unused;\npublic class DisposableThing : IDisposable { public void Dispose() {} }\n"
    ),
    "Unused/ComparableThing.cs": (
        "namespace Unused;\npublic class ComparableThing : IComparable "
        "{ public int CompareTo(object o) => 0; }\n"
    ),
    # A test scanning its host, and an NUnit assertion, load nothing.
    "Model/ITestOnly.cs": "namespace Model;\npublic interface ITestOnly {}\npublic interface INunit {}\n",
    "tests/HostTests.cs": (
        "namespace Tests;\nusing Model;\npublic class HostTests {\n"
        "    public void Scan(Host.ApplicationHost h, object x) {\n"
        "        h.GetExports<ITestOnly>();\n"
        "    }\n}\n"
    ),
    "Plugins/Assertions.cs": (
        "namespace Plugins;\nusing Model;\npublic static class Assertions {\n"
        "    public static void Check(object x) { Assert.That(x, Is.AssignableTo<INunit>()); }\n}\n"
    ),
    "Unused/TestOnlyImpl.cs": "namespace Unused;\nusing Model;\npublic class TestOnlyImpl : ITestOnly {}\n",
    "Unused/NunitImpl.cs": "namespace Unused;\nusing Model;\npublic class NunitImpl : INunit {}\n",
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
    analyzer = DeadCodeAnalyzer(
        graph,
        git_meta_map={},
        parsed_files=builder._parsed_files,
        source_map=source_map,
        repo_root=root,
    )
    return analyzer.analyze({"min_confidence": 0.0})


def _flagged(report) -> set[str]:
    """Paths with an unused-export or unreachable-file finding."""
    return {
        f.file_path
        for f in report.findings
        if f.kind in (DeadCodeKind.UNUSED_EXPORT, DeadCodeKind.UNREACHABLE_FILE)
    }


def test_classes_the_runtime_discovers_by_type_are_not_flagged(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    discovered = {
        "Tasks/DeleteLogFileTask.cs",  # GetExports<IScheduledTask>
        "Tasks/OptimizeTask.cs",  # through an abstract base
        "Providers/TmdbEpisodeProvider.cs",  # through an unresolved generic interface chain
        "Plugins/OrderHandler.cs",  # Scrutor AssignableTo<IHandler>()
        "Plugins/AudioPlugin.cs",  # typeof(IPlugin).IsAssignableFrom
        "Plugins/ExportedPart.cs",  # MEF [Export]
    }
    assert flagged.isdisjoint(discovered), flagged & discovered


def test_classes_no_production_scan_reaches_are_still_flagged(tmp_path: Path) -> None:
    flagged = _flagged(_report(tmp_path))
    assert {
        "Unused/OrphanTask.cs",  # IUnscanned is never scanned
        "Other/DeleteLogFileTask.cs",  # shares only a simple name with a discovered class
        "Unused/DisposableThing.cs",  # a scan over IDisposable (the parser drops BCL bases too)
        "Unused/ComparableThing.cs",  # a scan over a System.-qualified type
        "Unused/TestOnlyImpl.cs",  # scanned only by a test
        "Unused/NunitImpl.cs",  # Is.AssignableTo<> is an assertion
    } <= flagged, flagged


def test_generic_arguments_and_namespaces_are_dropped_from_a_base_name() -> None:
    blob = b"var p = host.GetExports<Model.IRemoteMetadataProvider<Episode, EpisodeInfo>>();\n"
    assert _scanned_type_names(nx.DiGraph(), {"Host.cs": blob}) == {"IRemoteMetadataProvider"}
    assert _simple_type_name("Model.IFoo<A, B>") == "IFoo"


def test_scans_over_framework_types_and_assertions_name_nothing() -> None:
    blob = (
        b"if (typeof(IDisposable).IsAssignableFrom(t)) {}\n"
        b"var a = host.GetExports<System.Collections.IList>();\n"
        b"Assert.That(x, Is.AssignableTo<IPlugin>());\n"
        b"x.Should().BeAssignableTo<IPlugin>();\n"
    )
    assert _scanned_type_names(nx.DiGraph(), {"Host.cs": blob}) == set()
