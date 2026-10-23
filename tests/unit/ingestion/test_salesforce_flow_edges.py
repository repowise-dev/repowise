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
    "<fields><name>H</name><extensionName>flowruntime:fileUpload</extensionName></fields>"
    "</screens>\n"
    "    <start><object>Account</object><triggerType>RecordAfterSave</triggerType></start>\n"
    "    <variables><name>w</name><dataType>Apex</dataType><apexClass>Wrapper</apexClass></variables>\n"
)

_APP = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}\n',
    f"{_D}/flows/Main_Flow.flow-meta.xml": _MAIN_FLOW,
    f"{_D}/flows/Child_Flow.flow-meta.xml": _flow("    <processType>AutoLaunchedFlow</processType>\n"),
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
    f"{_D}/flexipages/Home.flexipage-meta.xml": (
        f"<FlexiPage {_NS}><flexiPageRegions><itemInstances><componentInstance>"
        "<componentName>acme:onPage</componentName></componentInstance>"
        "<componentInstance><componentName>force:detailPanel</componentName></componentInstance>"
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
    assert refs.components == ["c:pickerLwc", "c:legacyAura", "flowruntime:fileUpload"]
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

    # Apex names flows by static type and by literal; a comment does not count.
    launcher = f"{cls}/Launcher.cls"
    assert edge(f"{launcher}::Launcher::run", f"{child}::Child_Flow") == "framework_binds"
    assert edge(f"{launcher}::Launcher::run", f"{main}::Main_Flow") == "framework_binds"
    # LWC <lightning-flow>, literal names only.
    assert edge(f"{lwc}/host/host.html", child) == "framework"
    targets = {t for _, t in graph.out_edges(f"{lwc}/host/host.html")}
    assert main not in targets

    # Placement metadata anchors what it names, and nothing it merely grants.
    anchored = {t for _, t in graph.out_edges("framework:salesforce")}
    assert anchored == {
        f"{lwc}/onPage/onPage.js",
        f"{lwc}/onTab/onTab.js",
        f"{lwc}/onAction/onAction.js",
        f"{lwc}/onSite/onSite.js",
        f"{cls}/InboundHandler.cls",
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


def test_a_name_in_two_projects_binds_to_the_nearest_and_refuses_a_tie(tmp_path: Path) -> None:
    flow = _flow(
        "    <actionCalls><name>A</name><actionName>Shared</actionName>"
        "<actionType>apex</actionType></actionCalls>\n"
    )
    cls = "public class Shared { @InvocableMethod public static void go(List<Id> x) {} }\n"
    repo = _write(tmp_path, {
        "one/force-app/flows/F.flow-meta.xml": flow,
        "one/force-app/classes/Shared.cls": cls,
        "two/force-app/classes/Shared.cls": cls,
        "top/flows/G.flow-meta.xml": flow,
    })  # fmt: skip
    graph, _ = _graph(repo)
    assert graph.has_edge("one/force-app/flows/F.flow-meta.xml", "one/force-app/classes/Shared.cls")
    assert not graph.has_edge("one/force-app/flows/F.flow-meta.xml", "two/force-app/classes/Shared.cls")
    assert not any(
        t.endswith("Shared.cls") for _, t in graph.out_edges("top/flows/G.flow-meta.xml")
    )
