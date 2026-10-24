"""Salesforce Apex coverage parser (Salesforce CLI JSON).

Reads what ``sf apex run test --code-coverage --result-format json`` (or the
legacy ``sfdx force:apex:test:run -c -r json``, or ``sf apex get test``)
prints, and the files its ``--output-dir`` writes:

- the whole result, as stdout or ``test-result-<run id>.json``::

    {"status": 0, "result": {"summary": {...}, "tests": [...],
     "coverage": {"coverage": [<entry>, ...], "records": [<record>, ...]}}}

- ``test-result-codecoverage.json``, the bare ``[<entry>, ...]`` list.

An entry is one class or trigger's coverage for the run::

    {"id": "01p...", "name": "AccountService", "totalLines": 28,
     "lines": {"4": 1, "18": 0}, "totalCovered": 22, "coveredPercent": 79}

``lines`` holds every executable line, ``1`` covered and ``0`` not. A record
is one test method's coverage of one class or trigger (``ApexCodeCoverage``)::

    {"ApexTestClass": {"Name": "AccountServiceTest"}, "TestMethodName": "creates",
     "ApexClassOrTrigger": {"Id": "01p...", "Name": "AccountService"},
     "Coverage": {"coveredLines": [3, 4], "uncoveredLines": [8]}}

Names are API names, never file paths: the file path is the name plus
``.cls`` for an ``ApexClass`` id (``01p`` prefix) or ``.trigger`` for an
``ApexTrigger`` id (``01q``), and the resolver maps that to the one file of
that name in the repo. A name with no known id prefix stays bare and the
resolver tries both extensions.
"""

from __future__ import annotations

import json
from typing import Any

from .model import ContextCoverageReport, CoverageReport, TestCoverage, file_coverage

#: Salesforce key prefixes of the two Apex metadata types coverage names.
_EXT_BY_ID_PREFIX = {"01p": ".cls", "01q": ".trigger"}

_BOM = chr(0xFEFF)


def _dicts(value: Any) -> list[dict]:
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _load(text: str) -> tuple[list[dict], list[dict]]:
    """``(entries, records)`` from any of the CLI's JSON shapes, empty when unreadable."""
    try:
        data = json.loads(text.lstrip(_BOM))
    except (ValueError, TypeError):
        return [], []
    if isinstance(data, list):
        return _dicts(data), []
    result = data.get("result", data) if isinstance(data, dict) else None
    coverage = result.get("coverage") if isinstance(result, dict) else None
    if not isinstance(coverage, dict):
        return [], []
    return _dicts(coverage.get("coverage")), _dicts(coverage.get("records"))


def _apex_path(name: Any, sf_id: Any) -> str | None:
    """``Name.cls`` / ``Name.trigger`` by the id's key prefix, bare ``Name`` when unknown."""
    if not isinstance(name, str) or not name.strip():
        return None
    ext = _EXT_BY_ID_PREFIX.get(sf_id[:3], "") if isinstance(sf_id, str) else ""
    return name.strip() + ext


def _lines(raw: Any) -> list[int]:
    if not isinstance(raw, list):
        return []
    return [n for n in raw if isinstance(n, int) and not isinstance(n, bool) and n > 0]


def parse_apex(text: str) -> CoverageReport:
    """Per-class aggregate coverage from Salesforce CLI JSON."""
    entries, _ = _load(text)
    files = []
    for entry in entries:
        path = _apex_path(entry.get("name"), entry.get("id"))
        lines = entry.get("lines")
        if path is None or not isinstance(lines, dict):
            continue
        coverable: list[int] = []
        covered: list[int] = []
        for line, hits in lines.items():
            try:
                n = int(line)
            except (TypeError, ValueError):
                continue
            if n <= 0:
                continue
            coverable.append(n)
            if isinstance(hits, (int, float)) and hits > 0:
                covered.append(n)
        # A class with no executable lines (an interface) has nothing to measure.
        if coverable:
            files.append(file_coverage(path, covered, coverable))
    return CoverageReport(source_format="apex", files=files)


def parse_apex_contexts(text: str) -> ContextCoverageReport:
    """Per-test records (one per test method and class or trigger) from Salesforce CLI JSON.

    ``test_id`` is ``TestClass.method``, the name ``sf apex run test --tests``
    takes; ``test_file`` is the test class's ``.cls`` name, for the resolver to
    map like the covered file.
    """
    _, raw_records = _load(text)
    records: list[TestCoverage] = []
    for rec in raw_records:
        target = rec.get("ApexClassOrTrigger")
        test_class = rec.get("ApexTestClass")
        method = rec.get("TestMethodName")
        coverage = rec.get("Coverage")
        if not (isinstance(target, dict) and isinstance(test_class, dict)):
            continue
        path = _apex_path(target.get("Name"), target.get("Id"))
        class_name = test_class.get("Name")
        covered = _lines(coverage.get("coveredLines")) if isinstance(coverage, dict) else []
        if path is None or not isinstance(class_name, str) or not class_name or not covered:
            continue
        test_id = f"{class_name}.{method}" if isinstance(method, str) and method else class_name
        records.append(
            TestCoverage(
                test_id=test_id,
                file_path=path,
                covered_lines=sorted(set(covered)),
                source_format="apex",
                test_file=f"{class_name}.cls",
            )
        )
    return ContextCoverageReport(source_format="apex", records=records, has_contexts=bool(records))
