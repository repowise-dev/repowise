"""JUnit XML test reports: read as test timings, placed on their test files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from repowise.core.evidence.add import EvidenceRefusedError, prepare_evidence
from repowise.core.evidence.format_v1 import EvidenceFormatError, parse_evidence
from repowise.core.evidence.ingest import join_entries, place_test_entries
from repowise.core.evidence.profile_formats import detect_profile_format, junit

# pytest --junitxml, default xunit2 family: no file attribute.
PYTEST_XUNIT2 = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="4" time="1.2">
<testcase classname="tests.test_cart.TestCart" name="test_adds[1-a]" time="0.900" />
<testcase classname="tests.test_cart.TestCart" name="test_removes" time="0.010"><failure message="x">boom</failure></testcase>
<testcase classname="tests.test_cart" name="test_module_level" time="0.002"><skipped message="s" /></testcase>
<testcase classname="tests.test_cart" name="test_errors" time="0.003"><error message="e" /></testcase>
</testsuite></testsuites>
"""

# junit_family=xunit1 adds file and line.
PYTEST_XUNIT1 = b"""<testsuite name="pytest" tests="1">
<testcase classname="tests.test_cart.TestCart" file="tests/test_cart.py" line="3" name="test_adds" time="0.5" />
</testsuite>
"""

# Maven Surefire / Gradle: one file per class; JUnit 5 names carry their argument list.
SUREFIRE = b"""<?xml version="1.0" encoding="UTF-8"?>
<!-- generated -->
<testsuite name="com.acme.CartTest" time="0.31" tests="2" errors="0" skipped="0" failures="0">
<properties><property name="java.version" value="21"/></properties>
<testcase name="addsItem()" classname="com.acme.CartTest" time="0.3"/>
<testcase name="totals(int)[1]" classname="com.acme.CartTest$Totals" time="0.01"/>
<system-out>noise</system-out>
</testsuite>
"""

# Vitest's junit reporter names the file as the classname.
VITEST = b"""<testsuites name="vitest tests" tests="1">
<testsuite name="src/cart.test.ts" tests="1">
<testcase classname="src/cart.test.ts" name="cart &gt; adds an item" time="0.004" />
</testsuite></testsuites>
"""

# jest-junit's default: a title, no file.
JEST = b"""<testsuites name="jest tests"><testsuite name="cart">
<testcase classname="cart adds an item" name="cart adds an item" time="0.004" />
</testsuite></testsuites>
"""


def _doc(data: bytes):
    return parse_evidence(json.dumps(junit.to_v1(data)))


@pytest.mark.parametrize("data", [PYTEST_XUNIT2, PYTEST_XUNIT1, SUREFIRE, VITEST, JEST])
def test_every_runner_shape_is_detected_as_junit(data: bytes) -> None:
    assert detect_profile_format(data) == "junit"


def test_other_xml_is_not_junit() -> None:
    assert detect_profile_format(b'<?xml version="1.0"?><coverage line-rate="1"/>') is None


def test_pytest_cases_keep_their_classname_until_placed() -> None:
    doc = _doc(PYTEST_XUNIT2)

    assert doc.kind == "test_timing"
    assert [(e["test_id"], e["outcome"], e["duration_s"]) for e in doc.entries] == [
        ("tests.test_cart.TestCart::test_adds[1-a]", "passed", 0.9),
        ("tests.test_cart.TestCart::test_removes", "failed", 0.01),
        ("tests.test_cart::test_module_level", "skipped", 0.002),
        ("tests.test_cart::test_errors", "error", 0.003),
    ]
    # Class or module fixtures run inside the first test of each classname.
    assert [e["first_in_class"] for e in doc.entries] == [True, None, True, None]


def test_a_case_that_names_its_file_gets_a_path_id() -> None:
    (entry,) = _doc(PYTEST_XUNIT1).entries

    assert entry["test_id"] == "tests/test_cart.py::TestCart::test_adds"
    assert entry["test_file"] == "tests/test_cart.py"
    assert entry["location"] == {
        "path": "tests/test_cart.py", "function": "TestCart::test_adds", "line": None, "exact": True,
    }
    assert entry["classname"] is None


def test_a_path_classname_is_the_file() -> None:
    (entry,) = _doc(VITEST).entries

    assert entry["test_id"] == "src/cart.test.ts::cart > adds an item"
    assert entry["test_file"] == "src/cart.test.ts"


def test_placing_joins_dotted_names_to_indexed_files() -> None:
    files = {"tests/test_cart.py", "src/test/java/com/acme/CartTest.java", "src/cart.py"}

    pytest_doc = place_test_entries(_doc(PYTEST_XUNIT2), files)
    java_doc = place_test_entries(_doc(SUREFIRE), files)

    assert [e["test_id"] for e in pytest_doc.entries] == [
        "tests/test_cart.py::TestCart::test_adds[1-a]",
        "tests/test_cart.py::TestCart::test_removes",
        "tests/test_cart.py::test_module_level",
        "tests/test_cart.py::test_errors",
    ]
    assert pytest_doc.entries[0]["location"]["function"] == "TestCart::test_adds"
    assert [(e["test_id"], e["location"]["function"]) for e in java_doc.entries] == [
        ("src/test/java/com/acme/CartTest.java::CartTest::addsItem()", "CartTest::addsItem"),
        (
            "src/test/java/com/acme/CartTest.java::CartTest::Totals::totals(int)[1]",
            "CartTest::Totals::totals",
        ),
    ]


def test_a_name_two_files_end_with_is_not_guessed() -> None:
    doc = place_test_entries(_doc(PYTEST_XUNIT2), {"a/tests/test_cart.py", "b/tests/test_cart.py"})

    assert all(e["classname"] and e["location"] is None for e in doc.entries)


def test_an_unplaced_test_keeps_its_timing_and_is_counted() -> None:
    doc = place_test_entries(_doc(JEST), {"src/cart.ts"})

    joined = join_entries(doc, {}, {}, frozenset())

    assert joined.matched == 1
    assert joined.unlinked == {"unplaced": 1}
    (row,) = joined.rows
    assert (row["test_id"], row["symbol_id"], row["duration_s"], row["first_in_class"]) == (
        "cart adds an item::cart adds an item", None, 0.004, True,
    )


def test_reports_combine_into_one_run() -> None:
    combined = junit.combine([SUREFIRE, VITEST])

    assert detect_profile_format(combined) == "junit"
    assert len(_doc(combined).entries) == 3


@pytest.mark.parametrize("data", [b"<testsuite", b"<coverage/>"])
def test_a_file_that_is_not_junit_is_refused(data: bytes) -> None:
    with pytest.raises(EvidenceFormatError):
        junit.to_v1(data)


def test_a_converted_report_needs_the_commit(tmp_path: Path) -> None:
    with pytest.raises(EvidenceRefusedError) as refused:
        prepare_evidence(SUREFIRE, name="r.xml", checkout_root=tmp_path)

    assert refused.value.option == "--commit"
