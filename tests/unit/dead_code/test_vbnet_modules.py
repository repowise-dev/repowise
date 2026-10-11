"""A VB.NET ``Module`` is used when its members are, and never named for it.

Its members are in scope unqualified wherever its namespace is visible, so a
call site writes ``Run()`` and no importer ever names ``Tools``. Repro of
SCrawler's ``MainModShared`` and ``STDownloaderDeclarations``, both reported
deletion-ready while their members were called across the solution.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.analysis.dead_code.risk_factors import UNPROVEN_EXPORT_CONFIDENCE
from repowise.core.analysis.dead_code.vbnet_modules import vbnet_module_names
from tests.unit.dead_code._helpers import _build_graph

_EXPORTS_ONLY = {
    "detect_unreachable_files": False,
    "detect_unused_internals": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}

_PATH = "Shared/Helpers.vb"
_CALLER = "App/Main.vb"


def _source(container: str) -> bytes:
    return (
        "Public Class Used\n"
        "End Class\n"
        f"Public {container} Tools\n"
        "    Public Sub Run()\n"
        "    End Sub\n"
        f"End {container}\n"
    ).encode()


def _sym(name: str, start: int, end: int, kind: str = "class") -> dict:
    return {
        "name": name,
        "kind": kind,
        "visibility": "public",
        "decorators": [],
        "start_line": start,
        "end_line": end,
        "language": "vbnet",
    }


def _tools_finding(container: str, member_called: bool):
    graph = _build_graph(
        nodes={
            _PATH: {"language": "vbnet", "symbols": [_sym("Used", 1, 2), _sym("Tools", 3, 6)]},
            _CALLER: {
                "language": "vbnet",
                "is_entry_point": True,
                "symbols": [_sym("Start", 1, 3, kind="function")],
            },
        },
        # The importer names a sibling, so import absence would read as evidence.
        edges=[(_CALLER, _PATH, {"edge_type": "imports", "imported_names": ["Used"]})],
    )
    member = f"{_PATH}::Tools::Run"
    graph.add_node(member, node_type="symbol", file_path=_PATH, language="vbnet", kind="method")
    graph.add_edge(f"{_PATH}::Tools", member, edge_type="has_method")
    # One resolved call, so VB's resolution basis is known to the deletion gate.
    graph.add_edge(
        f"{_CALLER}::Start", f"{_PATH}::Used", edge_type="calls", resolution_origin="import_scoped"
    )
    if member_called:
        graph.add_edge(f"{_CALLER}::Start", member, edge_type="calls")
    source_map = {_PATH: _source(container), _CALLER: b"Sub Start()\n    Run()\nEnd Sub\n"}
    report = DeadCodeAnalyzer(graph, source_map=source_map).analyze(dict(_EXPORTS_ONLY))
    found = [
        f
        for f in report.findings
        if f.kind is DeadCodeKind.UNUSED_EXPORT and f.symbol_name == "Tools"
    ]
    return found[0] if found else None


def test_module_whose_member_is_called_is_not_reported():
    assert _tools_finding("Module", member_called=True) is None


def test_module_with_no_used_member_is_reported_but_not_deletion_ready():
    finding = _tools_finding("Module", member_called=False)
    assert finding is not None
    assert finding.confidence == pytest.approx(UNPROVEN_EXPORT_CONFIDENCE)
    assert not finding.safe_to_delete


def test_class_keeps_the_import_absence_evidence():
    """Only a Module's members are in scope unqualified; a Class is named."""
    finding = _tools_finding("Class", member_called=False)
    assert finding is not None
    assert finding.confidence == pytest.approx(1.0)
    assert finding.safe_to_delete


def test_header_after_an_attribute_line_is_read():
    source = b"<HideModuleName>\nPublic Module Tools\nEnd Module\n"
    assert vbnet_module_names([_sym("Tools", 1, 3)], source) == {"Tools"}


def test_crlf_source_is_read():
    source = b"Public Class Used\r\nEnd Class\r\nPublic Module Tools\r\nEnd Module\r\n"
    syms = [_sym("Used", 1, 2), _sym("Tools", 3, 4)]
    assert vbnet_module_names(syms, source) == {"Tools"}


def test_module_nested_in_a_namespace_is_read():
    """The Namespace block is the Module's parent; it is still a Module."""
    source = b"Namespace Downloads\n    Public Module Tools\n    End Module\nEnd Namespace\n"
    sym = dict(_sym("Tools", 2, 3), parent_name="Downloads")
    assert vbnet_module_names([sym], source) == {"Tools"}


@pytest.mark.parametrize("comment", ["' Module Tools was split out", "REM Module Tools"])
def test_a_comment_naming_the_module_is_not_its_header(comment):
    source = f"<Serializable>\n{comment}\nPublic Class Tools\nEnd Class\n".encode()
    assert vbnet_module_names([_sym("Tools", 1, 4)], source) == frozenset()


def test_unreadable_header_counts_as_a_module():
    """Unknown lowers a confidence; it never keeps a deletion-ready claim."""
    assert vbnet_module_names([_sym("Tools", 1, 3)], None) == {"Tools"}


def test_other_languages_are_not_read():
    sym = dict(_sym("Tools", 1, 3), language="csharp")
    assert vbnet_module_names([sym], b"public static class Tools {}\n") == frozenset()
