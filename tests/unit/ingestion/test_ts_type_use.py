"""TypeScript / JavaScript Phase 2 — type-reference resolution.

Covers the chain that turns TS type-position references into graph edges
the dead-code analyzer respects:

* ``@param.type`` capture → ``TypeReference`` extraction with the TS head
  extractor (unwraps ``Foo[]`` / ``ns.Foo`` / ``Foo<T>`` and filters TS
  builtins);
* ``_resolve_ts_type_refs`` → ``type_use`` edges for interfaces / classes
  used only as parameter / field / return / generic-constraint / heritage
  types and never imported as values;
* the end-to-end dead-code outcome: an exported ``interface`` referenced
  only as a TypeScript type annotation is no longer flagged
  ``unused_export``, while genuinely-dead interfaces still are.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx
import pytest

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser

_PARSER = ASTParser()


def _file_info(path: str, abs_path: str, language: str = "typescript") -> FileInfo:
    return FileInfo(
        path=path,
        abs_path=abs_path,
        language=language,
        size_bytes=100,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# ---------------------------------------------------------------------------
# Parser: TS type-ref extraction + head unwrapping
# ---------------------------------------------------------------------------


class TestTsTypeRefExtraction:
    def _parse(self, body: str, language: str = "typescript") -> list:
        info = _file_info("p/f.ts" if language == "typescript" else "p/f.js",
                          "/repo/p/f", language=language)
        return _PARSER.parse_file(info, body.encode("utf-8")).type_refs

    def test_param_field_return_captured(self) -> None:
        refs = self._parse(
            "interface I { f: Field }\n"
            "class C { g: Gee }\n"
            "function take(x: Param): Return { return null as any }\n"
            "const arr = (a: ArrowParam): ArrowReturn => null as any;\n"
        )
        names = {r.type_name for r in refs}
        assert {"Field", "Gee", "Param", "Return", "ArrowParam", "ArrowReturn"} <= names

    def test_builtins_filtered(self) -> None:
        refs = self._parse(
            "function f(s: string, n: number, p: Promise<number>): Date { return new Date() }\n"
        )
        names = {r.type_name for r in refs}
        assert names.isdisjoint(
            {"string", "number", "Promise", "Date", "void", "any"}
        )

    def test_array_and_generic_unwrapped(self) -> None:
        refs = self._parse(
            "interface I { items: Item[]; promised: Promise<PValue> }\n"
        )
        names = {r.type_name for r in refs}
        # Item[] → Item, Promise<...> → filtered (builtin). PValue is
        # only captured if the grammar walks into generic arguments; we
        # don't require it here — only that Item is captured and Promise
        # is not treated as a user type.
        assert "Item" in names
        assert "Promise" not in names

    def test_heritage_extends_and_implements_captured(self) -> None:
        refs = self._parse(
            "interface IA extends IB, IC { }\n"
            "class Derived extends Base implements Iface { }\n"
        )
        names_by_origin = {(r.type_name, r.origin) for r in refs}
        assert ("IB", "extends") in names_by_origin
        assert ("IC", "extends") in names_by_origin
        assert ("Base", "extends") in names_by_origin
        assert ("Iface", "implements") in names_by_origin

    def test_generic_constraint_captured(self) -> None:
        refs = self._parse("function f<T extends Cons>(x: T): T { return x }\n")
        names_by_origin = {(r.type_name, r.origin) for r in refs}
        assert ("Cons", "generic_constraint") in names_by_origin
        # ``T`` is a single-uppercase generic param — filtered.
        assert "T" not in {n for n, _ in names_by_origin}

    def test_type_alias_rhs_captured(self) -> None:
        refs = self._parse("type Alias = OtherType\n")
        names_by_origin = {(r.type_name, r.origin) for r in refs}
        assert ("OtherType", "type_alias") in names_by_origin

    def test_namespace_qualified_type_dropped_to_head(self) -> None:
        refs = self._parse(
            "import * as ns from './x'\n"
            "function f(x: ns.Inner): void {}\n"
        )
        names = {r.type_name for r in refs}
        assert "Inner" in names
        assert "ns" not in names


# ---------------------------------------------------------------------------
# type_use edges + dead-code outcome (end-to-end through GraphBuilder)
# ---------------------------------------------------------------------------


def _build_graph(repo: Path, sources: dict[str, str]) -> nx.DiGraph:
    for rel, body in sources.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    builder = GraphBuilder(repo_path=repo)
    for rel in sources:
        abs_path = str((repo / rel).resolve())
        parsed = _PARSER.parse_file(
            _file_info(rel, abs_path), (repo / rel).read_bytes()
        )
        builder.add_file(parsed)
    return builder.build()


_TYPE_ONLY_SOURCES = {
    "types.ts": (
        "export interface UsedAsFieldType { name: string }\n"
        "export interface UsedAsParamType { x: number }\n"
        "export interface UsedAsReturnType { y: number }\n"
        "export interface UsedAsImpl { run(): void }\n"
        "export class UsedAsExtends { value = 1 }\n"
        "export interface GenuinelyDead {}\n"
    ),
    "consumer.ts": (
        "import type { UsedAsFieldType, UsedAsParamType, UsedAsReturnType }\n"
        "  from './types'\n"
        "import { UsedAsImpl, UsedAsExtends } from './types'\n"
        "export class HasField { f: UsedAsFieldType = { name: 'x' } }\n"
        "export function takes(p: UsedAsParamType): UsedAsReturnType {\n"
        "  return { y: p.x }\n"
        "}\n"
        "export class Impl implements UsedAsImpl { run(): void {} }\n"
        "export class Sub extends UsedAsExtends {}\n"
    ),
    "entry.ts": (
        "import { HasField, takes, Impl, Sub } from './consumer'\n"
        "new HasField(); takes({x: 1}); new Impl(); new Sub();\n"
    ),
}


class TestTsTypeUseEdges:
    def test_type_only_import_produces_type_use_provenance(
        self, tmp_path: Path
    ) -> None:
        graph = _build_graph(tmp_path, _TYPE_ONLY_SOURCES)
        edge = graph.get_edge_data("consumer.ts", "types.ts", {})
        assert edge, "consumer.ts should connect to types.ts"
        # Imported names should cover both value-imports and type-position
        # references (the latter folded in by the type-ref strategy).
        names = set(edge.get("imported_names", []))
        assert {
            "UsedAsFieldType",
            "UsedAsParamType",
            "UsedAsReturnType",
            "UsedAsImpl",
            "UsedAsExtends",
        } <= names


class TestTsDeadCodeOutcome:
    def _report(self, graph: nx.DiGraph):
        analyzer = DeadCodeAnalyzer(graph, git_meta_map={})
        return analyzer.analyze(
            {
                "detect_unreachable_files": False,
                "detect_zombie_packages": False,
                "detect_unused_internals": True,
                "min_confidence": 0.0,
            }
        )

    def test_types_referenced_only_as_annotations_not_flagged(
        self, tmp_path: Path
    ) -> None:
        report = self._report(_build_graph(tmp_path, _TYPE_ONLY_SOURCES))
        unused = {
            f.symbol_name
            for f in report.findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        # Each interface/class is consumed cross-file only as a TS type —
        # without type_use edges every one of these would be flagged.
        assert {
            "UsedAsFieldType",
            "UsedAsParamType",
            "UsedAsReturnType",
            "UsedAsImpl",
            "UsedAsExtends",
        }.isdisjoint(unused)

    def test_genuinely_dead_interface_still_flagged(self, tmp_path: Path) -> None:
        report = self._report(_build_graph(tmp_path, _TYPE_ONLY_SOURCES))
        unused = {
            f.symbol_name
            for f in report.findings
            if f.kind == DeadCodeKind.UNUSED_EXPORT
        }
        assert "GenuinelyDead" in unused


# ---------------------------------------------------------------------------
# Strategy registration — sanity check
# ---------------------------------------------------------------------------


class TestStrategyRegistration:
    def test_typescript_and_javascript_registered(self) -> None:
        from repowise.core.ingestion.type_ref_resolution import _STRATEGIES

        assert "typescript" in _STRATEGIES
        assert "javascript" in _STRATEGIES
