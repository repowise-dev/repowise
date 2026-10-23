"""Apex call, heritage and type resolution: the project namespace and case folding."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_CLASSES = "force-app/main/default/classes"


def _build(repo: Path):
    traverser = FileTraverser(repo)
    parser = ASTParser()
    builder = GraphBuilder(repo_path=repo)
    for fi in traverser.traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


def _project(root: Path, files: dict[str, str], namespace: str = "") -> None:
    root.mkdir(parents=True, exist_ok=True)
    manifest = {"packageDirectories": [{"path": "force-app", "default": True}], "namespace": namespace}
    (root / "sfdx-project.json").write_text(json.dumps(manifest))
    for name, body in files.items():
        path = root / (name if "/" in name else f"{_CLASSES}/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)


def _calls(graph, caller: str) -> dict[str, str]:
    """``{callee id: resolution origin}`` for every call edge out of *caller*."""
    return {
        callee: data.get("resolution_origin")
        for _, callee, data in graph.out_edges(caller, data=True)
        if data.get("edge_type") == "calls"
    }


def _id(name: str, symbol: str, project: str = "") -> str:
    prefix = f"{project}/" if project else ""
    return f"{prefix}{_CLASSES}/{name}::{symbol}"


_SERVICE = """public class AccountService {
    public AccountService() {}
    public AccountService(Integer size) {}
    public static void run() {}
    public void save() {}
    public class Result {
        public Boolean ok() { return true; }
    }
}
"""


def test_new_binds_the_constructor_of_a_project_class(tmp_path: Path) -> None:
    # The class and its constructors share a name, which the global tier refuses.
    _project(tmp_path, {
        "AccountService.cls": _SERVICE,
        "Caller.cls": "public class Caller {\n    void go() { new AccountService(5); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {_id("AccountService.cls", "AccountService::AccountService#1"): "same_package"}


def test_names_resolve_in_any_case(tmp_path: Path) -> None:
    _project(tmp_path, {
        "AccountService.cls": _SERVICE,
        "Caller.cls": (
            "public class Caller {\n"
            "    void go() {\n"
            "        ACCOUNTSERVICE.Run();\n"
            "        accountservice svc = new accountService();\n"
            "    }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls[_id("AccountService.cls", "AccountService::run")] == "receiver_same_package"


def test_static_inner_and_typed_receivers(tmp_path: Path) -> None:
    _project(tmp_path, {
        "AccountService.cls": _SERVICE,
        "Caller.cls": (
            "public class Caller {\n"
            "    private AccountService held;\n"
            "    void go() {\n"
            "        AccountService.run();\n"
            "        AccountService.Result r = new AccountService.Result();\n"
            "        r.ok();\n"
            "        held.save();\n"
            "    }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {
        _id("AccountService.cls", "AccountService::run"): "receiver_same_package",
        _id("AccountService.cls", "AccountService::Result"): "receiver_same_package",
        _id("AccountService.cls", "Result::ok"): "receiver_typed_same_package",
        _id("AccountService.cls", "AccountService::save"): "receiver_field_same_package",
    }


def test_inner_class_wins_inside_its_outer(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Result.cls": "public class Result {\n    public void ok() {}\n}\n",
        "AccountService.cls": (
            "public class AccountService {\n"
            "    void go() { new Result(); }\n"
            "    public class Result {}\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("AccountService.cls", "AccountService::go"))
    assert set(calls) == {_id("AccountService.cls", "AccountService::Result")}


def test_a_local_variable_is_not_the_class_its_name_folds_to(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Logger.cls": "public class Logger {\n    public static void info() {}\n}\n",
        "Caller.cls": (
            "public class Caller {\n"
            "    void go(String logger) {\n"
            "        logger.info();\n"
            "    }\n"
            "}\n"
        ),
    })
    assert _calls(_build(tmp_path), _id("Caller.cls", "Caller::go")) == {}


def test_platform_and_other_namespace_prefixes_name_no_repo_class(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Test.cls": "public class Test {\n    public static void startTest() {}\n}\n",
        "Caller.cls": (
            "public class Caller {\n"
            "    void go() {\n"
            "        System.Test.startTest();\n"
            "        other.Test.startTest();\n"
            "        npsp.Test.startTest();\n"
            "    }\n"
            "}\n"
        ),
    }, namespace="npsp")
    # Only the project's own prefix reaches the repo class.
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {_id("Test.cls", "Test::startTest"): "receiver_same_package"}


def test_each_project_resolves_its_own_copy(tmp_path: Path) -> None:
    caller = "public class Caller {\n    void go() { Util.help(); }\n}\n"
    util = "public class Util {\n    public static void help() {}\n}\n"
    for project in ("one", "two"):
        _project(tmp_path / project, {"Util.cls": util, "Caller.cls": caller})
    graph = _build(tmp_path)
    for project in ("one", "two"):
        calls = _calls(graph, _id("Caller.cls", "Caller::go", project))
        assert calls == {_id("Util.cls", "Util::help", project): "receiver_same_package"}


def test_a_legacy_copy_outside_the_package_directories(tmp_path: Path) -> None:
    util = "public class Util {\n    public static void help() {}\n}\n"
    _project(tmp_path, {
        "Util.cls": util,
        "Caller.cls": "public class Caller {\n    void go() { Util.help(); }\n}\n",
        "mdapi/classes/Util.cls": util,
        "mdapi/classes/Other.cls": "public class Other {\n    void go() { Util.help(); }\n}\n",
    })
    graph = _build(tmp_path)
    assert _calls(graph, _id("Caller.cls", "Caller::go")) == {
        _id("Util.cls", "Util::help"): "receiver_same_package"
    }
    assert _calls(graph, "mdapi/classes/Other.cls::Other::go") == {
        "mdapi/classes/Util.cls::Util::help": "receiver_same_package"
    }


def test_inherited_methods(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Base.cls": (
            "public virtual class Base {\n"
            "    protected void helper() {}\n"
            "    public virtual void run() {}\n"
            "}\n"
        ),
        "Child.cls": (
            "public class Child extends BASE {\n"
            "    public override void run() {\n"
            "        helper();\n"
            "        this.helper();\n"
            "        super.run();\n"
            "    }\n"
            "}\n"
        ),
    })
    graph = _build(tmp_path)
    assert graph.has_edge(_id("Child.cls", "Child"), _id("Base.cls", "Base"))
    calls = _calls(graph, _id("Child.cls", "Child::run"))
    assert calls == {
        _id("Base.cls", "Base::helper"): "enclosing_inherited",
        _id("Base.cls", "Base::run"): "self_inherited",
    }


def test_a_bare_call_never_reaches_another_class_method(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Other.cls": "public class Other {\n    public void unique() {}\n}\n",
        "Caller.cls": "public class Caller {\n    void go() { unique(); }\n}\n",
    })
    assert _calls(_build(tmp_path), _id("Caller.cls", "Caller::go")) == {}


def test_a_bare_call_means_its_own_class_however_another_spells_it(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Other.cls": "public class Other {\n    public void ContactFromDi() {}\n}\n",
        "Caller.cls": (
            "public class Caller {\n"
            "    void go() { ContactFromDi(); }\n"
            "    void contactFromDi() {}\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert set(calls) == {_id("Caller.cls", "Caller::contactFromDi")}


def test_a_capitalised_bare_call_is_a_constructor_not_a_method(tmp_path: Path) -> None:
    # ``new Contact()`` is the sObject, not the repo method ``contact``.
    _project(tmp_path, {
        "Model.cls": "public class Model {\n    public void contact() {}\n}\n",
        "Caller.cls": "public class Caller {\n    void go() { new Contact(); }\n}\n",
    })
    assert _calls(_build(tmp_path), _id("Caller.cls", "Caller::go")) == {}


def test_an_overload_is_chosen_by_an_exact_argument_type(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Log.cls": (
            "public class Log {\n"
            "    public static void add(String message) {}\n"
            "    public static void add(Exception error) {}\n"
            "}\n"
        ),
        "Caller.cls": (
            "public class Caller {\n"
            "    void go(Exception e) { Log.add(e); }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert set(calls) == {_id("Log.cls", "Log::add#1(Exception)")}


def test_a_chained_call_is_typed_by_the_inner_return_type(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Query.cls": (
            "public class Query {\n"
            "    public static Query make() { return new Query(); }\n"
            "    public Query field(String name) { return this; }\n"
            "}\n"
        ),
        "Caller.cls": "public class Caller {\n    void go() { Query.make().field('Id'); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls[_id("Query.cls", "Query::field")] == "return_type_same_package"


def test_type_references_draw_file_edges(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Shape.cls": "public interface Shape {}\n",
        "Caller.cls": "public class Caller {\n    void go(SHAPE s) {}\n}\n",
    })
    graph = _build(tmp_path)
    data = graph.get_edge_data(f"{_CLASSES}/Caller.cls", f"{_CLASSES}/Shape.cls")
    assert data is not None and data["edge_type"] == "type_use"


def test_a_lower_case_bare_call_is_not_a_constructor(tmp_path: Path) -> None:
    # ``execute()`` inherited from a platform class is not ``new Execute()``.
    _project(tmp_path, {
        "Execute.cls": "public class Execute {}\n",
        "Other.cls": "public class Other {\n    public void execute() {}\n}\n",
        "Job.cls": "public class Job implements Queueable {\n    void go() { execute(); }\n}\n",
    })
    assert _calls(_build(tmp_path), _id("Job.cls", "Job::go")) == {}


def test_a_malformed_manifest_still_resolves(tmp_path: Path) -> None:
    _project(tmp_path, {
        "AccountService.cls": _SERVICE,
        "Caller.cls": "public class Caller {\n    void go() { AccountService.run(); }\n}\n",
    })
    (tmp_path / "sfdx-project.json").write_text('{"packageDirectories": 5, "namespace": 7}')
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {_id("AccountService.cls", "AccountService::run"): "receiver_same_package"}


def test_a_property_is_never_the_callee(tmp_path: Path) -> None:
    # The child's property ``status`` hides nothing: the call is the base's method.
    _project(tmp_path, {
        "Base.cls": "public virtual class Base {\n    public static String status() { return ''; }\n}\n",
        "Child.cls": "public class Child extends Base {\n    public String status { get; set; }\n}\n",
        "Caller.cls": "public class Caller {\n    void go() { Child.status(); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {_id("Base.cls", "Base::status"): "receiver_same_package"}


def _with_dependency(root: Path, files: dict[str, str], package: str, depends_on: str) -> None:
    _project(root, files)
    manifest = {
        "packageDirectories": [
            {
                "path": "force-app",
                "package": package,
                "dependencies": [{"package": f"{depends_on}@1.0.0-0"}] if depends_on else [],
            }
        ],
        "namespace": "",
    }
    (root / "sfdx-project.json").write_text(json.dumps(manifest))


def test_another_project_is_reached_only_through_a_dependency(tmp_path: Path) -> None:
    base = "public virtual class Base {\n    public static void help() {}\n}\n"
    child = (
        "public class Child extends Base {\n"
        "    void go() { Base.help(); }\n"
        "}\n"
    )
    _with_dependency(tmp_path / "base", {"Base.cls": base}, "BasePack", "")
    _with_dependency(tmp_path / "uses", {"Child.cls": child}, "Uses", "BasePack")
    _with_dependency(tmp_path / "other", {"Child.cls": child}, "Other", "")
    graph = _build(tmp_path)
    base_id = _id("Base.cls", "Base", "base")
    assert graph.has_edge(_id("Child.cls", "Child", "uses"), base_id)
    assert _calls(graph, _id("Child.cls", "Child::go", "uses")) == {
        _id("Base.cls", "Base::help", "base"): "receiver_same_package"
    }
    # No declared dependency: no heritage edge and no call edge across projects.
    assert not graph.has_edge(_id("Child.cls", "Child", "other"), base_id)
    assert _calls(graph, _id("Child.cls", "Child::go", "other")) == {}


def test_a_namespaced_project_is_reached_through_its_prefix(tmp_path: Path) -> None:
    _project(tmp_path / "base", {"Util.cls": "public class Util {\n    public static void help() {}\n}\n"}, namespace="usf3")
    _project(tmp_path / "uses", {
        "Caller.cls": "public class Caller {\n    void go() { usf3.Util.help(); Util.help(); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go", "uses"))
    assert calls == {_id("Util.cls", "Util::help", "base"): "receiver_same_package"}


def test_an_exact_argument_type_decides_only_when_no_other_overload_could_take_it(
    tmp_path: Path,
) -> None:
    _project(tmp_path, {
        "Log.cls": (
            "public class Log {\n"
            "    public static void add(Exception error, Long code) {}\n"
            "    public static void add(String message, Integer level) {}\n"
            "    public static void put(Id record, Account owner) {}\n"
            "    public static void put(String name, Integer level) {}\n"
            "    public static void link(Account owner, String note) {}\n"
            "    public static void link(Contact person, Integer rank) {}\n"
            "}\n"
        ),
        "Caller.cls": (
            "public class Caller {\n"
            "    void go(String name, Contact person) {\n"
            "        Log.add('x', unknown);\n"
            "        Log.put(name, unknown);\n"
            "        Log.link(person, unknown);\n"
            "    }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    # A String never becomes an Exception, so ``add`` is chosen; it may become
    # an Id, so the untyped argument could still mean the first ``put``, which
    # stands for the set.
    assert set(calls) == {
        _id("Log.cls", "Log::add#2(String,Integer)"),
        _id("Log.cls", "Log::put#2(Id,Account)"),
        # One sObject never stands for another, so the Contact member is chosen.
        _id("Log.cls", "Log::link#2(Contact,Integer)"),
    }


def test_a_variable_in_another_case_and_a_builtin_named_member_type(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Plugin.cls": (
            "public class Plugin {\n"
            "    public interface Batchable { void start(); }\n"
            "    public void run() {}\n"
            "}\n"
        ),
        "Caller.cls": (
            "public class Caller {\n"
            "    void go(Plugin.Batchable batch) {\n"
            "        Plugin worker = new Plugin();\n"
            "        WORKER.run();\n"
            "        batch.start();\n"
            "    }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls[_id("Plugin.cls", "Plugin::run")] == "receiver_typed_same_package"
    assert calls[_id("Plugin.cls", "Batchable::start")] == "receiver_typed_same_package"


def test_a_qualified_parent_is_read_through_its_outer_type(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Metadata.cls": "public class Metadata {\n    public virtual class FlowNode {}\n}\n",
        "Other.cls": "public class Other {}\n",
        "Right.cls": "public class Right extends Metadata.FlowNode {}\n",
        "Wrong.cls": "public class Wrong extends Other.FlowNode {}\n",
        "Managed.cls": "public class Managed extends pkg.Outer.FlowNode {}\n",
    })
    graph = _build(tmp_path)
    flow_node = _id("Metadata.cls", "Metadata::FlowNode")
    assert graph.has_edge(_id("Right.cls", "Right"), flow_node)
    assert not graph.has_edge(_id("Wrong.cls", "Wrong"), flow_node)
    assert not graph.has_edge(_id("Managed.cls", "Managed"), flow_node)


def test_the_own_namespace_prefix_never_reaches_a_dependency(tmp_path: Path) -> None:
    util = "public class Util {\n    public static void help() {}\n}\n"
    _with_dependency(tmp_path / "base", {"Util.cls": util}, "BasePack", "")
    _with_dependency(
        tmp_path / "uses",
        {
            "Caller.cls": (
                "public class Caller {\n"
                "    void prefixed() { acme.Util.help(); }\n"
                "    void bare() { Util.help(); }\n"
                "}\n"
            )
        },
        "Uses",
        "BasePack",
    )
    manifest = tmp_path / "uses" / "sfdx-project.json"
    manifest.write_text(manifest.read_text().replace('"namespace": ""', '"namespace": "acme"'))
    graph = _build(tmp_path)
    # Only the unprefixed call reaches the dependency's class.
    assert _calls(graph, _id("Caller.cls", "Caller::prefixed", "uses")) == {}
    assert _calls(graph, _id("Caller.cls", "Caller::bare", "uses")) == {
        _id("Util.cls", "Util::help", "base"): "receiver_same_package"
    }


# --- ``new`` is read from the syntax (``CallSite.constructor``), not from capitalisation ---


def test_a_lower_case_new_binds_the_type(tmp_path: Path) -> None:
    # Apex is case-insensitive: ``new gifts()`` is the class ``Gifts``. With no
    # declared constructor there is no member of that name to fold it onto.
    _project(tmp_path, {
        "Gifts.cls": "public class Gifts {\n}\n",
        "Other.cls": "public class Other {\n    public void gifts() {}\n}\n",
        "Seeder.cls": "public class Seeder {\n    void run() { new gifts(); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Seeder.cls", "Seeder::run"))
    assert calls == {_id("Gifts.cls", "Gifts"): "same_package"}


def test_new_is_the_type_and_a_plain_call_is_the_method(tmp_path: Path) -> None:
    # The class declares a method named like the type it builds.
    _project(tmp_path, {
        "Seeder.cls": (
            "public class Seeder {\n"
            "    Contact contact() { return null; }\n"
            "    void run() { new Contact(); }\n"
            "    void again() { contact(); Contact(); }\n"
            "}\n"
        ),
    })
    graph = _build(tmp_path)
    # ``new Contact()`` is the sObject: it never binds the method ``contact``.
    assert _calls(graph, _id("Seeder.cls", "Seeder::run")) == {}
    # Plain calls stay on the member path, in either case.
    assert set(_calls(graph, _id("Seeder.cls", "Seeder::again"))) == {
        _id("Seeder.cls", "Seeder::contact")
    }


def test_a_project_type_wins_over_a_method_of_the_same_name(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Contact.cls": "public class Contact {\n    public Contact() {}\n}\n",
        "Seeder.cls": (
            "public class Seeder {\n"
            "    void contact() {}\n"
            "    void run() { new Contact(); new contact(); }\n"
            "}\n"
        ),
    })
    calls = _calls(_build(tmp_path), _id("Seeder.cls", "Seeder::run"))
    assert calls == {_id("Contact.cls", "Contact::Contact"): "same_package"}


def test_a_scoped_new_binds_the_inner_type(tmp_path: Path) -> None:
    _project(tmp_path, {
        "Outer.cls": "public class Outer {\n    public class Inner {\n        public Inner() {}\n    }\n}\n",
        "Caller.cls": "public class Caller {\n    void go() { new outer.inner(); }\n}\n",
    })
    calls = _calls(_build(tmp_path), _id("Caller.cls", "Caller::go"))
    assert calls == {_id("Outer.cls", "Inner::Inner"): "receiver_same_package"}


def test_only_a_new_site_is_flagged_as_a_constructor(tmp_path: Path) -> None:
    _project(tmp_path, {
        "A.cls": (
            "public class A {\n"
            "    void go() { new gifts(); Foo.bar(); baz(); new Outer.Inner(); new Foo().run(); }\n"
            "}\n"
        ),
    })
    info = next(fi for fi in FileTraverser(tmp_path).traverse() if fi.abs_path.endswith("A.cls"))
    parsed = ASTParser().parse_file(info, Path(info.abs_path).read_bytes())
    flagged = {(c.target_name, c.receiver_name) for c in parsed.calls if c.constructor}
    plain = {(c.target_name, c.receiver_name) for c in parsed.calls if not c.constructor}
    assert flagged == {("gifts", None), ("Inner", "Outer"), ("Foo", None)}
    assert {("baz", None), ("bar", "Foo"), ("run", "Foo")} <= plain
