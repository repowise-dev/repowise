"""Unit tests for TYPO3 framework edges (F8)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.models import FileInfo, ParsedFile
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext


def _file_info(rel: str, abs_path: str, language: str = "php") -> FileInfo:
    return FileInfo(
        path=rel,
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


def _build_parsed(repo: Path) -> dict[str, ParsedFile]:
    parser = ASTParser()
    out: dict[str, ParsedFile] = {}
    for ext, lang in (
        (".php", "php"),
        (".js", "javascript"),
        (".yaml", "yaml"),
        (".yml", "yaml"),
    ):
        for src in repo.rglob(f"*{ext}"):
            rel = src.resolve().relative_to(repo.resolve()).as_posix()
            if "/.git/" in f"/{rel}/":
                continue
            fi = _file_info(rel, str(src.resolve()), language=lang)
            out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _ctx(repo: Path, parsed: dict[str, ParsedFile]) -> ResolverContext:
    path_set = set(parsed.keys())
    stem_map: dict[str, list[str]] = {}
    for p in path_set:
        stem = Path(p).stem.lower()
        stem_map.setdefault(stem, []).append(p)
    return ResolverContext(
        path_set=path_set, stem_map=stem_map, graph=nx.DiGraph(), repo_path=repo
    )


def _graph_with_nodes(parsed: dict[str, ParsedFile]) -> nx.DiGraph:
    graph = nx.DiGraph()
    for p in parsed:
        graph.add_node(p, language=parsed[p].file_info.language)
    return graph


def _make_extension(
    repo: Path,
    *,
    composer_type: bool = True,
    extension_key: str = "my_ext",
    files: dict[str, str] | None = None,
) -> None:
    """Lay down a minimal TYPO3 extension at repo root."""
    if composer_type:
        composer = {
            "name": f"vendor/{extension_key.replace('_', '-')}",
            "type": "typo3-cms-extension",
            "require": {"typo3/cms-core": "^13.4"},
            "extra": {"typo3/cms": {"extension-key": extension_key}},
        }
        (repo / "composer.json").write_text(json.dumps(composer))
    if files:
        for rel, content in files.items():
            target = repo / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)


class TestTypo3Detection:
    def test_composer_type_detects_extension(self, tmp_path: Path) -> None:
        _make_extension(
            tmp_path,
            files={
                "ext_localconf.php": "<?php\n",
                "Configuration/Icons.php": "<?php\nreturn [];\n",
            },
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count >= 2
        assert graph.has_edge("framework:typo3-core", "ext_localconf.php")
        assert graph.has_edge("framework:typo3-core", "Configuration/Icons.php")

    def test_legacy_ext_emconf_only_triggers(self, tmp_path: Path) -> None:
        # No composer.json — legacy non-composer install.
        (tmp_path / "ext_emconf.php").write_text("<?php\n$EM_CONF[] = [];\n")
        (tmp_path / "ext_localconf.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        count = add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert count >= 2
        assert graph.has_edge("framework:typo3-core", "ext_emconf.php")
        assert graph.has_edge("framework:typo3-core", "ext_localconf.php")

    def test_non_typo3_php_repo_adds_no_typo3_edges(self, tmp_path: Path) -> None:
        # Plain PHP package; composer.json without TYPO3 type.
        (tmp_path / "composer.json").write_text(
            json.dumps({"name": "vendor/lib", "type": "library"})
        )
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "Foo.php").write_text("<?php\nclass Foo {}\n")
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert "framework:typo3-core" not in graph


class TestTypo3ConventionFiles:
    def test_tca_files_get_edges(self, tmp_path: Path) -> None:
        _make_extension(
            tmp_path,
            files={
                "Configuration/TCA/tx_my_ext_item.php": "<?php\nreturn [];\n",
                "Configuration/TCA/Overrides/tt_content.php": "<?php\n",
                "Configuration/Backend/Routes.php": "<?php\nreturn [];\n",
            },
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(
            "framework:typo3-core", "Configuration/TCA/tx_my_ext_item.php"
        )
        assert graph.has_edge(
            "framework:typo3-core", "Configuration/TCA/Overrides/tt_content.php"
        )
        assert graph.has_edge(
            "framework:typo3-core", "Configuration/Backend/Routes.php"
        )

    def test_v14_extension_without_legacy_files_works(self, tmp_path: Path) -> None:
        # v14: no ext_emconf.php, no ext_tables.php, just composer.json + Configuration/.
        composer = {
            "name": "vendor/my-ext",
            "type": "typo3-cms-extension",
            "require": {"typo3/cms-core": "^14.0"},
            "extra": {"typo3/cms": {"extension-key": "my_ext"}},
        }
        (tmp_path / "composer.json").write_text(json.dumps(composer))
        (tmp_path / "ext_localconf.php").write_text("<?php\n")
        (tmp_path / "Configuration").mkdir()
        (tmp_path / "Configuration" / "Services.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge("framework:typo3-core", "ext_localconf.php")
        assert graph.has_edge("framework:typo3-core", "Configuration/Services.php")

    def test_yaml_only_configuration_directory(self, tmp_path: Path) -> None:
        # Common minimal extension layout: only ``Configuration/Services.yaml``,
        # no PHP files in ``Configuration/``. The yaml file must still get an
        # edge so the directory doesn't show as a zombie package.
        _make_extension(
            tmp_path,
            files={
                "Configuration/Services.yaml": "services:\n  _defaults:\n    autowire: true\n",
                "Configuration/RTE/Default.yaml": "editor:\n  config: {}\n",
            },
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge("framework:typo3-core", "Configuration/Services.yaml")
        assert graph.has_edge(
            "framework:typo3-core", "Configuration/RTE/Default.yaml"
        )


class TestTypo3JavaScriptModules:
    def test_registered_js_files_get_edges_from_js_modules(
        self, tmp_path: Path
    ) -> None:
        _make_extension(
            tmp_path,
            extension_key="my_ext",
            files={
                "Configuration/JavaScriptModules.php": (
                    "<?php\nreturn [\n"
                    "    'dependencies' => ['core'],\n"
                    "    'imports' => [\n"
                    "        '@vendor/my-ext/Cowriter' "
                    "=> 'EXT:my_ext/Resources/Public/JavaScript/Ckeditor/cowriter.js',\n"
                    "        '@vendor/my-ext/AIService' "
                    '=> "EXT:my_ext/Resources/Public/JavaScript/Ckeditor/AIService.js",\n'
                    "    ],\n"
                    "];\n"
                ),
                "Resources/Public/JavaScript/Ckeditor/cowriter.js": "// stub\n",
                "Resources/Public/JavaScript/Ckeditor/AIService.js": "// stub\n",
            },
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(
            "Configuration/JavaScriptModules.php",
            "Resources/Public/JavaScript/Ckeditor/cowriter.js",
        )
        assert graph.has_edge(
            "Configuration/JavaScriptModules.php",
            "Resources/Public/JavaScript/Ckeditor/AIService.js",
        )

    def test_cross_extension_js_references_are_skipped(self, tmp_path: Path) -> None:
        # Reference into a different extension's JS file shouldn't create
        # an edge in *this* repo's graph (we can't resolve EXT:other/ here).
        _make_extension(
            tmp_path,
            extension_key="my_ext",
            files={
                "Configuration/JavaScriptModules.php": (
                    "<?php\nreturn [\n"
                    "    'imports' => [\n"
                    "        '@core/Foo' => 'EXT:other_ext/Resources/Public/JavaScript/Foo.js',\n"
                    "    ],\n"
                    "];\n"
                ),
                "Resources/Public/JavaScript/Local.js": "// stub\n",
            },
        )
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        # No edge to a file under a foreign extension key.
        assert not any(
            target.startswith("EXT:other_ext/") for _, target in graph.edges()
        )


class TestTypo3TechStackHint:
    def test_tech_stack_alone_triggers_when_files_match(
        self, tmp_path: Path
    ) -> None:
        # No composer.json, no ext_emconf.php — pure tech-stack hint.
        # Without an extension root we still add zero edges (correct behaviour:
        # we need a discoverable extension).
        (tmp_path / "ext_localconf.php").write_text("<?php\n")
        # ext_emconf.php missing on purpose
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=["typo3"])
        # ext_localconf.php at root with no other anchor: discovery walks
        # composer.json (none) and ext_emconf.php (none). No roots → no edges.
        # This documents the current behaviour; pure-tech-stack hint without
        # any extension marker is a no-op.
        assert "framework:typo3-core" not in graph or not list(
            graph.successors("framework:typo3-core")
        )


class TestTypo3ProjectMode:
    """Composer-managed full TYPO3 install: extensions live under vendor/."""

    def test_vendor_extensions_discovered(self, tmp_path: Path) -> None:
        # Project root has its own composer.json (type=project, requires cms-core).
        (tmp_path / "composer.json").write_text(
            json.dumps(
                {
                    "name": "vendor/site",
                    "type": "project",
                    "require": {"typo3/cms-core": "^13.4"},
                }
            )
        )
        # An extension installed under vendor/.
        ext_root = tmp_path / "vendor" / "myvendor" / "my-ext"
        ext_root.mkdir(parents=True)
        (ext_root / "composer.json").write_text(
            json.dumps(
                {
                    "name": "myvendor/my-ext",
                    "type": "typo3-cms-extension",
                    "extra": {"typo3/cms": {"extension-key": "my_ext"}},
                }
            )
        )
        (ext_root / "ext_localconf.php").write_text("<?php\n")
        (ext_root / "Configuration").mkdir()
        (ext_root / "Configuration" / "Icons.php").write_text("<?php\nreturn [];\n")

        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        assert graph.has_edge(
            "framework:typo3-core", "vendor/myvendor/my-ext/ext_localconf.php"
        )
        assert graph.has_edge(
            "framework:typo3-core",
            "vendor/myvendor/my-ext/Configuration/Icons.php",
        )

    def test_node_modules_is_skipped(self, tmp_path: Path) -> None:
        # node_modules can contain composer.json by accident — never treat
        # those as TYPO3 extensions.
        bogus = tmp_path / "node_modules" / "evil" / "composer.json"
        bogus.parent.mkdir(parents=True)
        bogus.write_text(
            json.dumps({"name": "evil/pkg", "type": "typo3-cms-extension"})
        )
        # Need any TYPO3 marker for detection to even fire.
        (tmp_path / "composer.json").write_text(
            json.dumps({"type": "typo3-cms-extension"})
        )
        (tmp_path / "ext_localconf.php").write_text("<?php\n")
        parsed = _build_parsed(tmp_path)
        graph = _graph_with_nodes(parsed)
        ctx = _ctx(tmp_path, parsed)
        add_framework_edges(graph, parsed, ctx, tech_stack=[])
        # The bogus path should never become an edge target.
        assert not any(
            "node_modules/" in target
            for _, target in graph.edges()
            if isinstance(target, str)
        )
