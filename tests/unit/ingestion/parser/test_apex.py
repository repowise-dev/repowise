"""Unit tests for the Apex language pipeline.

Tests parse inline byte strings so no filesystem I/O is needed. Covers
symbols and kinds, inner classes, triggers, properties, annotations as
decorators, visibility, heritage, calls versus type references, the CumulusCI
namespace-token sanitiser, non-ASCII source and case variants.
"""

from __future__ import annotations

from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.sfc_source import prepare_source
from tests.unit.ingestion.parser._helpers import _make_file_info

CLASS_SOURCE = b"""\
/**
 * Account operations.
 */
@IsTest
public with sharing class AccountService extends BaseService implements Queueable, Database.Batchable<SObject> {
    public String status { get; set; }
    private Integer hidden;
    @SuppressWarnings('PMD.AvoidGlobalModifier')
    Integer count { get; private set; }

    public AccountService() { this(1); }
    public AccountService(Integer size) { super(size); }

    /** Find accounts. */
    @AuraEnabled(cacheable=true)
    global static List<Account> find(String name, Outer.Inner opt) {
        AccountSelector selector = new AccountSelector();
        selector.byName(name);
        AccountSelector.log(name);
        new Wrapper.Builder().build();
        Outer.Inner.staticCall();
        rows.items.size();
        System.debug(name);
        this.helper();
        helper();
        insert new Account(Name = name);
        return [SELECT Id FROM Account WHERE Name = :name];
    }

    void helper() {}
    webservice static String ws() { return ''; }

    public class Wrapper {
        public void build() {}
    }

    public interface Notifier extends IBase {
        void send(String message);
    }

    public enum Mode { SYNC, ASYNC }
}
"""

TRIGGER_SOURCE = b"""\
trigger AccountTrigger on Account (before insert, after update) {
    AccountTriggerHandler.run(Trigger.new);
}
"""


def _parse(parser: ASTParser, source: bytes, path: str = "classes/AccountService.cls"):
    return parser.parse_file(_make_file_info(path, "apex"), source)


def _by_name(result) -> dict:
    """Symbols by name; a class wins over its same-named constructors."""
    return {s.name: s for s in sorted(result.symbols, key=lambda s: s.kind == "class")}


class TestApexSymbols:
    def test_kinds(self, parser: ASTParser) -> None:
        kinds = {(s.name, s.kind) for s in _parse(parser, CLASS_SOURCE).symbols}
        assert {
            ("AccountService", "class"),
            ("find", "method"),
            ("Wrapper", "class"),
            ("build", "method"),
            ("Notifier", "interface"),
            ("send", "method"),
            ("Mode", "enum"),
            ("status", "variable"),
            ("count", "variable"),
        } <= kinds

    def test_plain_field_is_not_a_symbol(self, parser: ASTParser) -> None:
        assert "hidden" not in _by_name(_parse(parser, CLASS_SOURCE))

    def test_constructor_overloads_get_their_own_ids(self, parser: ASTParser) -> None:
        ctors = [s for s in _parse(parser, CLASS_SOURCE).symbols if s.name == "AccountService"]
        ids = {s.id for s in ctors if s.kind != "class"}
        assert len(ids) == 2

    def test_inner_class_members_name_their_class(self, parser: ASTParser) -> None:
        symbols = _by_name(_parse(parser, CLASS_SOURCE))
        assert symbols["Wrapper"].parent_name == "AccountService"
        assert symbols["build"].parent_name == "Wrapper"
        assert symbols["send"].parent_name == "Notifier"

    def test_annotations_are_decorators(self, parser: ASTParser) -> None:
        symbols = _by_name(_parse(parser, CLASS_SOURCE))
        assert symbols["find"].decorators[0].startswith("@AuraEnabled(cacheable=true)")
        assert symbols["AccountService"].decorators[0].startswith("@IsTest")
        assert symbols["helper"].decorators == []

    def test_modifier_keywords_are_lowercased_without_annotations(self, parser: ASTParser) -> None:
        symbols = _by_name(_parse(parser, CLASS_SOURCE))
        assert symbols["find"].modifiers == ("global", "static")
        assert symbols["ws"].modifiers == ("webservice", "static")
        assert symbols["helper"].modifiers == ()
        # ``with sharing`` is not a single keyword, and annotations stay decorators.
        assert symbols["AccountService"].modifiers == ("public",)

    def test_apexdoc(self, parser: ASTParser) -> None:
        symbols = _by_name(_parse(parser, CLASS_SOURCE))
        assert symbols["AccountService"].docstring == "Account operations."
        assert symbols["find"].docstring == "Find accounts."

    def test_visibility(self, parser: ASTParser) -> None:
        symbols = _by_name(_parse(parser, CLASS_SOURCE))
        assert symbols["find"].visibility == "public"  # global
        assert symbols["ws"].visibility == "public"  # webservice
        assert symbols["helper"].visibility == "private"  # no keyword
        assert symbols["send"].visibility == "public"  # interface member
        # Annotation text naming ``Global`` is not an access keyword.
        assert symbols["count"].visibility == "private"

    def test_trigger(self, parser: ASTParser) -> None:
        result = _parse(parser, TRIGGER_SOURCE, "triggers/AccountTrigger.trigger")
        (trigger,) = result.symbols
        assert (trigger.name, trigger.kind, trigger.visibility) == (
            "AccountTrigger",
            "function",
            "public",
        )
        assert trigger.signature == "trigger AccountTrigger on Account (before insert, after update)"
        (call,) = result.calls
        assert (call.receiver_name, call.target_name) == ("AccountTriggerHandler", "run")
        assert call.caller_symbol_id == trigger.id


class TestApexHeritage:
    def test_extends_and_implements(self, parser: ASTParser) -> None:
        rels = {(h.child_name, h.kind, h.parent_name) for h in _parse(parser, CLASS_SOURCE).heritage}
        assert ("AccountService", "extends", "BaseService") in rels
        # Platform interfaces stay: they mark runtime-invoked contract methods.
        assert ("AccountService", "implements", "Queueable") in rels
        assert ("AccountService", "implements", "Batchable") in rels
        assert ("Notifier", "extends", "IBase") in rels

    def test_uppercase_keywords(self, parser: ASTParser) -> None:
        source = b"PUBLIC CLASS Foo EXTENDS Bar IMPLEMENTS Baz { }"
        rels = {(h.kind, h.parent_name) for h in _parse(parser, source, "Foo.cls").heritage}
        assert rels == {("extends", "Bar"), ("implements", "Baz")}


class TestApexCalls:
    def _calls(self, parser: ASTParser) -> set[tuple[str | None, str]]:
        return {(c.receiver_name, c.target_name) for c in _parse(parser, CLASS_SOURCE).calls}

    def test_member_static_and_constructor_calls(self, parser: ASTParser) -> None:
        calls = self._calls(parser)
        assert {
            (None, "AccountSelector"),
            ("selector", "byName"),
            ("AccountSelector", "log"),
            # ``new Wrapper.Builder().build()``: the type path is the receiver,
            # and the qualifier is the receiver of the inner class's construction.
            ("Wrapper.Builder", "build"),
            ("Wrapper", "Builder"),
            ("this", "helper"),
            (None, "helper"),
            ("System", "debug"),
        } <= calls

    def test_dotted_receivers_keep_their_path(self, parser: ASTParser) -> None:
        calls = self._calls(parser)
        assert ("Outer.Inner", "staticCall") in calls
        assert ("rows.items", "size") in calls
        # Never a receiver-less copy that would read as an own-class call.
        assert (None, "staticCall") not in calls
        assert (None, "size") not in calls
        assert (None, "build") not in calls

    def test_constructor_chaining_is_not_a_call(self, parser: ASTParser) -> None:
        # ``this(..)`` / ``super(..)`` make no call site, as in java.scm.
        source = b"public class A extends B { public A() { this(1); } public A(Integer x) { super(x); } }"
        assert _parse(parser, source, "A.cls").calls == []

    def test_soql_and_dml_are_not_calls(self, parser: ASTParser) -> None:
        targets = {t for _, t in self._calls(parser)}
        assert not targets & {"insert", "SELECT", "Account_insert"}

    def test_type_positions_are_type_refs_not_calls(self, parser: ASTParser) -> None:
        result = _parse(parser, CLASS_SOURCE)
        type_names = {t.type_name for t in result.type_refs}
        assert {"AccountSelector", "Inner", "BaseService"} <= type_names
        # Builtins are filtered whatever their case.
        assert not type_names & {"String", "List", "Integer"}
        targets = {c.target_name for c in result.calls}
        assert "Inner" not in targets and "BaseService" not in targets

    def test_lowercase_builtin_types_are_filtered(self, parser: ASTParser) -> None:
        source = b"public class Foo { list<string> names; void m(map<id, Bar> x) {} }"
        type_names = {t.type_name for t in _parse(parser, source, "Foo.cls").type_refs}
        assert type_names == {"Bar"}


class TestApexSource:
    def test_cumulusci_tokens_are_blanked_in_place(self) -> None:
        source = b"%%%NAMESPACE%%%Util.go(); x = %%%NAMESPACE_DOT%%%Foo__c.Name;"
        prepared = prepare_source("apex", source)
        assert len(prepared) == len(source)
        assert b"%%%" not in prepared
        assert prepared.index(b"Util.go") == source.index(b"Util.go")

    def test_namespaced_file_parses(self, parser: ASTParser) -> None:
        source = b"""\
public class Seed {
    public static void run() {
        %%%NAMESPACE%%%Util.go();
    }
}
"""
        result = _parse(parser, source, "scripts/Seed.cls")
        assert {s.name for s in result.symbols} == {"Seed", "run"}
        assert ("Util", "go") in {(c.receiver_name, c.target_name) for c in result.calls}

    def test_non_ascii_keeps_names_and_lines(self, parser: ASTParser) -> None:
        source = "public class CaféService {\n    /** Résumé ✓ */\n    public void m() { String s = 'éé'; helper(); }\n    void helper() {}\n}\n".encode()
        result = _parse(parser, source, "CaféService.cls")
        symbols = _by_name(result)
        assert "CaféService" in symbols
        assert symbols["m"].docstring == "Résumé ✓"
        assert [c.line for c in result.calls if c.target_name == "helper"] == [3]

    def test_case_variants_keep_the_declared_name(self, parser: ASTParser) -> None:
        source = b"PUBLIC CLASS MixedCase { PUBLIC STATIC VOID DoIt() { HELPER.RUN(); } }"
        result = _parse(parser, source, "MixedCase.cls")
        symbols = _by_name(result)
        assert symbols["DoIt"].visibility == "public"
        assert ("HELPER", "RUN") in {(c.receiver_name, c.target_name) for c in result.calls}
