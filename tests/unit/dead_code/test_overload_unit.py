"""An overload set is one dead-code unit.

Java and C# overloads of different arity carry their own ids (``pad#1``,
``pad#2``). Dead code still judges the set as one symbol: a use of any member
uses all of them, no member becomes a finding of its own, and the composed
constructor id ``path::X::X`` finds ``X``'s constructors wherever their arities
put them.
"""

from __future__ import annotations

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind

_FILE = "src/Box.cs"
_CONFIG = {
    "detect_unreachable_files": False,
    "detect_unused_exports": False,
    "detect_zombie_packages": False,
    "min_confidence": 0.0,
}


def _graph(symbols: list[tuple[str, str, str, int]], calls: list[tuple[str, str]]) -> nx.DiGraph:
    """*symbols* as ``(id suffix, name, kind, start line)``, all private C#."""
    g = nx.DiGraph()
    g.add_node(_FILE, language="csharp")
    for suffix, name, kind, line in symbols:
        sym_id = f"{_FILE}::{suffix}"
        g.add_node(
            sym_id,
            node_type="symbol",
            file_path=_FILE,
            name=name,
            kind=kind,
            language="csharp",
            visibility="private",
            decorators=[],
            start_line=line,
            end_line=line + 2,
        )
        g.add_edge(_FILE, sym_id, edge_type="defines")
    for caller, callee in calls:
        g.add_edge(caller, f"{_FILE}::{callee}", edge_type="calls")
    return g


def _unused(g: nx.DiGraph) -> list[tuple[str, int]]:
    report = DeadCodeAnalyzer(g, git_meta_map={}).analyze(_CONFIG)
    return sorted(
        (f.symbol_name, f.start_line)
        for f in report.findings
        if f.kind == DeadCodeKind.UNUSED_INTERNAL
    )


_PAD = [("Box::pad#1", "pad", "method", 10), ("Box::pad#2", "pad", "method", 20)]


def test_a_use_of_one_overload_uses_the_set() -> None:
    assert _unused(_graph(_PAD, [("src/Main.cs::Main::Run", "Box::pad#2")])) == []


def test_an_unused_overload_set_adds_no_finding_per_member() -> None:
    """Members are reached through their type, so the split adds no findings."""
    assert _unused(_graph(_PAD, [])) == []


def test_a_constructor_overload_rescues_its_class() -> None:
    """``new Inner(a, b)`` lands on ``Inner::Inner#2``; the class is used."""
    symbols = [
        ("Inner", "Inner", "class", 3),
        ("Inner::Inner#0", "Inner", "method", 4),
        ("Inner::Inner#2", "Inner", "method", 5),
        ("Spare", "Spare", "class", 30),
    ]
    found = _unused(_graph(symbols, [("src/Main.cs::Main::Run", "Inner::Inner#2")]))
    assert ("Inner", 3) not in found
    assert ("Spare", 30) in found
