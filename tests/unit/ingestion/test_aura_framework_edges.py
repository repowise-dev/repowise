"""Salesforce Aura bundles: markup routing, handler symbols and bundle edges.

End to end through traverse -> parse -> graph -> framework edges -> dead code,
the path a real index takes.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code import DeadCodeAnalyzer
from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_A = "force-app/main/default/aura"

_REPO = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}',
    # A tab-placeable component with a controller, helper and renderer.
    f"{_A}/accountList/accountList.cmp": (
        '<aura:component implements="force:appHostable,c:selectable" extends="c:base">\n'
        '  <aura:registerEvent name="picked" type="c:accountPicked"/>\n'
        '  <aura:handler name="init" value="{!this}" action="{!c.doInit}"/>\n'
        '  <!-- <c:retired/> {!c.commentedOut} -->\n'
        '  <c:accountRow onclick="{!c.onRow}"/>\n'
        '  <acme:accountTile/>\n'
        '  <lightning:button onclick="{!c.missing}"/>\n'
        '  <other:thing/>\n'
        "</aura:component>\n"
    ),
    f"{_A}/accountList/accountListController.js": (
        "({\n"
        "    doInit : function(component, event, helper) {\n"
        "        helper.load(component);\n"
        "        helper.shared(component);\n"
        '        component.find("in").addHandler("keyup", component, "c.onKey");\n'
        "    },\n"
        "    onRow : function(cmp, evt, helper) {\n"
        '        var x = cmp.getReference("c.unbound");\n'
        "        // helper.notCalled(cmp);\n"
        '        $A.createComponent("c:accountTile", {}, function() {});\n'
        "    },\n"
        "    unbound : function(cmp) {},\n"
        "    onKey : function(cmp) {},\n"
        "    pick : function(cmp) {},\n"
        "    orphan : function(cmp) {},\n"
        "})\n"
    ),
    f"{_A}/accountList/accountListHelper.js": (
        "({\n"
        "    load : function(component) {\n"
        '        var action = component.get("c.getAccounts");\n'
        '        var client = component.get("c.unbound");\n'
        '        $A.createComponents([["c:accountRow", {}], ["lightning:card", {}]], function() {});\n'
        "    },\n"
        "    notCalled : function(component) {},\n"
        "})\n"
    ),
    f"{_A}/accountList/accountListRenderer.js": (
        "({\n"
        "    afterRender : function(cmp, helper) { this.superAfterRender(); },\n"
        "})\n"
    ),
    f"{_A}/base/base.cmp": (
        '<aura:component extensible="true" abstract="true" controller="acme.AccountCtrl">'
        "{!v.body}</aura:component>\n"
    ),
    "force-app/main/default/classes/AccountCtrl.cls": (
        "public with sharing class AccountCtrl {\n"
        "    @AuraEnabled public static List<Account> getAccounts() { return null; }\n"
        "    private static void getAccounts(Integer n) {}\n"
        "}\n"
    ),
    # Same name in another project: the nearer copy wins.
    "other-app/classes/AccountCtrl.cls": "public class AccountCtrl {}\n",
    # A sub-component inherits its parent's helper.
    f"{_A}/base/baseHelper.js": (
        "({\n    shared : function(component) { this.inner(component); },\n"
        "    inner : function(component) {},\n})\n"
    ),
    # An action-less aura:method runs the implementer's controller function of that name.
    f"{_A}/selectable/selectable.intf": '<aura:interface><aura:method name="pick"/></aura:interface>\n',
    f"{_A}/accountPicked/accountPicked.evt": '<aura:event type="COMPONENT"/>\n',
    f"{_A}/accountRow/accountRow.cmp": "<aura:component/>\n",
    f"{_A}/accountRow/accountRow.design": "<design:component/>\n",
    # A flow local action: the flow runtime runs its controller's `invoke`, and a
    # formatter has reflowed one handler expression across lines.
    f"{_A}/goNext/goNext.cmp": (
        '<aura:component implements="lightning:availableForFlowActions">\n'
        '  <lightning:button onclick="\n    {\n      !c.onClick;\n    }\n  "/>\n'
        "</aura:component>\n"
    ),
    f"{_A}/goNext/goNextController.js": (
        "({\n    invoke : function(cmp, event, helper) {},\n    onClick : function(cmp) {},\n})\n"
    ),
    f"{_A}/main/main.app": '<aura:application extends="force:slds"><c:accountList/></aura:application>\n',
    # An LWC bundle reached by an Aura tag, by its camelCase name.
    "force-app/main/default/lwc/accountTile/accountTile.js": (
        "import { LightningElement } from 'lwc';\nexport default class AccountTile extends LightningElement {}\n"
    ),
    # Generic suffixes outside an aura/<bundle>/ dir stay unclaimed.
    "grammar/Lexer.tokens": "ID=1\n",
    "aura/Loose.app": "<aura:application/>\n",
}


def _write(repo: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return repo


def _build(repo: Path):
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
    return builder, source_map


def _edges(graph, edge_type: str) -> set[tuple[str, str]]:
    return {(s, t) for s, t, d in graph.edges(data=True) if d.get("edge_type") == edge_type}


def test_markup_is_routed_by_bundle_dir_only(tmp_path: Path) -> None:
    builder, _ = _build(_write(tmp_path, _REPO))
    langs = {p: pf.file_info.language for p, pf in builder._parsed_files.items()}
    assert langs[f"{_A}/accountList/accountList.cmp"] == "aura"
    assert langs[f"{_A}/accountRow/accountRow.design"] == "aura"
    assert langs[f"{_A}/main/main.app"] == "aura"
    assert "grammar/Lexer.tokens" not in langs
    assert "aura/Loose.app" not in langs


def test_bundle_js_handlers_are_symbols(tmp_path: Path) -> None:
    builder, _ = _build(_write(tmp_path, _REPO))
    ctrl = builder._parsed_files[f"{_A}/accountList/accountListController.js"]
    assert [s.name for s in ctrl.symbols] == ["doInit", "onRow", "unbound", "onKey", "pick", "orphan"]
    assert all(s.kind == "function" for s in ctrl.symbols)


def test_bundle_edges(tmp_path: Path) -> None:
    builder, _ = _build(_write(tmp_path, _REPO))
    g = builder.graph()
    cmp_ = f"{_A}/accountList/accountList.cmp"
    ctrl = f"{_A}/accountList/accountListController.js"
    helper = f"{_A}/accountList/accountListHelper.js"
    renderer = f"{_A}/accountList/accountListRenderer.js"
    tile = "force-app/main/default/lwc/accountTile/accountTile.js"
    framework = _edges(g, "framework")
    assert {
        (cmp_, f"{_A}/base/base.cmp"),
        (cmp_, f"{_A}/selectable/selectable.intf"),
        (cmp_, f"{_A}/accountPicked/accountPicked.evt"),
        (cmp_, f"{_A}/accountRow/accountRow.cmp"),
        (cmp_, tile),  # own namespace from sfdx-project.json, LWC target
        (cmp_, ctrl),
        (cmp_, renderer),
        (ctrl, helper),
        (f"{_A}/main/main.app", cmp_),
    } <= framework
    assert g.edges[cmp_, ctrl]["imported_names"] == ["doInit", "onRow", "pick"]
    assert all(t != f"{_A}/retired/retired.cmp" for _, t in framework)
    assert (ctrl, f"{_A}/base/baseHelper.js") in framework
    binds = _edges(g, "framework_binds")
    assert {
        (f"{cmp_}::__module__", f"{ctrl}::doInit"),
        (f"{cmp_}::__module__", f"{ctrl}::onRow"),
        (f"{cmp_}::__module__", f"{renderer}::afterRender"),
        (f"{ctrl}::doInit", f"{helper}::load"),
        (f"{ctrl}::onRow", f"{ctrl}::unbound"),
        (f"{ctrl}::doInit", f"{ctrl}::onKey"),
        (f"{ctrl}::doInit", f"{_A}/base/baseHelper.js::shared"),
        (f"{cmp_}::__module__", f"{ctrl}::pick"),
        (f"{_A}/base/baseHelper.js::shared", f"{_A}/base/baseHelper.js::inner"),
    } <= binds
    assert (f"{ctrl}::onRow", f"{helper}::notCalled") not in binds
    cls = "force-app/main/default/classes/AccountCtrl.cls"
    assert (f"{_A}/base/base.cmp", cls) in framework
    assert g.edges[helper, cls]["imported_names"] == ["getAccounts"]
    getter = [t for s, t in binds if s == f"{helper}::load" and t.startswith(f"{cls}::")]
    assert getter == [f"{cls}::AccountCtrl::getAccounts#0"]
    # `get("c.x")` naming no Apex method but a controller function is a client action.
    assert (f"{helper}::load", f"{ctrl}::unbound") in binds
    go = f"{_A}/goNext/goNext"
    assert (f"{go}.cmp::__module__", f"{go}Controller.js::invoke") in binds
    assert (f"{go}.cmp::__module__", f"{go}Controller.js::onClick") in binds
    dynamic = _edges(g, "dynamic_uses")
    assert (ctrl, tile) in dynamic
    assert (helper, f"{_A}/accountRow/accountRow.cmp") in dynamic
    assert g.nodes[cmp_].get("is_entry_point") is True
    assert g.nodes[f"{_A}/main/main.app"].get("is_entry_point") is True
    assert not g.nodes[f"{_A}/base/base.cmp"].get("is_entry_point")


def test_dead_code_keeps_bound_handlers_and_reports_unbound_ones(tmp_path: Path) -> None:
    repo = _write(tmp_path, _REPO)
    builder, source_map = _build(repo)
    report = DeadCodeAnalyzer(
        builder.graph(), {}, parsed_files=builder._parsed_files, source_map=source_map, repo_root=repo
    ).analyze({"detect_unused_internals": True})
    dead = {(f.file_path, f.symbol_name or "") for f in report.findings}
    # A bundle's JS is loaded and read by the Lightning runtime: never deletion-ready.
    assert not any(f.safe_to_delete for f in report.findings if "/aura/" in f.file_path)
    ctrl = f"{_A}/accountList/accountListController.js"
    for live in (
        (ctrl, ""),
        (ctrl, "doInit"),
        (ctrl, "onRow"),
        (ctrl, "unbound"),
        (ctrl, "onKey"),
        (ctrl, "pick"),
        (f"{_A}/base/baseHelper.js", "shared"),
        (f"{_A}/base/baseHelper.js", "inner"),
        (f"{_A}/accountList/accountListHelper.js", "load"),
        (f"{_A}/accountList/accountListRenderer.js", "afterRender"),
    ):
        assert live not in dead
    assert not any(path.endswith((".cmp", ".app", ".evt", ".intf")) for path, _ in dead)
    assert (ctrl, "orphan") in dead
    assert (f"{_A}/accountList/accountListHelper.js", "notCalled") in dead


def test_change_sources_see_aura_markup() -> None:
    from repowise.core.ingestion.traverser import is_candidate_source_path

    assert is_candidate_source_path(f"{_A}/accountList/accountList.cmp")
    assert is_candidate_source_path(f"{_A}/main/main.app")
    assert not is_candidate_source_path("grammar/Lexer.tokens")


def test_only_aura_bundle_files_carry_the_aura_risk_factor() -> None:
    from repowise.core.analysis.dead_code.risk_factors import path_risk_factors

    assert path_risk_factors(f"{_A}/accountList/accountListHelper.js") == ("aura",)
    assert path_risk_factors("src/aura/util.js") == ()
