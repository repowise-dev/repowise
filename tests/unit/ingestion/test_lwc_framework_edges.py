"""Lightning Web Components: module specifiers, template tags and bindings, exposed bundles.

The end-to-end cases go through traverse -> parse -> graph -> framework edges
-> dead code, the path a real index takes.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.resolvers.lwc import kebab, lwc_bundle_dir, nearest

_LWC = "force-app/main/default/lwc"
_EXPOSED = "<LightningComponentBundle><isExposed>true</isExposed></LightningComponentBundle>\n"
# A target counts for nothing while the bundle is not exposed.
_HIDDEN = (
    "<LightningComponentBundle><isExposed>false</isExposed>"
    "<targets><target>lightning__Tab</target></targets></LightningComponentBundle>\n"
)

_APP = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}\n',
    f"{_LWC}/parent/parent.js": (
        "import { LightningElement } from 'lwc';\n"
        "import { reduceErrors } from 'c/ldsUtils';\n"
        "import getThings from '@salesforce/apex/ThingController.getThings';\n"
        "import NAME from '@salesforce/schema/Account.Name';\n"
        "export default class Parent extends LightningElement {\n"
        "  get label() { return reduceErrors([]); }\n"
        "  handleClick() { return getThings(); }\n"
        "  notInTemplate() { return NAME; }\n"
        "  get row() { return null; }\n"
        "}\n"
    ),
    f"{_LWC}/parent/parent.html": (
        "<template>\n"
        "  <!-- <c-orphan></c-orphan> -->\n"
        "  <lightning-button label={label} onclick={handleClick}></lightning-button>\n"
        "  <c-child-comp record-id={recordId}></c-child-comp>\n"
        "  <acme-fsc_flow-combobox></acme-fsc_flow-combobox>\n"
        "  <c-rd2-entry-form></c-rd2-entry-form>\n"
        "  <other-child-comp></other-child-comp>\n"
        "  <c-parent></c-parent>\n"
        '  <template for:each={label} for:item="row"><p key={row.id}>{row.name}</p></template>\n'
        "</template>\n"
    ),
    f"{_LWC}/parent/parent.js-meta.xml": _EXPOSED.replace(
        "</LightningComponentBundle>",
        '<targetConfigs><targetConfig targets="lightning__FlowScreen"'
        ' configurationEditor="c-parent-C-P-E"/></targetConfigs></LightningComponentBundle>',
    ),
    f"{_LWC}/parentCPE/parentCPE.js": (
        "import { LightningElement } from 'lwc';\n"
        "export default class ParentCPE extends LightningElement {}\n"
    ),
    f"{_LWC}/parent/__tests__/parent.test.js": (
        "import { createElement } from 'lwc';\nimport Parent from 'c/parent';\n"
        "import getThings from '@salesforce/apex/ThingController.getThings';\n"
        "it('renders', () => { createElement('c-parent', { is: Parent }); });\n"
    ),
    f"{_LWC}/childComp/childComp.js": (
        "import { LightningElement, api, wire } from 'lwc';\n"
        "import getThings from '@salesforce/apex/acme.thingController.getThings';\n"
        "export default class ChildComp extends LightningElement {\n"
        "  @api recordId;\n"
        "  @wire(getThings) things;\n"
        "}\n"
    ),
    f"{_LWC}/fsc_flowCombobox/fsc_flowCombobox.js": (
        "import { LightningElement } from 'lwc';\n"
        "export default class FlowCombobox extends LightningElement {}\n"
    ),
    f"{_LWC}/rd2EntryForm/rd2EntryForm.js": (
        "import { LightningElement } from 'lwc';\n"
        "export default class Rd2EntryForm extends LightningElement {}\n"
    ),
    "force-app/main/default/classes/ThingController.cls": (
        "public with sharing class ThingController {\n"
        "    @AuraEnabled(cacheable=true)\n"
        "    public static List<String> getThings() { return getThings(1); }\n"
        "    private static List<String> getThings(Integer n) { return new List<String>(); }\n"
        "}\n"
    ),
    f"{_LWC}/ldsUtils/ldsUtils.js": "export function reduceErrors(errors) { return errors; }\n",
    f"{_LWC}/orphan/orphan.js": (
        "import { LightningElement } from 'lwc';\n"
        "export default class Orphan extends LightningElement {}\n"
    ),
    f"{_LWC}/orphan/orphan.js-meta.xml": _HIDDEN,
}


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _graph(repo: Path) -> tuple[object, set[tuple[str, str]]]:
    builder = GraphBuilder(repo)
    parser = ASTParser()
    source_map: dict[str, bytes] = {}
    for fi in FileTraverser(repo).traverse():
        src = Path(fi.abs_path).read_bytes()
        builder.add_file(parser.parse_file(fi, src))
        source_map[fi.path] = src
    builder.set_source_map(source_map)
    builder.build()
    builder.add_framework_edges([])
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({"detect_unused_internals": True})
    dead = {(f.kind.value, f.file_path) for f in report.findings}
    return builder.graph(), dead


def test_kebab_keeps_digits_attached_and_underscores_literal() -> None:
    assert kebab("accountList") == "account-list"
    assert kebab("rd2EntryForm") == "rd2-entry-form"
    assert kebab("fsc_flowCombobox") == "fsc_flow-combobox"


def test_an_apex_import_binding_publishes_the_method_name(tmp_path: Path) -> None:
    repo = _write(tmp_path, {f"{_LWC}/a/a.js": (
        "import fetchThings from '@salesforce/apex/ns.ThingController.getThings';\n"
        "import other from './helper';\n"
    )})  # fmt: skip
    (fi,) = FileTraverser(repo).traverse()
    parsed = ASTParser().parse_file(fi, Path(fi.abs_path).read_bytes())
    apex, plain = parsed.imports
    assert apex.imported_names == ["getThings"]
    assert [(b.local_name, b.exported_name) for b in apex.bindings] == [("fetchThings", "getThings")]
    assert [(b.local_name, b.exported_name) for b in plain.bindings] == [("other", "default")]


def test_bundle_dir_covers_tests_and_ignores_the_lwc_root() -> None:
    assert lwc_bundle_dir(f"{_LWC}/a/__tests__/a.test.js") == f"{_LWC}/a"
    assert lwc_bundle_dir(f"{_LWC}/a/a.html") == f"{_LWC}/a"
    assert lwc_bundle_dir(f"{_LWC}/jsconfig.json") is None
    assert lwc_bundle_dir("src/app.js") is None


def test_nearest_prefers_the_referrer_s_project_and_refuses_a_tie() -> None:
    a, b = "p1/lwc/x/x.js", "p2/lwc/x/x.js"
    assert nearest((a, b), "p1/lwc/y/y.js") == a
    assert nearest((a, b), "p3/lwc/y/y.js") is None


def test_lwc_bundles_are_linked_bound_and_anchored(tmp_path: Path) -> None:
    graph, dead = _graph(_write(tmp_path, _APP))
    parent_js, parent_html = f"{_LWC}/parent/parent.js", f"{_LWC}/parent/parent.html"

    # c/ldsUtils from a bundle, c/parent from a jest test.
    assert graph.has_edge(parent_js, f"{_LWC}/ldsUtils/ldsUtils.js")
    assert graph.has_edge(f"{_LWC}/parent/__tests__/parent.test.js", parent_js)
    # @salesforce/apex: the class file, the imperative call and the @wire binding
    # reach the method (namespace and class case-insensitive). A jest test only
    # mocks Apex, so its import stays external.
    cls = "force-app/main/default/classes/ThingController.cls"
    method = f"{cls}::ThingController::getThings#0"
    assert graph.has_edge(parent_js, cls)
    call = graph.get_edge_data(f"{parent_js}::Parent::handleClick", method)
    assert (call["edge_type"], call["resolution_origin"]) == ("calls", "import_scoped")
    child_cls = f"{_LWC}/childComp/childComp.js::ChildComp"
    assert graph.get_edge_data(child_cls, method)["edge_type"] == "framework_binds"
    assert not graph.has_edge(f"{_LWC}/parent/__tests__/parent.test.js", cls)
    # Platform modules stay external.
    assert graph.has_edge(parent_js, "external:@salesforce/schema/Account.Name")

    # Tags: kebab, digits, underscores, the sfdx namespace; not other namespaces,
    # comments or the component's own tag.
    tag_targets = {t for _, t in graph.out_edges(parent_html) if not t.startswith(parent_html)}
    assert tag_targets == {
        f"{_LWC}/childComp/childComp.js",
        f"{_LWC}/fsc_flowCombobox/fsc_flowCombobox.js",
        f"{_LWC}/rd2EntryForm/rd2EntryForm.js",
    }

    # Bindings reach the getter and the handler, as framework_binds, and nothing else.
    module = f"{parent_html}::__module__"
    binds = {t for _, t, d in graph.out_edges(module, data=True) if d["edge_type"] == "framework_binds"}
    assert binds == {f"{parent_js}::Parent::label", f"{parent_js}::Parent::handleClick"}

    # The Flow property editor its meta names, matched case-insensitively.
    assert graph.has_edge(parent_js, f"{_LWC}/parentCPE/parentCPE.js")

    # Exposure anchors the bundle; an unexposed bundle nothing names stays dead.
    assert graph.has_edge("framework:lwc", parent_js)
    assert graph.nodes[parent_js]["is_entry_point"] is True
    unreachable = {path for kind, path in dead if kind == "unreachable_file"}
    assert f"{_LWC}/orphan/orphan.js" in unreachable
    assert not unreachable & {
        parent_js,
        f"{_LWC}/childComp/childComp.js",
        f"{_LWC}/ldsUtils/ldsUtils.js",
        f"{_LWC}/parentCPE/parentCPE.js",
    }


def test_a_loop_variable_shadows_a_member_only_inside_its_loop(tmp_path: Path) -> None:
    js = f"{_LWC}/list/list.js"
    files = {
        js: (
            "import { LightningElement } from 'lwc';\n"
            "export default class List extends LightningElement {\n"
            "  get item() { return 1; }\n  get child() { return 2; }\n"
            "  get it() { return 3; }\n  get rows() { return []; }\n  get idx() { return 4; }\n"
            "}\n"
        ),
        f"{_LWC}/list/list.html": (
            "<template>\n"
            "  <p>{item}</p>\n"
            '  <template for:each={rows} for:item="item">\n'
            "    <p key={item.id}>{item.name}</p>\n"
            '    <template for:each={item.kids} for:item="child">{child} {item.x}</template>\n'
            "    {child.name}\n"
            "  </template>\n"
            "  <template iterator:it={rows}>{it.value}</template>\n"
            '  <template for:each={rows} for:item="r" for:index="idx">{idx}</template>\n'
            "  {idx}\n"
            "</template>\n"
        ),
    }
    graph, _ = _graph(_write(tmp_path, files))
    module = f"{_LWC}/list/list.html::__module__"
    binds = {t.rpartition("::")[2] for _, t, d in graph.out_edges(module, data=True)
             if d["edge_type"] == "framework_binds"}  # fmt: skip
    # `item` before the loop and `rows` in the loop's own tag read the members;
    # `child` after the inner loop closes and `idx` after its loop read the members
    # too; `it` occurs only inside its loop.
    assert binds == {"item", "rows", "child", "idx"}


def test_a_name_shared_by_two_projects_resolves_within_the_importer_s(tmp_path: Path) -> None:
    files = {}
    for project in ("one", "two"):
        root = f"{project}/force-app/main/default/lwc"
        files[f"{root}/dup/dup.js"] = "export const x = 1;\n"
        files[f"{root}/user/user.js"] = "import { x } from 'c/dup';\nexport default x;\n"
    graph, _ = _graph(_write(tmp_path, files))
    for project in ("one", "two"):
        root = f"{project}/force-app/main/default/lwc"
        assert graph.has_edge(f"{root}/user/user.js", f"{root}/dup/dup.js")
