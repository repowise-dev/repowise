"""Apex dead code: entry points, case-folded name uses and metadata references.

Apex has no import statement, so a class is reached by name: from a live Apex
file, from Lightning/Visualforce/flow metadata, or by the platform itself
(triggers, entry annotations, ``global``, platform interfaces). Controls keep
the rescue from hiding dead code: a class only an access grant or a dead class
names stays dead.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from types import SimpleNamespace

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.file_reachability import (
    ReachabilityRescues,
    is_file_reachable,
)
from repowise.core.code_origin import code_origin, declares_test
from repowise.core.ingestion.models import HeritageRelation
from repowise.core.test_paths import paired_test_names

_CLASSES = "force-app/main/default/classes"


def _file(
    graph: nx.DiGraph, path: str, symbols: list[dict], *, is_test: bool = False
) -> None:
    graph.add_node(path, node_type="file", language="apex", is_test=is_test, symbol_count=1)
    for sym in symbols:
        sym_id = f"{path}::{sym['name']}" if not sym.get("parent_name") else (
            f"{path}::{sym['parent_name']}::{sym['name']}"
        )
        graph.add_node(
            sym_id,
            node_type="symbol",
            file_path=path,
            language="apex",
            visibility=sym.pop("visibility", "public"),
            **sym,
        )
        graph.add_edge(path, sym_id, edge_type="defines")


def _class(name: str, start: int = 1, end: int = 20, **extra) -> dict:
    return {"name": name, "kind": "class", "start_line": start, "end_line": end, **extra}


def _parsed(heritage: list[HeritageRelation] | None = None) -> SimpleNamespace:
    return SimpleNamespace(heritage=heritage or [])


def _analyze(
    graph: nx.DiGraph,
    sources: dict[str, str],
    repo_root: Path | None = None,
    heritage: dict[str, list[HeritageRelation]] | None = None,
    *,
    unindexed: list[tuple[str, str]] | None = None,
):
    # An empty checkout unless the test supplies one: metadata must be readable to judge.
    repo_root = repo_root or Path(tempfile.mkdtemp())
    parsed = {
        path: _parsed((heritage or {}).get(path))
        for path, data in graph.nodes(data=True)
        if data.get("node_type") == "file"
    }
    return DeadCodeAnalyzer(
        graph,
        git_meta_map={},
        parsed_files=parsed,
        source_map={k: v.encode() for k, v in sources.items()},
        repo_root=repo_root,
        unindexed_source_files=unindexed,
    ).analyze({"detect_zombie_packages": False, "min_confidence": 0.0})


def _dead_files(report) -> set[str]:
    return {f.file_path for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE}


def _flagged(report) -> set[tuple[str, str | None]]:
    return {(f.kind.value, f.symbol_name) for f in report.findings}


def _service_graph() -> tuple[nx.DiGraph, dict[str, str]]:
    graph = nx.DiGraph()
    _file(graph, f"{_CLASSES}/OrderService.cls", [_class("OrderService")])
    _file(graph, f"{_CLASSES}/OrderServiceTest.cls", [_class("OrderServiceTest")], is_test=True)
    sources = {
        f"{_CLASSES}/OrderService.cls": "public class OrderService {\n}\n",
        f"{_CLASSES}/OrderServiceTest.cls": "@IsTest class OrderServiceTest {}\n",
    }
    return graph, sources


def test_a_class_nothing_names_is_unreachable() -> None:
    graph, sources = _service_graph()
    report = _analyze(graph, sources)
    assert _dead_files(report) == {f"{_CLASSES}/OrderService.cls"}
    export = next(f for f in report.findings if f.kind is DeadCodeKind.UNUSED_EXPORT)
    assert export.symbol_name == "OrderService"
    assert not export.safe_to_delete


def test_a_name_written_in_another_case_is_a_use() -> None:
    graph, sources = _service_graph()
    sources[f"{_CLASSES}/OrderServiceTest.cls"] = (
        "@IsTest class OrderServiceTest { static void t() { ORDERSERVICE.run(); } }\n"
    )
    assert _analyze(graph, sources).findings == []


def test_lightning_and_metadata_files_name_classes(tmp_path: Path) -> None:
    graph, sources = _service_graph()
    # An LWC module imports the class through @salesforce/apex.
    sources["force-app/main/default/lwc/orders/orders.js"] = (
        "import place from '@salesforce/apex/OrderService.place';\n"
    )
    assert _analyze(graph, sources).findings == []

    graph, sources = _service_graph()
    flow = tmp_path / "force-app/main/default/flows/Place.flow-meta.xml"
    flow.parent.mkdir(parents=True)
    flow.write_text("<actionCalls><actionName>OrderService</actionName></actionCalls>")
    assert _analyze(graph, sources, repo_root=tmp_path).findings == []


def test_access_grants_and_docs_are_not_uses(tmp_path: Path) -> None:
    graph, sources = _service_graph()
    sources["docs/OrderService.md"] = "# OrderService\n"
    grant = tmp_path / "force-app/main/default/permissionsets/Admin.permissionset-meta.xml"
    grant.parent.mkdir(parents=True)
    grant.write_text("<classAccesses><apexClass>OrderService</apexClass></classAccesses>")
    (tmp_path / "manifest").mkdir()
    (tmp_path / "manifest/package.xml").write_text("<members>OrderService</members>")
    assert _dead_files(_analyze(graph, sources, repo_root=tmp_path)) == {
        f"{_CLASSES}/OrderService.cls"
    }


@pytest.mark.parametrize(
    "member",
    [
        {"name": "place", "kind": "method", "decorators": ["@AuraEnabled(cacheable=true)\n public static"]},
        {"name": "place", "kind": "method", "decorators": ["@invocableMethod(label='x')"]},
        {"name": "place", "kind": "method", "modifiers": ["global", "static"]},
        {"name": "place", "kind": "method", "modifiers": ["webservice", "static"]},
    ],
)
def test_an_entry_member_makes_its_file_a_root(member: dict) -> None:
    graph, sources = _service_graph()
    path = f"{_CLASSES}/OrderService.cls"
    sym_id = f"{path}::OrderService::place"
    graph.add_node(
        sym_id, node_type="symbol", file_path=path, language="apex", visibility="public",
        parent_name="OrderService", start_line=2, end_line=3, **member,
    )
    graph.add_edge(path, sym_id, edge_type="defines")
    assert _analyze(graph, sources).findings == []


def test_a_platform_interface_makes_its_file_a_root() -> None:
    graph, sources = _service_graph()
    heritage = {
        f"{_CLASSES}/OrderService.cls": [
            HeritageRelation(child_name="OrderService", parent_name="Schedulable", kind="implements", line=1)
        ]
    }
    assert _analyze(graph, sources, heritage=heritage).findings == []


def test_uses_count_only_from_live_files() -> None:
    graph, sources = _service_graph()
    _file(graph, f"{_CLASSES}/Legacy.cls", [_class("Legacy")])
    sources[f"{_CLASSES}/Legacy.cls"] = "public class Legacy { OrderService s; }\n"
    assert _dead_files(_analyze(graph, sources)) == {
        f"{_CLASSES}/OrderService.cls",
        f"{_CLASSES}/Legacy.cls",
    }
    # Once a test names Legacy, both are live.
    sources[f"{_CLASSES}/OrderServiceTest.cls"] = "@IsTest class OrderServiceTest { Legacy l; }\n"
    assert _dead_files(_analyze(graph, sources)) == set()


def test_triggers_and_anonymous_scripts_are_never_judged() -> None:
    graph = nx.DiGraph()
    _file(graph, "force-app/main/default/triggers/OrderTrigger.trigger",
          [{"name": "OrderTrigger", "kind": "function", "start_line": 1, "end_line": 3}])
    _file(graph, "scripts/apex/seed.apex", [])
    assert _analyze(graph, {}).findings == []


def _private_graph(source: str, **member: object) -> tuple[nx.DiGraph, dict[str, str]]:
    graph = nx.DiGraph()
    path = f"{_CLASSES}/OrderService.cls"
    _file(graph, path, [_class("OrderService", modifiers=["global"])])
    # A private method is never judged (members are reached through their
    # type); a private nested class is.
    sym = {"name": "Recalc", "kind": "class", "visibility": "private", "parent_name": "OrderService",
           "start_line": 4, "end_line": 5, **member}
    _file(graph, path, [sym])
    return graph, {path: source}


_UNUSED_PRIVATE = (
    "global class OrderService {\n  global void run() {\n  }\n  private class Recalc {\n  }\n}\n"
)


def test_a_private_class_named_in_another_case_is_used() -> None:
    source = _UNUSED_PRIVATE.replace("run() {\n", "run() {\n    new RECALC();\n", 1)
    # The use moved the class down a line.
    graph, sources = _private_graph(source, start_line=5, end_line=6)
    assert _analyze(graph, sources).findings == []


def test_an_unused_private_class_is_reported() -> None:
    graph, sources = _private_graph(_UNUSED_PRIVATE)
    assert _flagged(_analyze(graph, sources)) == {("unused_internal", "Recalc")}


@pytest.mark.parametrize("decorators", [["@TestVisible\n private"], ["@future\n private static"]])
def test_a_private_member_the_platform_or_a_test_calls_is_not_reported(decorators) -> None:
    source = _UNUSED_PRIVATE
    graph, sources = _private_graph(source, decorators=decorators)
    assert _analyze(graph, sources).findings == []


def test_an_inner_class_named_in_its_own_file_is_used() -> None:
    graph = nx.DiGraph()
    path = f"{_CLASSES}/OrderService.cls"
    _file(graph, path, [
        _class("OrderService", end=6, modifiers=["global"]),
        _class("Line", start=4, end=5, parent_name="OrderService"),
        _class("Unused", start=6, end=6, parent_name="OrderService"),
    ])
    source = "global class OrderService {\n  global LINE first() {\n  return null; }\n  public class Line {\n  }\n  public class Unused {} }\n"
    report = _analyze(graph, {path: source})
    assert _flagged(report) == {("unused_export", "Unused")}


def test_an_unchecked_apex_file_is_reachable() -> None:
    graph, _sources = _service_graph()
    path = f"{_CLASSES}/OrderService.cls"
    assert is_file_reachable(path, graph, ReachabilityRescues())
    assert not is_file_reachable(path, graph, ReachabilityRescues(apex_live_files=frozenset()))


class TestTestDetection:
    def test_an_is_test_class_is_test_code_whatever_its_name(self) -> None:
        text = "/**\n * Mentions class names.\n */\n@IsTest(SeeAllData=false)\nprivate class SampleHandler {}\n"
        assert declares_test("classes/SampleHandler.cls", text)
        assert code_origin("classes/SampleHandler.cls", text) == "test"

    def test_the_annotation_in_a_comment_or_on_a_method_is_not_enough(self) -> None:
        commented = "/** Use `@IsTest(SeeAllData=true)` in tests. */\npublic class ConnectApiWrapper {}\n"
        method = "public class Util {\n  @IsTest static void t() {}\n}\n"
        assert not declares_test("classes/ConnectApiWrapper.cls", commented)
        assert not declares_test("classes/Util.cls", method)
        assert code_origin("classes/Util.cls", method) == "production"

    def test_only_apex_classes_are_read(self) -> None:
        assert not declares_test("src/Foo.java", "@IsTest class Foo {}")


def test_apex_tests_pair_with_the_class_they_cover() -> None:
    names = paired_test_names("force-app/main/default/classes/AccountService.cls")
    assert {"AccountServiceTest.cls", "AccountService_Test.cls", "AccountService_TEST.cls",
            "AccountService_Tests.cls", "AccountServiceTests.cls"} <= names


def test_a_project_config_running_a_script_by_path_is_a_use(tmp_path: Path) -> None:
    graph, sources = _service_graph()
    (tmp_path / "cumulusci.yml").write_text(
        "tasks:\n  seed:\n    options:\n      path: force-app/main/default/classes/OrderService.cls\n"
    )
    assert _analyze(graph, sources, repo_root=tmp_path).findings == []


def test_a_name_too_short_to_search_is_never_reported() -> None:
    graph = nx.DiGraph()
    path = f"{_CLASSES}/Matchers.cls"
    _file(graph, path, [_class("Matchers", modifiers=["global"]), _class("Eq", start=2, end=2, parent_name="Matchers")])
    report = _analyze(graph, {path: "global class Matchers {\n  public class Eq {}\n}\n"})
    assert report.findings == []


def test_a_script_kept_as_a_class_file_is_named_by_its_path(tmp_path: Path) -> None:
    # CumulusCI runs anonymous Apex stored as ``.cls`` by path; its only
    # declared type is an exception class, not the file's stem.
    graph = nx.DiGraph()
    path = "datasets/rd2/load_settings.cls"
    _file(graph, path, [_class("InitException", start=9, end=9)])
    sources = {path: "public static void run() {}\npublic class InitException extends Exception {}\n"}
    assert _dead_files(_analyze(graph, sources, repo_root=tmp_path)) == {path}
    (tmp_path / "cumulusci.yml").write_text("tasks:\n  seed:\n    options:\n      path: datasets/rd2/load_settings.cls\n")
    assert _dead_files(_analyze(graph, sources, repo_root=tmp_path)) == set()


@pytest.mark.parametrize(
    "member",
    [
        {"name": "save", "kind": "method", "signature": "save() -> PageReference"},
        {"name": "OrderService", "kind": "function",
         "signature": "OrderService(ApexPages.StandardController controller)"},
    ],
)
def test_a_visualforce_shaped_class_is_a_root(member: dict) -> None:
    # Pages are not parsed yet, so the controller shape keeps the class live.
    graph, sources = _service_graph()
    path = f"{_CLASSES}/OrderService.cls"
    _file(graph, path, [{**member, "parent_name": "OrderService", "start_line": 2, "end_line": 3}])
    assert _analyze(graph, sources).findings == []


def test_a_global_nested_class_is_an_entry_point() -> None:
    # Reached by ``Type.forName`` on a configured name, or by a subscriber.
    graph = nx.DiGraph()
    path = f"{_CLASSES}/Handlers.cls"
    _file(graph, path, [
        _class("Handlers", end=4, modifiers=["global"]),
        _class("ToApex", start=2, end=3, parent_name="Handlers", modifiers=["global"]),
    ])
    assert _analyze(graph, {path: "global class Handlers {\n  global class ToApex {\n  }\n}\n"}).findings == []


def test_a_test_visible_member_does_not_make_its_class_a_root() -> None:
    graph, sources = _service_graph()
    path = f"{_CLASSES}/OrderService.cls"
    _file(graph, path, [{"name": "Cache", "kind": "class", "visibility": "private", "parent_name": "OrderService",
                         "start_line": 2, "end_line": 2, "decorators": ["@TestVisible\n private"]}])
    report = _analyze(graph, sources)
    assert _dead_files(report) == {path}
    assert ("unused_internal", "Cache") not in _flagged(report)


def test_metadata_past_the_read_budget_leaves_apex_unjudged(tmp_path: Path, monkeypatch) -> None:
    from repowise.core.analysis.dead_code import apex_reachability

    monkeypatch.setattr(apex_reachability, "_TOTAL_BYTES", 0)
    (tmp_path / "flows").mkdir()
    (tmp_path / "flows/Place.flow-meta.xml").write_text("<actionName>Other</actionName>")
    graph, sources = _service_graph()
    assert _analyze(graph, sources, repo_root=tmp_path).findings == []


def test_a_quoted_annotation_does_not_make_a_test() -> None:
    text = "@SuppressWarnings('@IsTest')\npublic class Foo {}\n"
    assert not declares_test("classes/Foo.cls", text)


def test_an_annotation_after_a_quoted_argument_still_makes_a_test() -> None:
    text = "@SuppressWarnings('PMD.ApexDoc')\n@IsTest\nprivate class FooTests {}\n"
    assert declares_test("classes/FooTests.cls", text)


def test_without_the_repository_nothing_is_judged() -> None:
    # The metadata that may name a class cannot be read, so unread means unjudged.
    graph, sources = _service_graph()
    report = DeadCodeAnalyzer(
        graph, git_meta_map={}, parsed_files={p: _parsed() for p in graph.nodes if p.endswith(".cls")},
        source_map={k: v.encode() for k, v in sources.items()}, repo_root=None,
    ).analyze({"detect_zombie_packages": False, "min_confidence": 0.0})
    assert report.findings == []


def test_an_apex_file_missing_from_the_source_map_leaves_apex_unjudged() -> None:
    graph, sources = _service_graph()
    del sources[f"{_CLASSES}/OrderServiceTest.cls"]
    assert _analyze(graph, sources).findings == []


def test_a_name_in_a_file_ingestion_skipped_is_a_use(tmp_path: Path) -> None:
    graph, sources = _service_graph()
    bundle = tmp_path / "force-app/main/default/staticresources/app.js"
    bundle.parent.mkdir(parents=True)
    bundle.write_text("callApex('OrderService.place');")
    rel = "force-app/main/default/staticresources/app.js"
    report = _analyze(graph, sources, repo_root=tmp_path, unindexed=[(rel, "size")])
    assert _dead_files(report) == set()
