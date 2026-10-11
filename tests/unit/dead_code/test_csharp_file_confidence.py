"""C# files: no importer edge is weak evidence, and reference-assembly API is never dead.

From dotnet/runtime: 274 C# files reported unreachable at 1.0 on git age alone
(a servicing branch leaves nearly every file untouched for a year), among them
``FileConfigurationExtensions.cs``, whose public ``SetBasePath`` the library's
``ref/`` assembly lists.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import RISK_CAP_CONFIDENCE
from tests.unit.dead_code._helpers import _build_graph, _old_date

_STALE = {"commit_count_90d": 0, "last_commit_at": _old_date(800), "age_days": 900}


def _cls(name: str, language: str) -> dict:
    return {
        "name": name,
        "kind": "class",
        "visibility": "public",
        "decorators": [],
        "start_line": 3,
        "end_line": 9,
        "language": language,
    }


def _analyze(nodes: dict, source_map: dict[str, bytes]):
    graph = _build_graph(nodes)
    analyzer = DeadCodeAnalyzer(
        graph, git_meta_map={path: dict(_STALE) for path in nodes}, source_map=source_map
    )
    return analyzer.analyze({"detect_zombie_packages": False, "min_confidence": 0.0})


def _file_finding(report, path):
    return next(
        (f for f in report.findings if f.kind is DeadCodeKind.UNREACHABLE_FILE and f.file_path == path),
        None,
    )


def test_csharp_file_is_capped_to_the_review_tier_whatever_its_age():
    path = "src/Linq/ExceptQueryOperator.cs"
    report = _analyze(
        {path: {"language": "csharp", "symbols": [_cls("ExceptQueryOperator", "csharp")]}},
        {path: b"namespace Linq;\n\ninternal sealed class ExceptQueryOperator\n{\n}\n"},
    )
    finding = _file_finding(report, path)
    assert finding.confidence == pytest.approx(RISK_CAP_CONFIDENCE)
    assert any("Imported by namespace" in line for line in finding.evidence)


def test_type_listed_in_a_reference_assembly_is_never_dead():
    src = "src/libraries/Config.FileExtensions/src/FileConfigurationExtensions.cs"
    ref = "src/libraries/Config.FileExtensions/ref/Config.FileExtensions.cs"
    report = _analyze(
        {
            src: {"language": "csharp", "symbols": [_cls("FileConfigurationExtensions", "csharp")]},
            ref: {"language": "csharp", "symbols": [_cls("FileConfigurationExtensions", "csharp")]},
        },
        {
            src: b"namespace C;\n\npublic static class FileConfigurationExtensions\n{\n}\n",
            ref: b"namespace C\n{\n    public static class FileConfigurationExtensions { }\n}\n",
        },
    )
    # Neither the source file, nor its type, nor the reference stub itself.
    assert report.findings == []


def test_file_whose_type_another_file_names_is_capped():
    # A Swift type is used across its module without an import.
    path = "Sources/App/FilterType.swift"
    user = "Sources/App/FilterRef.swift"
    report = _analyze(
        {
            path: {"language": "swift", "symbols": [_cls("FilterType", "swift")]},
            user: {"language": "swift", "is_entry_point": True},
        },
        {
            path: b"import Foundation\n\npublic enum FilterType { case a }\n",
            user: b"struct FilterRef { var t: FilterType }\n",
        },
    )
    finding = _file_finding(report, path)
    assert finding.confidence == pytest.approx(RISK_CAP_CONFIDENCE)
    assert any("FilterRef.swift" in line for line in finding.evidence)


def test_file_whose_types_nothing_names_keeps_its_age_score():
    path = "pkg/orphan.py"
    report = _analyze(
        {path: {"language": "python", "symbols": [_cls("Orphan", "python")]}},
        {path: b"\n\nclass Orphan:\n    pass\n", "pkg/other.py": b"x = 1\n"},
    )
    assert _file_finding(report, path).confidence == pytest.approx(1.0)
