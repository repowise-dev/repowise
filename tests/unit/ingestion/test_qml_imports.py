"""QML import extraction and resolution (lightweight regex tier, #727).

QML has no published tree-sitter grammar, so it ships at the lightweight
tier: real file-to-file import edges, no symbol claims. These tests pin
both what the tier captures and what it refuses to — module imports,
quoted relative references, comment-stripping, dedup, Qt builtins
resolving external, and the qmldir module-name index.
"""

from __future__ import annotations

from pathlib import Path

import networkx as nx

from repowise.core.ingestion.lightweight_imports import extract_lightweight_imports
from repowise.core.ingestion.lightweight_imports.qml import extract_qml_imports
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.context import ResolverContext


def _modules(source: str) -> list[str]:
    return [i.module_path for i in extract_qml_imports(source)]


def _ctx(repo: Path | None, files: dict[str, str]) -> ResolverContext:
    """ResolverContext over *files* ({path: content}); writes them under *repo*."""
    if repo is not None:
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
    stem_map: dict[str, list[str]] = {}
    for p in files:
        stem = p.rsplit("/", 1)[-1].rsplit(".", 1)[0].lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=set(files),
        stem_map=stem_map,
        graph=nx.DiGraph(),
        repo_path=repo,
    )


def _resolve(module: str, importer: str, files: dict[str, str], repo: Path | None = None) -> str | None:
    ctx = _ctx(repo, files)
    return resolve_import(module, importer, "qml", ctx)


class TestExtraction:
    def test_module_import_with_version(self) -> None:
        assert _modules("import QtQuick 2.15\n") == ["QtQuick"]

    def test_dotted_module_import(self) -> None:
        assert _modules("import QtQuick.Controls 2.15\n") == ["QtQuick.Controls"]

    def test_module_import_with_alias(self) -> None:
        assert _modules("import org.kde.kirigami as K\n") == ["org.kde.kirigami"]

    def test_quoted_directory_import(self) -> None:
        assert _modules('import "components"\n') == ['"components"']

    def test_quoted_script_import(self) -> None:
        assert _modules('import "js/app.js" as AppScript\n') == ['"js/app.js"']

    def test_relative_parent_import(self) -> None:
        assert _modules('import "../shared"\n') == ['"../shared"']

    def test_line_comment_import_is_not_an_edge(self) -> None:
        assert _modules("// import QtQuick\nimport QtQuick 2.15\n") == ["QtQuick"]

    def test_block_comment_import_is_not_an_edge(self) -> None:
        assert _modules("/* import \"old.js\" */\nimport QtQuick 2.15\n") == ["QtQuick"]

    def test_duplicate_import_recorded_once(self) -> None:
        assert _modules("import QtQuick 2.15\nimport QtQuick 2.15\n") == ["QtQuick"]

    def test_module_and_path_imports_coexist(self) -> None:
        assert _modules('import QtQuick 2.15\nimport "components"\n') == [
            "QtQuick",
            '"components"',
        ]

    def test_extract_via_dispatch(self) -> None:
        """The parser's no-grammar path must reach the QML extractor."""
        from datetime import datetime

        from repowise.core.ingestion.models import FileInfo

        fi = FileInfo(
            path="ui/Main.qml",
            abs_path="/tmp/ui/Main.qml",
            language="qml",  # type: ignore[arg-type]
            size_bytes=0,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        imports = extract_lightweight_imports(
            fi, b'import QtQuick 2.15\nimport "components"\n'
        )
        assert [i.module_path for i in imports] == ["QtQuick", '"components"']


class TestResolution:
    def test_module_import_resolves_via_qmldir(self, tmp_path) -> None:
        files = {
            "ui/Main.qml": "import MyCompany.Controls 1.0\n",
            "controls/qmldir": "module MyCompany.Controls\n",
        }
        assert _resolve("MyCompany.Controls", "ui/Main.qml", files, tmp_path) == "controls/qmldir"

    def test_qt_builtin_module_resolves_external(self, tmp_path) -> None:
        files = {"ui/Main.qml": "import QtQuick 2.15\n"}
        assert _resolve("QtQuick", "ui/Main.qml", files, tmp_path) is None

    def test_unknown_module_resolves_external(self, tmp_path) -> None:
        files = {"ui/Main.qml": "import SomethingElse 1.0\n"}
        assert _resolve("SomethingElse", "ui/Main.qml", files, tmp_path) is None

    def test_directory_import_resolves_to_qmldir(self) -> None:
        files = {
            "ui/Main.qml": 'import "components"\n',
            "ui/components/qmldir": "module ui.components\n",
        }
        assert _resolve('"components"', "ui/Main.qml", files) == "ui/components/qmldir"

    def test_script_import_resolves_to_file(self) -> None:
        files = {
            "ui/Main.qml": 'import "js/app.js" as AppScript\n',
            "ui/js/app.js": "function helper() {}\n",
        }
        assert _resolve('"js/app.js"', "ui/Main.qml", files) == "ui/js/app.js"

    def test_parent_relative_directory(self) -> None:
        files = {
            "ui/pages/Page.qml": 'import "../shared"\n',
            "ui/shared/qmldir": "module shared\n",
        }
        assert _resolve('"../shared"', "ui/pages/Page.qml", files) == "ui/shared/qmldir"

    def test_escape_out_of_repo_yields_nothing(self) -> None:
        files = {"ui/Main.qml": 'import "../../outside"\n'}
        assert _resolve('"../../outside"', "ui/Main.qml", files) is None

    def test_unique_suffix_fallback_for_moved_script_dir(self) -> None:
        # `import "js/app.js"` from ui/pages/Page.qml: dir-join gives
        # ui/pages/js/app.js (miss), but the file lives at ui/js/app.js —
        # unique /js/app.js suffix links it.
        files = {
            "ui/pages/Page.qml": 'import "js/app.js" as A\n',
            "ui/js/app.js": "x\n",
            "ui/other/also/app.js": "y\n",
        }
        # Two files end in /js/app.js? No — ui/other/also/app.js does not end
        # with "/js/app.js", so this is already unique.
        assert _resolve('"js/app.js"', "ui/pages/Page.qml", files) == "ui/js/app.js"
        files["ui/js/app.js"] = "x\n"
        files["ui/vendor/js/app.js"] = "z\n"
        # Now two files end in /js/app.js — a tie, no guessed edge.
        assert _resolve('"js/app.js"', "ui/pages/Page.qml", files) is None

    def test_bare_name_without_slash_never_suffix_matches(self) -> None:
        # A lone "utils" could be anything — matching by suffix would be a
        # guess (same policy as the HTML asset resolver).
        files = {
            "ui/Main.qml": 'import "utils"\n',
            "ui/js/utils.js": "x\n",
        }
        assert _resolve('"utils"', "ui/Main.qml", files) is None
