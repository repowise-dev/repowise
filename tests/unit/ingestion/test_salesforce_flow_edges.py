"""Salesforce flows and placement metadata: flow symbols, their edges, and liveness.

The end-to-end cases go through traverse -> parse -> graph -> framework edges
-> dead code, the path a real index takes.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.salesforce_flow import flow_api_name, read_flow

_D = "force-app/main/default"
_NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _flow(body: str) -> str:
    return f'<?xml version="1.0" encoding="UTF-8"?>\n<Flow {_NS}>\n{body}</Flow>\n'


_MAIN_FLOW = _flow(
    "    <actionCalls><name>Go</name><actionName>acme__Mailer</actionName>"
    "<actionType>apex</actionType></actionCalls>\n"
    "    <actionCalls><name>Ext</name><actionName>other__Mailer</actionName>"
    "<actionType>apex</actionType></actionCalls>\n"
    "    <actionCalls><name>Mail</name><actionName>emailSimple</actionName>"
    "<actionType>emailSimple</actionType></actionCalls>\n"
    "    <apexPluginCalls><name>P</name><apexClass>LegacyPlugin</apexClass></apexPluginCalls>\n"
    "    <!-- <subflows><flowName>Ghost</flowName></subflows> -->\n"
    "    <subflows><name>S</name><flowName>child_flow</flowName></subflows>\n"
    "    <processType>Flow</processType>\n"
    "    <screens><name>Scr</name><fields><name>F</name>"
    "<extensionName>c:pickerLwc</extensionName><fieldType>ComponentInstance</fieldType></fields>"
    "<fields><name>G</name><extensionName>c:legacyAura</extensionName></fields>"
    "<fields><name>M</name><extensionName>c:markupAura</extensionName></fields>"
    "<fields><name>H</name><extensionName>flowruntime:fileUpload</extensionName></fields>"
    "</screens>\n"
    "    <start><object>Account</object><triggerType>RecordAfterSave</triggerType></start>\n"
    "    <variables><name>w</name><dataType>Apex</dataType><apexClass>Wrapper</apexClass></variables>\n"
)

_APP = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}\n',
    f"{_D}/flows/Main_Flow.flow-meta.xml": _MAIN_FLOW,
    f"{_D}/flows/Child_Flow.flow-meta.xml": _flow("    <processType>AutoLaunchedFlow</processType>\n"),
    f"{_D}/flows/Onboard.flow-meta.xml": _flow("    <processType>AutoLaunchedFlow</processType>\n"),
    f"{_D}/flows/With_Inputs.flow-meta.xml": _flow("    <processType>AutoLaunchedFlow</processType>\n"),
    f"{_D}/flows/Vf_Flow.flow-meta.xml": _flow("    <processType>AutoLaunchedFlow</processType>\n"),
    f"{_D}/classes/Mailer.cls": (
        "public with sharing class Mailer {\n"
        "    @InvocableMethod(label='Send')\n"
        "    public static void send(List<Id> ids) {}\n"
        "    public static void helper() {}\n"
        "}\n"
    ),
    f"{_D}/classes/LegacyPlugin.cls": "global class LegacyPlugin implements Process.Plugin {}\n",
    f"{_D}/classes/Wrapper.cls": "public class Wrapper { @AuraEnabled public String name; }\n",
    f"{_D}/classes/Launcher.cls": (
        "public class Launcher {\n"
        "    public void run(Map<String, Object> m) {\n"
        "        Flow.Interview.Child_Flow f = new Flow.Interview.Child_Flow(m);\n"
        "        // Flow.Interview.Main_Flow is only a comment\n"
        "        Flow.Interview.createInterview('Main_Flow', m).start();\n"
        "    }\n"
        "}\n"
    ),
    f"{_D}/flows/Quoted_Flow.flow-meta.xml": _flow("    <processType>Flow</processType>\n"),
    f"{_D}/classes/Quoter.cls": (
        "public class Quoter {\n"
        "    public String text() {\n"
        "        return 'Flow.Interview.createInterview(\\'Quoted_Flow\\', m)'\n"
        "            + ' new Flow.Interview.Quoted_Flow(m)';\n"
        "    }\n"
        "}\n"
    ),
    f"{_D}/classes/InboundHandler.cls": (
        "global class InboundHandler implements Messaging.InboundEmailHandler {}\n"
    ),
    f"{_D}/emailservices/Inbound.xml-meta.xml": (
        f"<EmailServicesFunction {_NS}><apexClass>InboundHandler</apexClass></EmailServicesFunction>\n"
    ),
    f"{_D}/permissionsets/All.permissionset-meta.xml": (
        f"<PermissionSet {_NS}><classAccesses><apexClass>Launcher</apexClass>"
        "</classAccesses></PermissionSet>\n"
    ),
    f"{_D}/lwc/pickerLwc/pickerLwc.js": (
        "import { LightningElement } from 'lwc';\nexport default class PickerLwc extends LightningElement {}\n"
    ),
    f"{_D}/lwc/host/host.js": (
        "import { LightningElement } from 'lwc';\nexport default class Host extends LightningElement {}\n"
    ),
    f"{_D}/lwc/host/host.html": (
        '<template><lightning-flow flow-api-name="Child_Flow"></lightning-flow>'
        "<lightning-flow flow-api-name={dynamicName}></lightning-flow></template>\n"
    ),
    f"{_D}/lwc/onPage/onPage.js": (
        "import { LightningElement } from 'lwc';\nexport default class OnPage extends LightningElement {}\n"
    ),
    f"{_D}/lwc/onTab/onTab.js": (
        "import { LightningElement } from 'lwc';\nexport default class OnTab extends LightningElement {}\n"
    ),
    f"{_D}/lwc/onAction/onAction.js": (
        "import { LightningElement } from 'lwc';\nexport default class OnAction extends LightningElement {}\n"
    ),
    f"{_D}/lwc/onSite/onSite.js": (
        "import { LightningElement } from 'lwc';\nexport default class OnSite extends LightningElement {}\n"
    ),
    f"{_D}/lwc/orphan/orphan.js": (
        "import { LightningElement } from 'lwc';\nexport default class Orphan extends LightningElement {}\n"
    ),
    f"{_D}/aura/legacyAura/legacyAuraController.js": "({ init: function (c, e, h) {} })\n",
    f"{_D}/aura/markupAura/markupAura.cmp": (
        '<aura:component implements="lightning:availableForFlowScreens"/>\n'
    ),
    f"{_D}/aura/markupAura/markupAuraController.js": "({ init: function (c, e, h) {} })\n",
    f"{_D}/aura/flowHost/flowHost.cmp": (
        '<aura:component><lightning:flow aura:id="flow"/></aura:component>\n'
    ),
    f"{_D}/aura/flowHost/flowHostController.js": (
        "({ start: function(cmp) {\n"
        '    cmp.find("flow").startFlow("Onboard");\n'
        '    cmp.find("flow").startFlow("With_Inputs", inputVariables);\n'
        '    // cmp.find("flow").startFlow("Child_Flow");\n'
        "    var example = '.startFlow(\"Child_Flow\")';\n"
        '    cmp.find("flow").startFlow(dynamicName);\n'
        '    cmp.find("flow").startFlow("Missing_Flow");\n'
        "} })\n"
    ),
    f"{_D}/pages/Intake.page": (
        '<apex:page><flow:interview name="Vf_Flow"/>'
        '<!-- <flow:interview name="Child_Flow"/> -->\n</apex:page>\n'
    ),
    f"{_D}/flexipages/Home.flexipage-meta.xml": (
        f"<FlexiPage {_NS}><flexiPageRegions><itemInstances><componentInstance>"
        "<componentName>acme:onPage</componentName></componentInstance>"
        "<componentInstance><componentName>force:detailPanel</componentName></componentInstance>"
        "<componentInstanceProperties><name>flowName</name><value>Child_Flow</value>"
        "</componentInstanceProperties>"
        "<!-- <componentInstanceProperties><name>flowName</name><value>Onboard</value>"
        "</componentInstanceProperties> -->"
        "</itemInstances></flexiPageRegions></FlexiPage>\n"
    ),
    f"{_D}/tabs/On_Tab.tab-meta.xml": f"<CustomTab {_NS}><lwcComponent>onTab</lwcComponent></CustomTab>\n",
    f"{_D}/quickActions/Account.Do.quickAction-meta.xml": (
        f"<QuickAction {_NS}><lightningWebComponent>onAction</lightningWebComponent>"
        "<type>LightningWebComponent</type></QuickAction>\n"
    ),
    "site/experiences/portal1/views/home.json": (
        '{"components": [{"definition": "c:onSite"}, {"definition": "forceCommunity:tabset"}]}\n'
    ),
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


def test_flow_api_name_drops_the_suffix_and_a_legacy_version() -> None:
    assert flow_api_name("a/flows/My_Flow.flow-meta.xml") == "My_Flow"
    assert flow_api_name("src/flows/churn-3.flow") == "churn"


def test_read_flow_collects_names_and_skips_comments() -> None:
    refs = read_flow(_MAIN_FLOW.encode())
    assert refs is not None
    assert refs.apex_actions == ["acme__Mailer", "other__Mailer", "LegacyPlugin"]
    assert refs.flows == ["child_flow"]
    assert refs.components == [
        "c:pickerLwc", "c:legacyAura", "c:markupAura", "flowruntime:fileUpload"
    ]  # fmt: skip
    assert refs.apex_types == ["Wrapper"]
    assert (refs.start_object, refs.trigger_type, refs.process_type) == (
        "Account", "RecordAfterSave", "Flow"
    )  # fmt: skip
    assert read_flow(b"<CustomObject/>") is None
    assert read_flow(b"<Flow><broken") is None


def test_flows_are_symbols_linked_to_apex_flows_and_components(tmp_path: Path) -> None:
    repo = _write(tmp_path, _APP)
    graph, dead = _graph(repo)
    main = f"{_D}/flows/Main_Flow.flow-meta.xml"
    child = f"{_D}/flows/Child_Flow.flow-meta.xml"
    cls = f"{_D}/classes"
    lwc = f"{_D}/lwc"

    node = graph.nodes[f"{main}::Main_Flow"]
    assert node["language"] == "salesforce_flow"
    assert node["signature"] == "flow Main_Flow (Flow, RecordAfterSave on Account)"

    def edge(s: str, t: str) -> str | None:
        data = graph.get_edge_data(s, t)
        return data["edge_type"] if data else None

    # Apex action: file edge, and the flow binds the one @InvocableMethod.
    assert edge(main, f"{cls}/Mailer.cls") == "framework"
    assert edge(f"{main}::Main_Flow", f"{cls}/Mailer.cls::Mailer::send") == "framework_binds"
    # A plugin class with no invocable method binds the class itself.
    assert edge(f"{main}::Main_Flow", f"{cls}/LegacyPlugin.cls::LegacyPlugin") == "framework_binds"
    assert edge(main, f"{cls}/Wrapper.cls") == "framework"
    # Subflow: names fold case.
    assert edge(main, child) == "framework"
    assert edge(f"{main}::Main_Flow", f"{child}::Child_Flow") == "framework_binds"
    # Screen components: LWC main module; Aura controller while its markup is unindexed.
    assert edge(main, f"{lwc}/pickerLwc/pickerLwc.js") == "framework"
    assert edge(main, f"{_D}/aura/legacyAura/legacyAuraController.js") == "framework"
    # With its markup indexed, an Aura bundle is reached at the .cmp.
    assert edge(main, f"{_D}/aura/markupAura/markupAura.cmp") == "framework"

    # Apex names flows by static type and by literal; a comment does not count.
    launcher = f"{cls}/Launcher.cls"
    assert edge(f"{launcher}::Launcher::run", f"{child}::Child_Flow") == "framework_binds"
    assert edge(f"{launcher}::Launcher::run", f"{main}::Main_Flow") == "framework_binds"
    # A call written inside a string literal names nothing.
    quoted = f"{_D}/flows/Quoted_Flow.flow-meta.xml"
    assert not {t for _, t in graph.out_edges(f"{cls}/Quoter.cls")} & {quoted}
    assert not [s for s, _ in graph.in_edges(f"{quoted}::Quoted_Flow") if "Quoter" in s]
    # LWC <lightning-flow>, literal names only.
    assert edge(f"{lwc}/host/host.html", child) == "framework"
    targets = {t for _, t in graph.out_edges(f"{lwc}/host/host.html")}
    assert main not in targets

    # Aura and Visualforce launch flows from literal references; comments and
    # dynamic Aura names do not add edges.
    aura_controller = f"{_D}/aura/flowHost/flowHostController.js"
    onboard = f"{_D}/flows/Onboard.flow-meta.xml"
    with_inputs = f"{_D}/flows/With_Inputs.flow-meta.xml"
    vf_page = f"{_D}/pages/Intake.page"
    vf_flow = f"{_D}/flows/Vf_Flow.flow-meta.xml"
    assert edge(aura_controller, onboard) == "framework"
    assert edge(aura_controller, with_inputs) == "framework"
    assert edge(f"{aura_controller}::__module__", f"{onboard}::Onboard") == "framework_binds"
    assert edge(vf_page, vf_flow) == "framework"
    assert edge(f"{vf_page}::__module__", f"{vf_flow}::Vf_Flow") == "framework_binds"
    assert not graph.has_edge(aura_controller, child)
    assert not graph.has_edge(vf_page, child)
    assert {t for _, t in graph.out_edges(aura_controller) if "/flows/" in t} == {
        onboard, with_inputs
    }

    # Placement metadata anchors what it names, and nothing it merely grants.
    anchored = {t for _, t in graph.out_edges("framework:salesforce")}
    assert anchored == {
        f"{lwc}/onPage/onPage.js",
        f"{lwc}/onTab/onTab.js",
        f"{lwc}/onAction/onAction.js",
        f"{lwc}/onSite/onSite.js",
        f"{cls}/InboundHandler.cls",
        child,
    }
    assert graph.nodes[f"{lwc}/onPage/onPage.js"]["is_entry_point"] is True

    unreachable = {p for kind, p in dead if kind == "unreachable_file"}
    assert f"{lwc}/orphan/orphan.js" in unreachable
    for live in ("pickerLwc/pickerLwc.js", "onPage/onPage.js", "onTab/onTab.js", "onSite/onSite.js"):
        assert f"{lwc}/{live}" not in unreachable
    # Flows are placed by the org and never reported.
    assert not {p for _, p in dead if "/flows/" in p}


def test_a_legacy_flow_routes_by_directory_and_js_flow_does_not(tmp_path: Path) -> None:
    repo = _write(tmp_path, {
        "src/flows/Old_Flow-2.flow": _flow("    <processType>Flow</processType>\n"),
        "lib/types/index.js.flow": "// @flow\nexport type T = string;\n",
        "src/flows/types.js.flow": "// @flow\nexport type T = string;\n",
    })  # fmt: skip
    langs = {fi.path: fi.language for fi in FileTraverser(repo).traverse()}
    assert langs == {"src/flows/Old_Flow-2.flow": "salesforce_flow"}
    graph, _ = _graph(repo)
    assert "src/flows/Old_Flow-2.flow::Old_Flow" in graph


def _action_flow(name: str) -> str:
    return _flow(
        f"    <actionCalls><name>A</name><actionName>{name}</actionName>"
        "<actionType>apex</actionType></actionCalls>\n"
    )


_ACTION_CLS = "public class {} {{ @InvocableMethod public static void go(List<Id> x) {{}} }}\n"
_PROJECT = '{"packageDirectories": [{"path": "force-app"}]}\n'


def test_a_flow_binds_its_own_projects_copy_of_a_name_two_projects_declare(tmp_path: Path) -> None:
    repo = _write(tmp_path, {
        "one/sfdx-project.json": _PROJECT,
        "one/force-app/flows/F.flow-meta.xml": _action_flow("Shared"),
        "one/force-app/classes/Shared.cls": _ACTION_CLS.format("Shared"),
        "two/sfdx-project.json": _PROJECT,
        "two/force-app/classes/Shared.cls": _ACTION_CLS.format("Shared"),
        "three/sfdx-project.json": _PROJECT,
        "three/force-app/flows/G.flow-meta.xml": _action_flow("Shared"),
    })  # fmt: skip
    graph, _ = _graph(repo)
    assert graph.has_edge("one/force-app/flows/F.flow-meta.xml", "one/force-app/classes/Shared.cls")
    assert not graph.has_edge("one/force-app/flows/F.flow-meta.xml", "two/force-app/classes/Shared.cls")
    # Two other projects declare it and neither is a dependency: no edge.
    assert not any(
        t.endswith("Shared.cls") for _, t in graph.out_edges("three/force-app/flows/G.flow-meta.xml")
    )


def test_a_flow_reaches_a_class_only_one_other_project_declares(tmp_path: Path) -> None:
    repo = _write(tmp_path, {
        "app/sfdx-project.json": _PROJECT,
        "app/force-app/flows/F.flow-meta.xml": _action_flow("PostToTeams"),
        "teams/sfdx-project.json": _PROJECT,
        "teams/force-app/classes/PostToTeams.cls": _ACTION_CLS.format("PostToTeams"),
        "ns/sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "zz"}\n',
        "ns/force-app/classes/Prefixed.cls": _ACTION_CLS.format("Prefixed"),
        "app/force-app/flows/G.flow-meta.xml": _action_flow("Prefixed"),
    })  # fmt: skip
    graph, _ = _graph(repo)
    assert graph.has_edge("app/force-app/flows/F.flow-meta.xml", "teams/force-app/classes/PostToTeams.cls")
    # A namespaced project's class is reached only through its prefix.
    assert not graph.has_edge("app/force-app/flows/G.flow-meta.xml", "ns/force-app/classes/Prefixed.cls")


def test_a_flow_prefix_from_cumulusci_yml_is_the_repositorys_own(tmp_path: Path) -> None:
    repo = _write(tmp_path, {
        "sfdx-project.json": _PROJECT,
        "cumulusci.yml": "project:\n    package:\n        namespace: npsp\n",
        "force-app/flows/F.flow-meta.xml": _action_flow("npsp__Mailer"),
        "force-app/classes/Mailer.cls": _ACTION_CLS.format("Mailer"),
    })  # fmt: skip
    graph, _ = _graph(repo)
    assert graph.has_edge("force-app/flows/F.flow-meta.xml", "force-app/classes/Mailer.cls")


def _trigger_flow(obj: str) -> str:
    return _flow(
        f"    <start><object>{obj}</object><triggerType>RecordAfterSave</triggerType></start>\n"
    )


def test_a_record_triggered_flow_references_its_object(tmp_path: Path) -> None:
    obj = f'<?xml version="1.0"?>\n<CustomObject {_NS}>\n    <label>L</label>\n</CustomObject>\n'
    repo = _write(tmp_path, {
        "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}\n',
        f"{_D}/objects/Job__c/Job__c.object-meta.xml": obj,
        f"{_D}/flows/On_Job.flow-meta.xml": _trigger_flow("acme__JOB__c"),
        f"{_D}/flows/On_Case.flow-meta.xml": _trigger_flow("Case"),
    })  # fmt: skip
    graph, _ = _graph(repo)
    flow, target = f"{_D}/flows/On_Job.flow-meta.xml", f"{_D}/objects/Job__c/Job__c.object-meta.xml"
    assert graph.get_edge_data(f"{flow}::On_Job", f"{target}::Job__c")["edge_type"] == "references"
    assert graph.has_edge(flow, target)
    # No metadata for the object: nothing to bind.
    assert not list(graph.successors(f"{_D}/flows/On_Case.flow-meta.xml::On_Case"))
