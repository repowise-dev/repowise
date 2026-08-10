"""The C/C++ and Swift entry-point warmups read ingestion's bytes.

Both warmups scan file text for an entry marker across every file of their
language. Neither ``ParsedFile`` nor ``FileInfo`` carries source, so both
used to re-open each file during ``GraphBuilder.build()``, after ingestion
had already read the whole repo. They now consult ``ctx.source_map``.

Same discriminating trick as the dead-code prepass tests: disk and map carry
different text, so the result says which one was read.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import networkx as nx

from repowise.core.ingestion.graph_warmups import _mark_cpp_entry_point_files, _warmup_swift


def _parsed(tmp_path: Path, rel: str, language: str, on_disk: str) -> SimpleNamespace:
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(on_disk, encoding="utf-8")
    return SimpleNamespace(file_info=SimpleNamespace(abs_path=str(abs_path), language=language))


def _graph(*paths: str) -> nx.DiGraph:
    g = nx.DiGraph()
    for p in paths:
        g.add_node(p, node_type="file", is_entry_point=False)
    return g


# --------------------------------------------------------------------------
# C / C++
# --------------------------------------------------------------------------


def test_cpp_warmup_reads_the_source_map_not_the_disk(tmp_path):
    parsed = {"src/mod.cpp": _parsed(tmp_path, "src/mod.cpp", "cpp", "int main() {}\n")}
    graph = _graph("src/mod.cpp")

    _mark_cpp_entry_point_files(parsed, graph, {"src/mod.cpp": b"PYBIND11_MODULE(m, x) {}\n"})

    assert graph.nodes["src/mod.cpp"]["is_entry_point"] is True


def test_cpp_warmup_falls_back_to_disk_on_a_map_miss(tmp_path):
    parsed = {"src/mod.cpp": _parsed(tmp_path, "src/mod.cpp", "cpp", "PYBIND11_MODULE(m, x) {}\n")}
    graph = _graph("src/mod.cpp")

    _mark_cpp_entry_point_files(parsed, graph, {"src/other.cpp": b"nothing\n"})

    assert graph.nodes["src/mod.cpp"]["is_entry_point"] is True


def test_cpp_warmup_with_no_source_map_still_reads_disk(tmp_path):
    parsed = {"src/mod.cpp": _parsed(tmp_path, "src/mod.cpp", "cpp", "PYBIND11_MODULE(m, x) {}\n")}
    graph = _graph("src/mod.cpp")

    _mark_cpp_entry_point_files(parsed, graph, None)

    assert graph.nodes["src/mod.cpp"]["is_entry_point"] is True


def test_cpp_warmup_leaves_unmarked_files_alone(tmp_path):
    parsed = {"src/plain.cpp": _parsed(tmp_path, "src/plain.cpp", "cpp", "int f() { return 0; }\n")}
    graph = _graph("src/plain.cpp")

    _mark_cpp_entry_point_files(parsed, graph, {"src/plain.cpp": b"int f() { return 0; }\n"})

    assert graph.nodes["src/plain.cpp"]["is_entry_point"] is False


# --------------------------------------------------------------------------
# Swift
# --------------------------------------------------------------------------


def _swift_ctx(parsed: dict, graph: nx.DiGraph, source_map) -> SimpleNamespace:
    return SimpleNamespace(graph=graph, parsed_files=parsed, source_map=source_map)


def test_swift_warmup_reads_the_source_map_not_the_disk(tmp_path):
    parsed = {"App.swift": _parsed(tmp_path, "App.swift", "swift", "struct App {}\n")}
    graph = _graph("App.swift")

    _warmup_swift(_swift_ctx(parsed, graph, {"App.swift": b"@main\nstruct App {}\n"}))

    assert graph.nodes["App.swift"]["is_entry_point"] is True


def test_swift_warmup_falls_back_to_disk_on_a_map_miss(tmp_path):
    parsed = {"App.swift": _parsed(tmp_path, "App.swift", "swift", "@main\nstruct App {}\n")}
    graph = _graph("App.swift")

    _warmup_swift(_swift_ctx(parsed, graph, {}))

    assert graph.nodes["App.swift"]["is_entry_point"] is True


def test_swift_warmup_with_no_source_map_still_reads_disk(tmp_path):
    parsed = {"App.swift": _parsed(tmp_path, "App.swift", "swift", "@main\nstruct App {}\n")}
    graph = _graph("App.swift")

    _warmup_swift(_swift_ctx(parsed, graph, None))

    assert graph.nodes["App.swift"]["is_entry_point"] is True


# --------------------------------------------------------------------------
# Decoding parity
# --------------------------------------------------------------------------

# BOM, non-ASCII, and a byte that is not valid UTF-8. Both paths decode
# utf-8 / replace, so the marker either survives on both or on neither.
_AWKWARD_BYTES = "\ufeff// caf\u00e9\nPYBIND11_MODULE(m, x) {}\n".encode() + b"\xff\n"


def test_awkward_bytes_decode_the_same_from_map_and_disk(tmp_path):
    abs_path = tmp_path / "src/awkward.cpp"
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(_AWKWARD_BYTES)
    parsed = {
        "src/awkward.cpp": SimpleNamespace(
            file_info=SimpleNamespace(abs_path=str(abs_path), language="cpp")
        )
    }

    from_map = _graph("src/awkward.cpp")
    _mark_cpp_entry_point_files(parsed, from_map, {"src/awkward.cpp": _AWKWARD_BYTES})
    from_disk = _graph("src/awkward.cpp")
    _mark_cpp_entry_point_files(parsed, from_disk, None)

    assert (
        from_map.nodes["src/awkward.cpp"]["is_entry_point"]
        == from_disk.nodes["src/awkward.cpp"]["is_entry_point"]
        is True
    )


def test_missing_file_with_no_map_entry_is_skipped(tmp_path):
    parsed = {
        "src/gone.cpp": SimpleNamespace(
            file_info=SimpleNamespace(abs_path=str(tmp_path / "src/gone.cpp"), language="cpp")
        )
    }
    graph = _graph("src/gone.cpp")

    _mark_cpp_entry_point_files(parsed, graph, {})

    assert graph.nodes["src/gone.cpp"]["is_entry_point"] is False
