"""The marker prepasses read ingestion's bytes, and fall back cleanly.

``DeadCodeAnalyzer.__init__`` runs three scans over the whole indexed file
set: dynamic-import markers, ``namespace JSX`` declarations and bundler
``resolve.alias`` targets. Each used to open every file itself, so a TS/JS
repo paid a full-repo read pass each before detection started. They now take
ingestion's ``source_map``.

A fourth, the ``export { local as alias }`` map, is no longer a pass at all:
the parser records the clause on the parsed file, so this reads it back.

These tests pin the three states that matter (map hit, map miss, no map at
all) plus the decoding contract, since a prepass that decoded the handed-in
bytes differently from the file it used to open would silently move findings.

The discriminating trick throughout: the on-disk file and the ``source_map``
entry carry *different* text. Whichever one a prepass actually consulted is
then visible in the result.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.dead_code import DeadCodeAnalyzer

from ._helpers import _build_graph


def _parsed(tmp_path: Path, rel: str, on_disk: str) -> SimpleNamespace:
    """A ParsedFile stub for *rel*, with *on_disk* written to the real file."""
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(on_disk, encoding="utf-8")
    return SimpleNamespace(file_info=SimpleNamespace(abs_path=str(abs_path)))


def _analyzer(parsed_files: dict, source_map: dict[str, bytes] | None) -> DeadCodeAnalyzer:
    """Analyzer over an empty graph, so only the __init__ prepasses are exercised."""
    return DeadCodeAnalyzer(
        _build_graph(nodes={}),
        git_meta_map={},
        parsed_files=parsed_files,
        source_map=source_map,
    )


# --------------------------------------------------------------------------
# find_dynamic_import_files
# --------------------------------------------------------------------------


def test_dynamic_markers_read_the_source_map_not_the_disk(tmp_path):
    """A map hit wins: the marker is in the bytes, not in the file."""
    parsed = {"pkg/loader.py": _parsed(tmp_path, "pkg/loader.py", "x = 1\n")}
    source_map = {"pkg/loader.py": b"importlib.import_module('plugins')\n"}

    assert _analyzer(parsed, source_map)._dynamic_import_files == {"pkg/loader.py"}


def test_dynamic_markers_fall_back_to_disk_on_a_map_miss(tmp_path):
    """A map that simply lacks this path must not lose the marker.

    The update path hands over a map built for the files it re-read; anything
    outside it has to keep reading from disk or the rescue silently narrows.
    """
    parsed = {"pkg/loader.py": _parsed(tmp_path, "pkg/loader.py", "__import__('plugins')\n")}
    source_map = {"pkg/other.py": b"nothing here\n"}

    assert _analyzer(parsed, source_map)._dynamic_import_files == {"pkg/loader.py"}


def test_dynamic_markers_with_no_source_map_still_read_disk(tmp_path):
    """``source_map=None`` is the pre-existing contract: every caller unchanged."""
    parsed = {"pkg/loader.py": _parsed(tmp_path, "pkg/loader.py", "__import__('plugins')\n")}

    assert _analyzer(parsed, None)._dynamic_import_files == {"pkg/loader.py"}


def test_dynamic_markers_absent_from_both_sources(tmp_path):
    """No marker anywhere is still no marker. The map is not a free pass."""
    parsed = {"pkg/plain.py": _parsed(tmp_path, "pkg/plain.py", "x = 1\n")}

    assert _analyzer(parsed, {"pkg/plain.py": b"y = 2\n"})._dynamic_import_files == set()


# --------------------------------------------------------------------------
# _find_jsx_namespace_files
# --------------------------------------------------------------------------


def test_jsx_namespace_scan_reads_the_source_map(tmp_path):
    parsed = {"src/jsx.d.ts": _parsed(tmp_path, "src/jsx.d.ts", "export {};\n")}
    source_map = {"src/jsx.d.ts": b"declare namespace JSX { interface Element {} }\n"}

    assert _analyzer(parsed, source_map)._jsx_namespace_files == {"src/jsx.d.ts"}


def test_jsx_namespace_scan_falls_back_to_disk(tmp_path):
    parsed = {"src/jsx.d.ts": _parsed(tmp_path, "src/jsx.d.ts", "namespace JSX {}\n")}

    assert _analyzer(parsed, {})._jsx_namespace_files == {"src/jsx.d.ts"}


# --------------------------------------------------------------------------
# _find_bundler_alias_targets
# --------------------------------------------------------------------------


def _alias_fixture(tmp_path: Path, config_on_disk: str) -> dict:
    return {
        "vite.config.ts": _parsed(tmp_path, "vite.config.ts", config_on_disk),
        "src/shims/shiki.ts": _parsed(tmp_path, "src/shims/shiki.ts", "export default {};\n"),
    }


def test_bundler_alias_targets_read_the_source_map(tmp_path):
    parsed = _alias_fixture(tmp_path, "export default {};\n")
    source_map = {"vite.config.ts": b"alias: { shiki: './src/shims/shiki.ts' }\n"}

    assert _analyzer(parsed, source_map)._bundler_alias_targets == {"src/shims/shiki.ts"}


def test_bundler_alias_targets_fall_back_to_disk(tmp_path):
    parsed = _alias_fixture(tmp_path, "alias: { shiki: './src/shims/shiki.ts' }\n")

    assert _analyzer(parsed, {})._bundler_alias_targets == {"src/shims/shiki.ts"}


# --------------------------------------------------------------------------
# _find_ts_export_aliases
# --------------------------------------------------------------------------


def test_export_aliases_come_from_the_parsed_file(tmp_path):
    """The map is the parser's, inverted — neither disk nor the source map.

    Both of those carry a *different* alias here, so a pass that still read
    either one would say so. What the analyzer reports is what the parser
    recorded, which is the whole point of folding the two readers into one.
    """
    parsed = {
        "src/history.ts": _parsed(tmp_path, "src/history.ts", "export { OnDisk as History };\n")
    }
    parsed["src/history.ts"].export_aliases = {"History": "HistoryWrapper"}
    source_map = {"src/history.ts": b"export { InTheMap as History };\n"}

    assert _analyzer(parsed, source_map)._ts_export_aliases == {
        "src/history.ts": {"HistoryWrapper": "History"}
    }


def test_a_file_the_parser_recorded_no_alias_for_is_absent(tmp_path):
    """Including one whose text holds a clause the parser declined to record."""
    parsed = {
        "src/history.ts": _parsed(
            tmp_path, "src/history.ts", "export { HistoryWrapper as History };\n"
        )
    }
    parsed["src/history.ts"].export_aliases = {}

    assert _analyzer(parsed, {})._ts_export_aliases == {}


# --------------------------------------------------------------------------
# Decoding: a map hit and a disk read must agree on the same bytes
# --------------------------------------------------------------------------

# A UTF-8 BOM, a non-ASCII comment, and a lone 0xFF that is valid in the
# locale codec but not in UTF-8. Three ways two decoders can diverge.
_AWKWARD_BYTES = (
    "\ufeff// caf\u00e9 \u00ff\nimportlib.import_module('plugins')\n".encode() + b"\xff\n"
)


def test_bom_and_non_ascii_decode_the_same_from_map_and_disk(tmp_path):
    """The prepasses must not become encoding-sensitive by moving to bytes.

    Reading via ``source_map`` decodes the bytes ourselves; reading from disk
    used to go through ``Path.read_text(errors="ignore")``. Both paths decode
    identically now, so a BOM or an undecodable byte cannot make the same file
    match on one path and miss on the other.
    """
    abs_path = tmp_path / "pkg/awkward.py"
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_bytes(_AWKWARD_BYTES)
    parsed = {"pkg/awkward.py": SimpleNamespace(file_info=SimpleNamespace(abs_path=str(abs_path)))}

    from_map = _analyzer(parsed, {"pkg/awkward.py": _AWKWARD_BYTES})._dynamic_import_files
    from_disk = _analyzer(parsed, None)._dynamic_import_files

    assert from_map == from_disk == {"pkg/awkward.py"}


def test_unreadable_file_with_no_map_entry_is_skipped(tmp_path):
    """A path in neither place is dropped, not raised, because the pass is best-effort."""
    parsed = {
        "pkg/gone.py": SimpleNamespace(
            file_info=SimpleNamespace(abs_path=str(tmp_path / "pkg/gone.py"))
        )
    }

    assert _analyzer(parsed, {})._dynamic_import_files == set()
