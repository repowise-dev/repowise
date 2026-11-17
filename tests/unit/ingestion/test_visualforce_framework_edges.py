"""Salesforce Visualforce: page and component edges to Apex and to each other.

End to end through traverse -> parse -> graph -> framework edges, the path a
real index takes.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder

_D = "force-app/main/default"
_PAGE = f"{_D}/pages/AccountEdit.page"
_CMP = f"{_D}/components/AddressBlock.component"
_CTRL = f"{_D}/classes/AccountEditCtrl.cls"
_BASE = f"{_D}/classes/BaseCtrl.cls"
_EXT = f"{_D}/classes/AuditExt.cls"
_CMP_CTRL = f"{_D}/classes/AddressBlockCtrl.cls"

_REPO = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "force-app"}], "namespace": "acme"}',
    _PAGE: (
        '<apex:page Controller="AccountEditCtrl" extensions="acme.AuditExt, other.Foreign"'
        ' action="{!init}">\n'
        "  <!-- {!commentedOut} <c:Retired/> -->\n"
        "  <apex:form>\n"
        '    <apex:inputText value="{!searchTerm}"/>\n'
        '    <apex:outputText value="{!IF(NOT(isAdmin), $Label.Hello, title)}"/>\n'
        '    <apex:outputField value="{!acc.Name}"/>\n'
        '    <apex:repeat value="{!rows}" var="row">{!row.Name} {!auditCount}</apex:repeat>\n'
        '    <apex:commandButton action="{!save}" value="{!baseLabel}"/>\n'
        '    <apex:selectList value="{!pick}"><apex:selectOptions value="{!items}"/></apex:selectList>\n'
        "    <apex:outputText value=\"{!LEN('missing')} {!notAMember}\"/>\n"
        "  </apex:form>\n"
        '  <c:addressBlock record="{!acc}"/>\n'
        "  <acme:AddressBlock/>\n"
        "  <other:AddressBlock/>\n"
        '  <apex:include pageName="acme__Footer"/>\n'
        '  <a href="{!URLFOR($Page.Help)}">help</a>\n'
        "  <script>var x = '{!JSENCODE(secret)}';</script>\n"
        "</apex:page>\n"
    ),
    f"{_D}/pages/Footer.page": "<apex:page>footer</apex:page>\n",
    f"{_D}/pages/Help.page": "<apex:page>help</apex:page>\n",
    f"{_D}/pages/Orphan.page": '<apex:page standardController="Account">{!Account.Name}</apex:page>\n',
    _CMP: (
        '<apex:component controller="AddressBlockCtrl">\n'
        '  <apex:attribute name="record" type="Account" assignTo="{!rec}" description="x"/>\n'
        '  <apex:attribute name="title" type="String" assignTo="{!title}"/>\n'
        "  {!record.Name} {!street}\n"
        "</apex:component>\n"
    ),
    _CTRL: (
        "public with sharing class AccountEditCtrl extends BaseCtrl {\n"
        "    public AccountEditCtrl() {}\n"
        "    public String searchTerm { get; set; }\n"
        "    public Boolean isAdmin { get; private set; }\n"
        "    public Account acc { get; set; }\n"
        "    private String secret;\n"
        "    public String getTitle() { return ''; }\n"
        "    public List<Account> getRows() { return null; }\n"
        "    public PageReference init() { return null; }\n"
        "    public PageReference save() { return null; }\n"
        "    public String getPick() { return null; }\n"
        "    public void setPick(String p) {}\n"
        "    public List<SelectOption> getItems() { return null; }\n"
        "    public void setItems(List<SelectOption> s) {}\n"
        "}\n"
    ),
    _BASE: "public virtual class BaseCtrl {\n    public String baseLabel { get; set; }\n}\n",
    _EXT: (
        "public class AuditExt {\n"
        "    public AuditExt() {}\n"
        "    public AuditExt(AccountEditCtrl c) {}\n"
        "    public Integer auditCount { get; set; }\n"
        "    public String searchTerm { get; set; }\n"
        "}\n"
    ),
    _CMP_CTRL: (
        "public class AddressBlockCtrl {\n"
        "    public Account rec { get; set; }\n"
        "    public String title { get; set; }\n"
        "    public String getStreet() { return ''; }\n"
        "}\n"
    ),
    f"{_D}/classes/Nav.cls": (
        "public class Nav {\n"
        "    // Page.Orphan in a comment\n"
        "    public PageReference go() { return Page.ACCOUNTEDIT; }\n"
        "    public PageReference again() { return new PageReference('/apex/AccountEdit'); }\n"
        "    public PageReference url() { return new PageReference('/apex/Orphan?id=1'); }\n"
        "    public String s() { return 'Page.Help'; }\n"
        "    public PageReference built(String n) { return new PageReference('/apex/' + n); }\n"
        "}\n"
    ),
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
    return builder


def _edges(graph, edge_type: str) -> set[tuple[str, str]]:
    return {(s, t) for s, t, d in graph.edges(data=True) if d.get("edge_type") == edge_type}


def _bound(graph, src: str) -> set[str]:
    """``Class::member`` names bound from *src*'s markup."""
    return {
        t.split("::", 1)[1].split("#")[0]
        for s, t in _edges(graph, "framework_binds")
        if s == f"{src}::__module__"
    }


def test_pages_and_components_are_visualforce(tmp_path: Path) -> None:
    builder = _build(_write(tmp_path, _REPO))
    langs = {p: pf.file_info.language for p, pf in builder._parsed_files.items()}
    assert langs[_PAGE] == "visualforce"
    assert langs[_CMP] == "visualforce"


def test_controller_and_extension_edges(tmp_path: Path) -> None:
    g = _build(_write(tmp_path, _REPO)).graph()
    framework = _edges(g, "framework")
    assert {(_PAGE, _CTRL), (_PAGE, _EXT), (_PAGE, _BASE), (_CMP, _CMP_CTRL)} <= framework
    assert g.nodes[_PAGE].get("is_entry_point") is True
    assert not g.nodes[_CMP].get("is_entry_point")


def test_expression_members_bind_in_lookup_order(tmp_path: Path) -> None:
    g = _build(_write(tmp_path, _REPO)).graph()
    bound = _bound(g, _PAGE)
    assert {
        "AccountEditCtrl::AccountEditCtrl",  # constructed by the platform
        "AuditExt::AuditExt",
        "AccountEditCtrl::init",
        "AccountEditCtrl::searchTerm",  # controller wins over the extension
        "AccountEditCtrl::isAdmin",
        "AccountEditCtrl::getTitle",
        "AccountEditCtrl::acc",
        "AccountEditCtrl::getRows",
        "AccountEditCtrl::save",
        "AccountEditCtrl::getPick",
        "AccountEditCtrl::setPick",  # input value: written back through the setter
        "AccountEditCtrl::getItems",
        "AuditExt::auditCount",
        "BaseCtrl::baseLabel",  # inherited through extends
    } <= bound
    # An extension is built with the page's controller, never with no argument.
    ext_ctors = {
        t for s, t in _edges(g, "framework_binds") if t.startswith(f"{_EXT}::AuditExt::AuditExt")
    }
    assert len(ext_ctors) == 1 and "AccountEditCtrl" in g.nodes[ext_ctors.pop()]["signature"]
    assert "AuditExt::searchTerm" not in bound
    assert "AccountEditCtrl::setItems" not in bound  # selectOptions only reads
    assert "AccountEditCtrl::secret" not in bound  # private
    assert g.edges[_PAGE, _CTRL]["imported_names"][:2] == ["AccountEditCtrl", "acc"]


def test_component_attributes_and_assign_to(tmp_path: Path) -> None:
    g = _build(_write(tmp_path, _REPO)).graph()
    # `record` is the component's own attribute; `title` is too, but its
    # assignTo writes the controller property of the same name.
    assert _bound(g, _CMP) == {
        "AddressBlockCtrl::rec",
        "AddressBlockCtrl::getStreet",
        "AddressBlockCtrl::title",
    }


def test_component_tags_and_page_references(tmp_path: Path) -> None:
    g = _build(_write(tmp_path, _REPO)).graph()
    framework = _edges(g, "framework")
    assert (_PAGE, _CMP) in framework  # <c:addressBlock>, case folded; <acme:...> too
    assert (_PAGE, f"{_D}/pages/Footer.page") in framework  # own ns__ prefix dropped
    assert (_PAGE, f"{_D}/pages/Help.page") in framework  # $Page.Help
    nav = f"{_D}/classes/Nav.cls"
    # Page.ACCOUNTEDIT; the literal to the same page keeps the compile-checked edge.
    assert (nav, _PAGE) in framework
    assert (nav, _PAGE) not in _edges(g, "dynamic_uses")
    assert (nav, f"{_D}/pages/Help.page") not in framework  # a string, not code
    assert (nav, f"{_D}/pages/Orphan.page") in _edges(g, "dynamic_uses")
    assert g.edges[nav, f"{_D}/pages/Orphan.page"]["hint_source"] == "vf_page_reference"
    assert not any(t.endswith("Retired.component") for _, t in framework)


_NEG = {
    "sfdx-project.json": '{"packageDirectories": [{"path": "a"}, {"path": "b"}], "namespace": "acme"}',
    "a/pages/Dup.page": "<apex:page/>\n",
    "b/pages/Dup.page": "<apex:page/>\n",
    "a/pages/Report_.page": "<apex:page/>\n",
    "a/pages/getParameters.page": "<apex:page/>\n",
    "a/pages/Main.page": (
        '<apex:page controller="MainCtrl">\n'
        '  <apex:include pageName="other__Report_"/>\n'
        "  {!hidden} {!guarded} {!writeOnly}\n"
        '  <apex:inputText value="{!writeOnly}"/>\n'
        "</apex:page>\n"
    ),
    # Not Visualforce markup: no root tag, so not an entry point.
    "a/pages/Notes.page": "plain text\n",
    "a/classes/MainCtrl.cls": (
        "public class MainCtrl {\n"
        "    protected String hidden { get; set; }\n"
        "    private String getGuarded() { return ''; }\n"
        "    public void setWriteOnly(String s) {}\n"
        "}\n"
    ),
    "c/classes/Nav.cls": (
        "public class Nav {\n"
        "    public void go(PageReference page, String kind) {\n"
        "        page.getParameters();\n"
        "        PageReference a = Page.Dup;\n"
        "        PageReference b = new PageReference('/apex/Report_' + kind);\n"
        "    }\n"
        "}\n"
    ),
}


def test_refusals(tmp_path: Path) -> None:
    g = _build(_write(tmp_path, _NEG)).graph()
    nav = "c/classes/Nav.cls"
    targets = {t for s, t in g.out_edges(nav) if t.endswith(".page")}
    # Two equally near Dup pages, a method call on a local named `page`, and
    # a concatenated literal that is only a prefix of a page name.
    assert targets == set()
    main = "a/pages/Main.page"
    assert not any(t.endswith("Report_.page") for _, t in g.out_edges(main))  # foreign ns
    # Protected and private members never bind; a setter alone binds for an input.
    assert _bound(g, main) == {"MainCtrl::setWriteOnly"}
    assert g.nodes[main].get("is_entry_point") is True
    assert not g.nodes["a/pages/Notes.page"].get("is_entry_point")


def test_a_class_named_page_shadows_page_references(tmp_path: Path) -> None:
    files = {
        "pages/Foo.page": "<apex:page/>\n",
        "classes/Page.cls": "public class Page { public static String Foo = ''; }\n",
        "classes/Use.cls": "public class Use { String s = Page.Foo; }\n",
    }
    g = _build(_write(tmp_path, files)).graph()
    assert not g.has_edge("classes/Use.cls", "pages/Foo.page")
