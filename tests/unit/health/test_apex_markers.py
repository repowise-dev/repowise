"""Apex health markers: SOQL injection, missing sharing, hardcoded Ids, SeeAllData.

Precision first: every firing shape has a paired quiet one, and the markers are
advisory, so no finding moves a score.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.analysis.health.apex_markers import (
    _DYNAMIC_QUERY,
    _HAS_DYNAMIC_QUERY,
    is_record_id,
)
from repowise.core.analysis.health.biomarkers.base import FileContext
from repowise.core.analysis.health.biomarkers.registry import detect_all
from repowise.core.analysis.health.complexity import walk_file
from repowise.core.analysis.health.scoring import biomarker_dimension, dimensions_for, score_file

_FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "lang_samples" / "apex"
_KINDS = ("apex_soql_injection", "apex_missing_sharing", "apex_hardcoded_id", "apex_see_all_data")


def _hits(src: str, name: str = "t.cls") -> list[tuple[str, str | None, str]]:
    fc = walk_file(name, "apex", src.encode())
    return sorted((h.kind, h.function, h.detail) for h in fc.perf_hits if h.kind in _KINDS)


def _fixture_hits(name: str) -> list[tuple[str, str | None, str]]:
    return _hits((_FIXTURES / name).read_text(encoding="utf-8"), name)


def _ctx(src: str, *, is_test: bool) -> FileContext:
    fc = walk_file("t.cls", "apex", src.encode())
    return FileContext(
        file_path="classes/t.cls", language="apex", nloc=10, has_test_file=False,
        module=None, is_test=is_test, perf_hits=fc.perf_hits,
    )


def test_fixture_hits():
    assert _fixture_hits("health_markers.cls") == sorted(
        [
            ("apex_soql_injection", "byName", "name"),
            ("apex_soql_injection", "byBuiltClause", "city"),
            ("apex_soql_injection", "bySearchProperty", "searchText"),
            ("apex_soql_injection", "byNames", "names"),
            ("apex_missing_sharing", "HealthMarkers", "line 13"),
            ("apex_hardcoded_id", "HealthMarkers", "0051t000002bF3QAAU"),
            ("apex_hardcoded_id", "defaultRecordType", "012000000000AbcAAE"),
        ]
    )


def test_see_all_data_fixture():
    assert _fixture_hits("HealthMarkersTest.cls") == [
        ("apex_see_all_data", "HealthMarkersTest", "class"),
        ("apex_see_all_data", "readsOrgData", "method"),
    ]


# -- apex_soql_injection --------------------------------------------------------

_INJECTION_QUIET = {
    "constant": "static final String N = 'x'; List<Account> m() { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + N + '\\''); }",
    "local from literal": "List<Account> m() { String n = 'x'; return Database.query('SELECT Id FROM Account WHERE Name = \\'' + n + '\\''); }",
    "escape wrapper": "List<Account> m(String n) { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + Util.escapeSoql(n) + '\\''); }",
    "number": "List<Account> m(Integer n) { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + n + '\\''); }",
    "outside quotes": "List<Account> m(String where) { return Database.query('SELECT Id FROM Account WHERE ' + where); }",
    "suppressed": "@SuppressWarnings('PMD.ApexSOQLInjection') List<Account> m(String n) { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + n + '\\''); }",
    "inline soql": "List<Account> m(String n) { return [SELECT Id FROM Account WHERE Name = :n]; }",
    "quotes stripped": "List<Account> m(String n) { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + n.replaceAll('[^a-z]', '') + '\\''); }",
    "suppressed in a list": "@SuppressWarnings('PMD.ApexDoc', 'PMD.ApexSOQLInjection') List<Account> m(String n) { return Database.query('SELECT Id FROM Account WHERE Name = \\'' + n + '\\''); }",
    "format, quoted placeholder": "List<Account> m(String n) { return Database.query(String.format('SELECT Id FROM Account WHERE Name = \\'{0}\\'', new List<Object>{ n })); }",
}


@pytest.mark.parametrize("body", _INJECTION_QUIET.values(), ids=_INJECTION_QUIET.keys())
def test_injection_quiet(body):
    hits = _hits(f"public with sharing class A {{ {body} }}")
    assert [h for h in hits if h[0] == "apex_soql_injection"] == []


def test_injection_through_a_field_built_in_the_constructor():
    src = """public with sharing class B implements Database.Batchable<SObject> {
        String query;
        public B(String status) { query = 'SELECT Id FROM Account WHERE Status__c = \\'' + status + '\\''; }
        public Database.QueryLocator start(Database.BatchableContext bc) { return Database.getQueryLocator(query); }
    }"""
    assert _hits(src) == [("apex_soql_injection", "start", "status")]


def test_injection_through_string_format():
    src = """public with sharing class A {
        List<Account> m(String n) {
            return Database.query(String.format('SELECT Id FROM Account WHERE Name = \\'\\'{0}\\'\\'', new List<Object>{ n }));
        }
    }"""
    assert _hits(src) == [("apex_soql_injection", "m", "n")]


# -- apex_missing_sharing -------------------------------------------------------

_SHARING_QUIET = {
    "declared": "public inherited sharing class A { void m() { update rows; } }",
    "subclass": "public class A extends Base { void m() { update rows; } }",
    "test class": "@IsTest private class A { static void m() { update rows; } }",
    "inner only": "public class A { public with sharing class B { void m() { update rows; } } }",
    "insert only": "public class A { void m() { insert new Account(); } }",
    "custom metadata": "public class A { List<X__mdt> m() { return [SELECT Id FROM X__mdt]; } }",
    "setup object": "public class A { void m() { List<RecordType> r = Database.query('SELECT Id, (SELECT Value FROM Localization) FROM RecordType'); } }",
    "custom setting": "public class A { My_Settings__c s; void m() { upsert s; } }",
    "suppressed": "@SuppressWarnings('PMD') public class A { void m() { update rows; } }",
    "interface": "public interface A { void m(); }",
    "trigger": "trigger T on Account (before update) { update Trigger.new; }",
}


@pytest.mark.parametrize("src", _SHARING_QUIET.values(), ids=_SHARING_QUIET.keys())
def test_missing_sharing_quiet(src):
    assert [h for h in _hits(src) if h[0] == "apex_missing_sharing"] == []


def test_missing_sharing_fires_on_dynamic_query_of_unknown_object():
    src = "public class A { List<SObject> m(String q) { return Database.query(q); } }"
    assert _hits(src) == [("apex_missing_sharing", "A", "line 1")]


# -- apex_hardcoded_id ----------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0051t000002bF3QAAU", True),  # 18 chars, valid checksum
        ("0051t000002bF3QAAA", False),  # checksum wrong
        ("0015f00000QzJ1T", True),  # 15 chars, Account key prefix
        ("001x000xxx35tPN", False),  # the site template's placeholder
        ("zzz1t000002bF3Q", False),  # 15 chars, unknown prefix
        ("001000000000000AAA", False),  # all zeros: a placeholder
        ("AccountServices", False),
        ("ExampleFeature1", False),
    ],
)
def test_is_record_id(value, expected):
    assert is_record_id(value) is expected


def test_hardcoded_id_quiet_in_fixture_class():
    src = "public class AccountFixtures { Id m() { return '0051t000002bF3QAAU'; } }"
    assert _hits(src) == []


# -- detectors and scoring --------------------------------------------------------


def test_test_files_keep_only_see_all_data():
    src = (_FIXTURES / "health_markers.cls").read_text(encoding="utf-8")
    assert {r.biomarker_type for r in detect_all(_ctx(src, is_test=True))} & set(_KINDS) == set()
    test_src = (_FIXTURES / "HealthMarkersTest.cls").read_text(encoding="utf-8")
    kinds = [r.biomarker_type for r in detect_all(_ctx(test_src, is_test=True))]
    assert kinds.count("apex_see_all_data") == 2


def test_findings_are_advisory_and_never_deduct():
    src = (_FIXTURES / "health_markers.cls").read_text(encoding="utf-8")
    results = [r for r in detect_all(_ctx(src, is_test=False)) if r.biomarker_type in _KINDS]
    assert {r.biomarker_type for r in results} == set(_KINDS) - {"apex_see_all_data"}
    for kind in _KINDS:
        assert dimensions_for(kind) == {"advisory"}
        assert biomarker_dimension(kind) == "advisory"
    scores, deductions = score_file(results)
    assert set(deductions) == {0.0}
    assert scores["defect"] == scores["maintainability"] == scores["performance"] == 10.0


@pytest.mark.parametrize("call", ["queryWithBinds", "countQueryWithBinds", "getQueryLocatorWithBinds"])
def test_injection_in_a_with_binds_only_file(call):
    src = f"""public with sharing class A {{
        Object m(String n) {{
            return Database.{call}('SELECT Id FROM Account WHERE Name = \\'' + n + '\\'', new Map<String, Object>(), AccessLevel.USER_MODE);
        }}
    }}"""
    assert _hits(src) == [("apex_soql_injection", "m", "n")]


def test_byte_gate_covers_every_dynamic_query_sink():
    for receiver, methods in _DYNAMIC_QUERY.items():
        for method in methods:
            assert _HAS_DYNAMIC_QUERY.search(f"{receiver.title()}.{method} (q)".encode()), method
