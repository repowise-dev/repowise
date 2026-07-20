"""FP-class rescues: VS Code lifecycle, export aliases, bundler alias shims.

Each of these surfaced as a manually-verified false positive in the dogfood
audit: ``deactivate`` in ``extension.ts`` (called by the extension host),
``export { Local as Alias }`` symbols whose importers carry only the alias,
and Vite ``resolve.alias`` shim modules referenced only by the bundler
config.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from repowise.core.analysis.dead_code import DeadCodeAnalyzer, DeadCodeKind
from tests.unit.dead_code._helpers import _build_graph

_EXPORT_CONFIG = {"detect_unreachable_files": False, "detect_zombie_packages": False}


def _sym(name: str, **overrides) -> dict:
    base = {
        "name": name,
        "kind": "function",
        "visibility": "public",
        "decorators": [],
        "start_line": 1,
        "end_line": 5,
        "complexity_estimate": 1,
    }
    base.update(overrides)
    return base


def _file_node(symbols: list[dict], language: str = "typescript") -> dict:
    return {
        "is_entry_point": False,
        "is_test": False,
        "is_api_contract": False,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "language": language,
    }


def _parsed_stub(tmp_path: Path, rel: str, source: str) -> SimpleNamespace:
    abs_path = tmp_path / rel
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    abs_path.write_text(source, encoding="utf-8")
    return SimpleNamespace(file_info=SimpleNamespace(abs_path=str(abs_path)))


def _unused_export_names(analyzer: DeadCodeAnalyzer) -> set[str]:
    report = analyzer.analyze(_EXPORT_CONFIG)
    return {f.symbol_name for f in report.findings if f.kind == DeadCodeKind.UNUSED_EXPORT}


def test_vscode_lifecycle_exports_not_flagged():
    g = _build_graph(
        nodes={
            "src/extension.ts": _file_node(
                [_sym("activate"), _sym("deactivate"), _sym("helperFn", start_line=7, end_line=9)]
            ),
        },
    )
    names = _unused_export_names(DeadCodeAnalyzer(g, git_meta_map={}))
    assert "activate" not in names
    assert "deactivate" not in names
    # Only the lifecycle pair is exempt, not the rest of the file.
    assert "helperFn" in names


def test_deactivate_outside_extension_module_still_flagged():
    g = _build_graph(
        nodes={"src/util.ts": _file_node([_sym("deactivate")])},
    )
    names = _unused_export_names(DeadCodeAnalyzer(g, git_meta_map={}))
    assert "deactivate" in names


def test_export_alias_importer_rescues_local_symbol(tmp_path: Path):
    src = (
        "function ConversationHistoryWrapper() {}\n"
        "export { ConversationHistoryWrapper as ConversationHistory };\n"
    )
    parsed = {"web/history.tsx": _parsed_stub(tmp_path, "web/history.tsx", src)}
    g = _build_graph(
        nodes={
            "web/history.tsx": _file_node([_sym("ConversationHistoryWrapper")]),
            "web/page.tsx": _file_node([]),
        },
        edges=[
            (
                "web/page.tsx",
                "web/history.tsx",
                {"edge_type": "imports", "imported_names": ["ConversationHistory"]},
            ),
        ],
    )
    names = _unused_export_names(DeadCodeAnalyzer(g, git_meta_map={}, parsed_files=parsed))
    assert "ConversationHistoryWrapper" not in names


def test_export_alias_without_importer_still_flagged(tmp_path: Path):
    src = "function Widget() {}\nexport { Widget as PublicWidget };\n"
    parsed = {"web/widget.tsx": _parsed_stub(tmp_path, "web/widget.tsx", src)}
    g = _build_graph(
        nodes={
            "web/widget.tsx": _file_node([_sym("Widget")]),
            "web/page.tsx": _file_node([]),
        },
        edges=[
            (
                "web/page.tsx",
                "web/widget.tsx",
                {"edge_type": "imports", "imported_names": ["SomethingElse"]},
            ),
        ],
    )
    names = _unused_export_names(DeadCodeAnalyzer(g, git_meta_map={}, parsed_files=parsed))
    assert "Widget" in names


def test_bundler_alias_shim_rescued(tmp_path: Path):
    # Both alias shapes seen in the wild: a plain relative string and a
    # bare segment fed through ``path.resolve(here, ...)``.
    config_src = (
        "export default defineConfig({\n"
        "  resolve: { alias: [\n"
        "    { find: /^shiki$/, replacement: './src/shims/shiki.ts' },\n"
        '    { find: "next-themes", replacement: path.resolve(here, "src/shims/next-themes.ts") },\n'
        "  ] },\n"
        "});\n"
    )
    parsed = {
        "webview/vite.config.mts": _parsed_stub(tmp_path, "webview/vite.config.mts", config_src),
        "webview/src/shims/shiki.ts": _parsed_stub(
            tmp_path, "webview/src/shims/shiki.ts", "export const codeToHtml = () => '';\n"
        ),
        "webview/src/shims/next-themes.ts": _parsed_stub(
            tmp_path, "webview/src/shims/next-themes.ts", "export const ThemeProvider = 0;\n"
        ),
    }
    g = _build_graph(
        nodes={
            "webview/src/shims/shiki.ts": _file_node([_sym("codeToHtml")]),
        },
    )
    analyzer = DeadCodeAnalyzer(g, git_meta_map={}, parsed_files=parsed)
    assert "webview/src/shims/shiki.ts" in analyzer._bundler_alias_targets
    assert "webview/src/shims/next-themes.ts" in analyzer._bundler_alias_targets

    names = _unused_export_names(analyzer)
    assert "codeToHtml" not in names

    file_report = analyzer.analyze({"detect_zombie_packages": False})
    unreachable = {
        f.file_path for f in file_report.findings if f.kind == DeadCodeKind.UNREACHABLE_FILE
    }
    assert "webview/src/shims/shiki.ts" not in unreachable
