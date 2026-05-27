"""Phase 5B — C/C++ type-reference resolution + WASM export markers.

Covers the chain that stops Hugo's embedded C → WASM modules
(``internal/warpc/genavif``, ``deps/parson``) from reading as dead:

* ``@param.type`` capture → ``TypeReference`` extraction with the C head
  extractor (filters primitive builtins / stdlib typedefs, unwraps named
  ``struct``/``class`` specifiers, drops the ``NS::`` qualifier for C++);
* ``_resolve_c_type_refs`` → ``type_use`` edges for a header struct used
  only as a field / parameter / return type across a ``#include``;
* WASM / emscripten export markers (``EMSCRIPTEN_KEEPALIVE`` /
  ``__attribute__((export_name(...)))`` / ``WASM_EXPORT``) → the symbol is
  marked ``is_exported_symbol`` so the unused-export pass skips it;
* the honesty guard: a genuinely-dead ``static`` helper is still flagged.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _file_info(path: str, abs_path: str) -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language="c",
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# A tiny vendored-C-style module: a header declaring structs, a .c that
# includes it and uses those structs as field/param/return types, plus a
# WASM-exported entry and a genuinely-dead static helper.
_SOURCES: dict[str, str] = {
    "parson.h": (
        "#ifndef PARSON_H\n#define PARSON_H\n"
        "typedef struct json_value_t JSON_Value;\n"
        "typedef struct json_object_t JSON_Object;\n"
        "struct json_object_t { JSON_Value *values; int count; };\n"
        "#endif\n"
    ),
    "parson.c": (
        '#include "parson.h"\n\n'
        "/* json_count uses JSON_Object as a param — reached only through the "
        "#include; it is itself genuinely unused (no caller, no export marker) "
        "and must stay flagged. */\n"
        "int json_count(JSON_Object *obj) { return obj->count; }\n"
    ),
    "avif.c": (
        '#include "parson.h"\n'
        "#define EMSCRIPTEN_KEEPALIVE __attribute__((used))\n\n"
        "/* exported across the WASM boundary — no in-binary caller. */\n"
        "EMSCRIPTEN_KEEPALIVE int avif_encode(JSON_Object *cfg) {\n"
        "    return cfg->count;\n"
        "}\n"
    ),
}


def _build_graph(repo: Path) -> nx.DiGraph:
    for rel, body in _SOURCES.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")

    builder = GraphBuilder(repo_path=repo)
    for rel in _SOURCES:
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(_file_info(rel, abs_path), (repo / rel).read_bytes())
        builder.add_file(parsed)
    return builder.build()


# ---------------------------------------------------------------------------
# Parser: C type-ref extraction + head unwrapping
# ---------------------------------------------------------------------------


class TestCTypeRefExtraction:
    def _parse(self, body: str) -> list:
        info = _file_info("p/f.c", "/repo/p/f.c")
        return _PARSER.parse_file(info, body.encode("utf-8")).type_refs

    def test_struct_field_and_param_types_captured(self) -> None:
        refs = self._parse(
            'struct Box { Widget *w; };\n'
            "int use(Gadget *g) { return 0; }\n"
        )
        names = {r.type_name for r in refs}
        assert {"Widget", "Gadget"} <= names

    def test_primitive_and_stdlib_builtins_filtered(self) -> None:
        refs = self._parse(
            "int f(int n, char *s, size_t len, unsigned long u) { return 0; }\n"
        )
        names = {r.type_name for r in refs}
        assert names.isdisjoint({"int", "char", "size_t", "unsigned", "long"})

    def test_named_struct_specifier_unwrapped(self) -> None:
        refs = self._parse("int f(struct Thing *t) { return 0; }\n")
        assert "Thing" in {r.type_name for r in refs}


# ---------------------------------------------------------------------------
# type_use edges + WASM export markers (end-to-end through GraphBuilder)
# ---------------------------------------------------------------------------


class TestCTypeUseEdges:
    def test_header_struct_used_across_include(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        # parson.c references JSON_Object (defined in parson.h) as a param.
        edge = graph.get_edge_data("parson.c", "parson.h", {})
        assert "JSON_Object" in edge.get("imported_names", [])


class TestWasmExportMarkers:
    def test_emscripten_keepalive_marks_exported(self, tmp_path: Path) -> None:
        graph = _build_graph(tmp_path)
        node = graph.nodes.get("avif.c::avif_encode")
        assert node is not None, "avif_encode symbol should be in the graph"
        assert node.get("is_exported_symbol") is True


# ---------------------------------------------------------------------------
# Honesty guard — genuinely-dead code must still surface
# ---------------------------------------------------------------------------


class TestHonestyGuard:
    def test_genuinely_unused_export_still_flagged(self, tmp_path: Path) -> None:
        # json_count carries no export marker and has no caller — the
        # WASM-marker work must not silence ordinary dead exports.
        graph = _build_graph(tmp_path)
        findings = DeadCodeAnalyzer(graph).analyze().findings
        dead_exports = {
            f.symbol_name
            for f in findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "json_count" in dead_exports

    def test_exported_wasm_fn_not_flagged(self, tmp_path: Path) -> None:
        # avif_encode carries EMSCRIPTEN_KEEPALIVE → exported across the WASM
        # boundary → must NOT be flagged despite having no in-binary caller.
        graph = _build_graph(tmp_path)
        findings = DeadCodeAnalyzer(graph).analyze().findings
        flagged = {f.symbol_name for f in findings}
        assert "avif_encode" not in flagged
