"""Salesforce object/field metadata as symbols, and the references bound to them."""

from __future__ import annotations

import json
from pathlib import Path

import networkx as nx

from repowise.core.ingestion import ASTParser, FileTraverser, GraphBuilder
from repowise.core.ingestion.traverser import _detect_language, is_candidate_source_path

_NS = 'xmlns="http://soap.sforce.com/2006/04/metadata"'


def _object(label: str) -> str:
    return f'<?xml version="1.0"?>\n<CustomObject {_NS}>\n    <label>{label}</label>\n</CustomObject>\n'


def _field(kind: str, reference_to: str | None = None) -> str:
    ref = f"    <referenceTo>{reference_to}</referenceTo>\n" if reference_to else ""
    return (
        f'<?xml version="1.0"?>\n<CustomField {_NS}>\n'
        f"    <fullName>X</fullName>\n    <type>{kind}</type>\n{ref}</CustomField>\n"
    )


_MDAPI_OBJECT = f"""<?xml version="1.0"?>
<CustomObject {_NS}>
    <compactLayouts>
        <fields>Name</fields>
    </compactLayouts>
    <fields>
        <fullName>Job__c</fullName>
        <referenceTo>Job__c</referenceTo>
        <type>Lookup</type>
    </fields>
    <fields>
        <fullName>Hours__c</fullName>
        <type>Number</type>
    </fields>
    <label>Shift</label>
</CustomObject>
"""


def _build(tmp_path: Path, files: dict[str, str]) -> nx.DiGraph:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    parser = ASTParser()
    builder = GraphBuilder(repo_path=tmp_path)
    for fi in FileTraverser(tmp_path).traverse():
        builder.add_file(parser.parse_file(fi, Path(fi.abs_path).read_bytes()))
    return builder.build()


_ROOT = "force-app/main/default/objects"


def test_only_object_and_field_metadata_are_claimed() -> None:
    assert _detect_language(Path("objects/Account/Account.object-meta.xml")) == "salesforce_object"
    assert _detect_language(Path("objects/A/fields/B__c.Field-Meta.XML")) == "salesforce_object"
    assert _detect_language(Path("src/objects/Account.object")) == "salesforce_object"
    assert _detect_language(Path("classes/Foo.cls-meta.xml")) == "unknown"
    assert is_candidate_source_path("objects/A/fields/B__c.field-meta.xml")
    assert not is_candidate_source_path("classes/Foo.cls-meta.xml")


def test_object_and_field_symbols(tmp_path: Path) -> None:
    g = _build(
        tmp_path,
        {
            f"{_ROOT}/Job__c/Job__c.object-meta.xml": _object("Job"),
            f"{_ROOT}/Job__c/fields/Due__c.field-meta.xml": _field("Date"),
        },
    )
    obj = g.nodes[f"{_ROOT}/Job__c/Job__c.object-meta.xml::Job__c"]
    assert obj["kind"] == "class"
    field = g.nodes[f"{_ROOT}/Job__c/fields/Due__c.field-meta.xml::Due__c"]
    assert field["kind"] == "property"
    assert field["qualified_name"] == "Job__c.Due__c"


def test_lookup_field_references_its_object(tmp_path: Path) -> None:
    obj_file = f"{_ROOT}/Job__c/Job__c.object-meta.xml"
    field_file = f"{_ROOT}/Shift__c/fields/Job__c.field-meta.xml"
    g = _build(
        tmp_path,
        {
            obj_file: _object("Job"),
            field_file: _field("Lookup", "job__C"),  # names compare case-insensitively
            f"{_ROOT}/Shift__c/fields/Owner__c.field-meta.xml": _field("Lookup", "User"),
        },
    )
    edge = g.get_edge_data(f"{field_file}::Job__c", f"{obj_file}::Job__c")
    assert edge is not None and edge["edge_type"] == "references"
    assert g.get_edge_data(field_file, obj_file)["edge_type"] == "reads"
    # A standard object with no metadata in the repository: no edge, no guess.
    owner = f"{_ROOT}/Shift__c/fields/Owner__c.field-meta.xml::Owner__c"
    assert not [t for _, t, d in g.out_edges(owner, data=True) if d["edge_type"] == "references"]


def test_mdapi_object_keeps_fields_inline(tmp_path: Path) -> None:
    g = _build(
        tmp_path,
        {
            "src/objects/Shift__c.object": _MDAPI_OBJECT,
            "src/objects/Job__c.object": _object("Job"),
        },
    )
    path = "src/objects/Shift__c.object"
    fields = {
        g.nodes[n]["name"]: g.nodes[n]
        for n in g.nodes
        if n.startswith(path + "::") and g.nodes[n].get("kind") == "property"
    }
    # compactLayouts/fields is a column list, not a field definition.
    assert set(fields) == {"Job__c", "Hours__c"}
    assert fields["Job__c"]["start_line"] == 6
    edge = g.get_edge_data(f"{path}::Shift__c::Job__c", "src/objects/Job__c.object::Job__c")
    assert edge is not None and edge["edge_type"] == "references"


def test_package_directory_copy_wins_and_namespace_tokens_strip(tmp_path: Path) -> None:
    (tmp_path / "sfdx-project.json").write_text(
        json.dumps({"packageDirectories": [{"path": "force-app"}], "namespace": "acme"})
    )
    pkg_obj = f"{_ROOT}/Job__c/Job__c.object-meta.xml"
    field_file = f"{_ROOT}/Shift__c/fields/Job__c.field-meta.xml"
    g = _build(
        tmp_path,
        {
            pkg_obj: _object("Job"),
            "unpackaged/config/objects/___NAMESPACE___Job__c.object": _object("Job"),
            field_file: _field("Lookup", "acme__Job__c"),
        },
    )
    targets = {t for _, t, d in g.out_edges(f"{field_file}::Job__c", data=True)}
    assert targets == {f"{pkg_obj}::Job__c"}
    assert "unpackaged/config/objects/___NAMESPACE___Job__c.object::Job__c" in g


def test_copies_of_one_object_bind_one_canonical_copy(tmp_path: Path) -> None:
    (tmp_path / "sfdx-project.json").write_text(
        json.dumps(
            {"packageDirectories": [{"path": "pkg-a"}, {"path": "pkg-b", "default": True}]}
        )
    )
    account = "objects/Account/Account.object-meta.xml"
    contact = "objects/Contact.object"
    mdapi_contact = _object("Contact").replace(
        "</CustomObject>",
        "    <fields>\n        <fullName>Level__c</fullName>\n        <type>Text</type>\n"
        "    </fields>\n</CustomObject>",
    )
    lookup = "pkg-b/objects/Shift__c/fields/Who__c.field-meta.xml"
    # A packaged fragment of Pay__c with no referenceTo; the test-org copy carries it.
    pay = "pkg-b/objects/Shift__c/fields/Pay__c.field-meta.xml"
    cls = "pkg-b/classes/Q.cls"
    qa_contact = f"unpackaged/config/qa/{contact}"
    g = _build(
        tmp_path,
        {
            # Package copies in two dirs and a test-org copy: the default dir wins.
            f"pkg-a/{account}": _object("Account"),
            f"pkg-b/{account}": _object("Account"),
            f"unpackaged/config/dev/{account}": _object("Account"),
            # Only test-org copies: the shortest path wins.
            f"unpackaged/config/dev/{contact}": mdapi_contact,
            qa_contact: mdapi_contact,
            lookup: _field("Lookup", "Contact"),
            pay: _field("Lookup"),
            "unpackaged/config/qa/objects/Shift__c/fields/Pay__c.field-meta.xml": _field(
                "Lookup", "Contact"
            ),
            cls: "public class Q {\n  void m() {\n    List<Account> a = [SELECT Id FROM Account];"
            "\n    List<Contact> c = [SELECT Level__c FROM Contact];"
            "\n    List<Shift__c> s = [SELECT Who__r.Level__c FROM Shift__c];"
            "\n    List<Shift__c> p = [SELECT Pay__r.Level__c FROM Shift__c];\n  }\n}\n",
        },
    )

    def refs(source: str) -> set[str]:
        return {t for _, t, d in g.out_edges(source, data=True) if d["edge_type"] == "references"}

    assert refs(f"{cls}::Q::m") == {
        f"pkg-b/{account}::Account",
        f"{qa_contact}::Contact",
        f"{qa_contact}::Contact::Level__c",
        f"{lookup}::Who__c",
        f"{pay}::Pay__c",
    }
    assert refs(f"{lookup}::Who__c") == {f"{qa_contact}::Contact"}


def test_malformed_metadata_yields_no_symbols(tmp_path: Path) -> None:
    g = _build(tmp_path, {f"{_ROOT}/Bad__c/Bad__c.object-meta.xml": "<CustomObject><label>"})
    assert f"{_ROOT}/Bad__c/Bad__c.object-meta.xml" in g
    assert f"{_ROOT}/Bad__c/Bad__c.object-meta.xml::Bad__c" not in g


def test_soql_file_references_object_and_fields(tmp_path: Path) -> None:
    obj_file = f"{_ROOT}/Job__c/Job__c.object-meta.xml"
    field_file = f"{_ROOT}/Job__c/fields/Due__c.field-meta.xml"
    g = _build(
        tmp_path,
        {
            obj_file: _object("Job"),
            field_file: _field("Date"),
            "scripts/soql/jobs.soql": "// saved query\nSELECT Id, due__c FROM Job__c\n",
        },
    )
    module = "scripts/soql/jobs.soql::__module__"
    targets = {t for _, t, d in g.out_edges(module, data=True) if d["edge_type"] == "references"}
    assert targets == {f"{obj_file}::Job__c", f"{field_file}::Due__c"}
    assert g.get_edge_data("scripts/soql/jobs.soql", obj_file)["edge_type"] == "reads"


_APEX = """public with sharing class JobService {
    private List<Job__c> pending;
    public static List<Job__c> open(Set<Id> ids) {
        return [SELECT Id, due__c, Shift__r.Hours__c, (SELECT Id FROM Shifts__r)
                FROM job__c j WHERE j.Due__c != null AND Id IN :ids];
    }
    public void save(Job__c job) {
        insert job;
        update this.pending;
        Database.query('SELECT Id FROM Shift__c');
        Database.query('SELECT Id FROM ' + job.Name);
        Account a = new Account();
        insert a;
    }
}
"""


def test_apex_soql_dml_and_trigger_reference_schema_symbols(tmp_path: Path) -> None:
    job = f"{_ROOT}/Job__c/Job__c.object-meta.xml"
    due = f"{_ROOT}/Job__c/fields/Due__c.field-meta.xml"
    shift_lookup = f"{_ROOT}/Job__c/fields/Shift__c.field-meta.xml"
    shift = f"{_ROOT}/Shift__c/Shift__c.object-meta.xml"
    hours = f"{_ROOT}/Shift__c/fields/Hours__c.field-meta.xml"
    cls = "force-app/main/default/classes/JobService.cls"
    trigger = "force-app/main/default/triggers/JobTrigger.trigger"
    g = _build(
        tmp_path,
        {
            job: _object("Job"),
            due: _field("Date"),
            shift_lookup: _field("Lookup", "Shift__c"),
            shift: _object("Shift"),
            hours: _field("Number"),
            cls: _APEX,
            trigger: "trigger JobTrigger on JOB__C (before insert) { }\n",
        },
    )

    def refs(source: str) -> set[str]:
        return {
            t for _, t, d in g.out_edges(source, data=True) if d["edge_type"] == "references"
        }

    # FROM object, selected and filtered fields, and a lookup hop; the child
    # relationship subquery (Shifts__r) names no object.
    assert refs(f"{cls}::JobService::open") == {
        f"{job}::Job__c",
        f"{due}::Due__c",
        f"{shift_lookup}::Shift__c",
        f"{hours}::Hours__c",
    }
    # DML on a parameter and a field, and a literal Database.query; the
    # concatenated query and the standard Account (no metadata) bind nothing.
    assert refs(f"{cls}::JobService::save") == {f"{job}::Job__c", f"{shift}::Shift__c"}
    assert refs(f"{trigger}::JobTrigger") == {f"{job}::Job__c"}
    assert g.get_edge_data(cls, job)["edge_type"] == "reads"


def test_dml_target_type_respects_block_scope() -> None:
    from tree_sitter import Parser
    from tree_sitter_language_pack import get_language

    from repowise.core.ingestion.salesforce_soql import apex_schema_refs

    source = b"""class T {
    void m(Boolean flag) {
        if (flag) { Contact c = new Contact(); insert c; }
        else { Lead c = new Lead(); insert c; }
        update c;
        insert new Case[]{ new Case() };
    }
}
"""
    root = Parser(get_language("apex")).parse(source).root_node
    refs = apex_schema_refs(root, [], "T.cls")
    # Each branch's insert sees its own declaration; the update after both
    # blocks sees neither, so it names nothing rather than guessing. An array
    # literal's element type is the target.
    assert sorted((r.line, r.object_name) for r in refs) == [(3, "Contact"), (4, "Lead"), (6, "Case")]
