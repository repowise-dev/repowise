"""JUnit XML test reports: test timings, not a profile.

Every test runner that writes JUnit XML (pytest ``--junitxml``, Maven
Surefire, Gradle, jest-junit, Vitest's ``junit`` reporter) writes the same
shape: ``<testsuites>`` or one ``<testsuite>`` holding ``<testcase
classname= name= time= [file=]>``, with a ``<failure>``, ``<error>`` or
``<skipped>`` child when the test did not pass. Each test case becomes one
``test_timing`` entry with its duration in seconds and its outcome.

A test's file comes from the case's ``file`` attribute (pytest's xunit1
family, jest-junit with ``addFileAttribute``), a ``classname`` that is a
path (Vitest; jest-junit with ``JEST_JUNIT_CLASSNAME={filepath}``), or the
suite's ``file``. When none says it, the entry keeps
the dotted ``classname`` (``tests.unit.test_x.TestC``, ``com.acme.CartTest``)
and ingest places it on the indexed file that name leads to
(:func:`repowise.core.evidence.ingest.place_test_entries`). The test id is
``path::qualname`` (the ``test_coverage`` namespace), and the entry names the
test function by its id after the file, parameters cut, so ingest joins it
exactly or keeps the timing without its function.

Timing is not exact evidence: a test's time includes its fixtures. A
fixture shared by a class or module runs once, inside the first test that
needs it, so the first case of each ``classname`` is marked
``first_in_class`` (and every other case is not): its time may carry the
whole class's setup.

The file carries no commit; ``evidence add`` needs ``--commit``. Properties,
captured output and failure messages are never read.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Iterator
from typing import Any
from xml.etree import ElementTree as ET
from xml.parsers import expat

from repowise.core.interchange import repo_relative_path
from repowise.core.test_paths import (
    dotted_name_file,
    files_by_stem,
    nodeid_function,
    nodeid_test_file,
)

from ..format_v1 import EVIDENCE_FORMAT, EvidenceFormatError

# The root element, after an optional XML declaration and comments.
_ROOT = re.compile(rb"^(?:<\?xml[^>]*\?>|<!--.*?-->|\s)*<testsuites?[\s>/]", re.S)
_ROOT_TAGS = ("testsuites", "testsuite")
_OUTCOME_CHILDREN = (("failure", "failed"), ("error", "error"), ("skipped", "skipped"))


def sniff(head: bytes) -> bool:
    return _ROOT.match(head.lstrip(b"\xef\xbb\xbf")) is not None


def combine(reports: Iterable[bytes]) -> bytes:
    """Several JUnit XML files as one ``<testsuites>`` document.

    Maven Surefire and Gradle write one file per test class into a
    directory; ingest reads one document per run. Other XML beside them
    (``failsafe-summary.xml``, ``testng-results.xml``), or a file that
    declares a document type, is left out; a file
    that is not XML, or a directory with no JUnit report, raises
    :class:`EvidenceFormatError`.
    """
    out = ET.Element("testsuites")
    for data in reports:
        try:
            root = _parse(data, junit_only=False)
        except _DocumentTypeError:
            # A JUnit report never declares one (testng-results.xml does): not a report.
            continue
        if root.tag in _ROOT_TAGS:
            out.extend(list(root) if root.tag == "testsuites" else [root])
    if not len(out):
        raise EvidenceFormatError(
            "no file holds a JUnit XML report (<testsuites> or <testsuite>)",
            "pass the directory the test run wrote its JUnit XML reports to",
        )
    return ET.tostring(out, encoding="utf-8")


def to_v1(data: bytes) -> dict[str, Any]:
    """The whole file as a v1 ``test_timing`` document; entries are checked by the v1 parser."""
    root = _parse(data)
    seen_classes: set[str] = set()
    tests: list[dict[str, Any]] = []
    for case, suite_file in _cases(root):
        classname = (case.get("classname") or "").strip()
        entry = _entry(case, classname, suite_file)
        entry["first_in_class"] = classname not in seen_classes
        seen_classes.add(classname)
        tests.append(entry)
    return {
        "format": EVIDENCE_FORMAT,
        "kind": "test_timing",
        "tool": {"name": "junit"},
        "tests": tests,
    }


class _DocumentTypeError(EvidenceFormatError):
    """The file declares a document type, which no JUnit XML report has."""


def _declares_doctype(data: bytes) -> bool:
    """Whether *data* declares a document type, read by expat in whatever encoding it names.

    The parser stops at the declaration, before any entity in it is defined;
    a file that is not well-formed is left for the real parse to report.
    """

    def stop(*_: object) -> None:
        raise _DocumentTypeError("", "")

    parser = expat.ParserCreate()
    parser.StartDoctypeDeclHandler = stop
    try:
        parser.Parse(data, True)
    except _DocumentTypeError:
        return True
    except expat.ExpatError:
        return False
    return False


def _parse(data: bytes, *, junit_only: bool = True) -> ET.Element:
    """The root element; a document type (and so any entity) is refused, never expanded."""
    if _declares_doctype(data):
        raise _DocumentTypeError(
            "the file declares a document type; a JUnit XML report has none",
            "ingest the JUnit XML file the test run wrote",
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise EvidenceFormatError(
            f"the file is not well-formed XML ({exc})",
            "ingest the JUnit XML file the test run wrote, after the run finished",
        ) from None
    if junit_only and root.tag not in _ROOT_TAGS:
        raise EvidenceFormatError(
            f"the root element is <{root.tag}>, not <testsuites> or <testsuite>",
            "write the report with the runner's JUnit XML reporter",
        )
    return root


def _cases(element: ET.Element, suite_file: str | None = None) -> Iterator[tuple[ET.Element, str | None]]:
    """Every ``<testcase>`` in document order, with the ``file`` of its nearest suite."""
    for child in element:
        if child.tag == "testsuite":
            yield from _cases(child, child.get("file") or suite_file)
        elif child.tag == "testcase":
            yield child, suite_file


def _entry(case: ET.Element, classname: str, suite_file: str | None) -> dict[str, Any]:
    """One case as a v1 test entry; a case with no name is left for the v1 parser to count."""
    name = (case.get("name") or "").strip()
    entry: dict[str, Any] = {"duration_s": _seconds(case.get("time")), "outcome": _outcome(case)}
    if not name:
        return entry
    path, outside = _test_file(case, classname, suite_file)
    if outside and nodeid_test_file(classname):
        # The classname is the absolute path, which names the machine, not the
        # repository: keep the test's own name only.
        entry["id"] = name
    elif path:
        test_id = f"{path}::{_qualname(classname, path, name)}"
        entry.update(id=test_id, path=path, function=nodeid_function(test_id), exact=True)
    else:
        # Placed on a file at ingest, from the indexed files the classname leads to.
        entry.update(id=f"{classname}::{name}" if classname else name, classname=classname or None)
    return entry


def _test_file(case: ET.Element, classname: str, suite_file: str | None) -> tuple[str | None, bool]:
    """The repo-relative file the report names for *case*, and whether it named one outside the repo."""
    named = (case.get("file") or "").strip() or nodeid_test_file(classname) or suite_file
    path, _ = repo_relative_path(named)
    return path, bool(named) and path is None


def _qualname(classname: str, path: str, name: str) -> str:
    """*name* after the classes *classname* names past the module *path* is.

    ``tests.test_x.TestC`` in ``tests/test_x.py`` gives ``TestC::<name>``; a
    classname that is the path itself, or does not lead to *path*, gives
    *name* alone.
    """
    found = dotted_name_file(classname, files_by_stem([path]))
    return "::".join((*found[1], name)) if found else name


def _seconds(value: str | None) -> float | None:
    try:
        seconds = float((value or "").replace(",", ""))
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _outcome(case: ET.Element) -> str:
    for tag, outcome in _OUTCOME_CHILDREN:
        if case.find(tag) is not None:
            return outcome
    return "passed"


__all__ = ["combine", "sniff", "to_v1"]
