"""Vite ``resolve.alias`` reading, and import resolution through it."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from repowise.core.ingestion import wire_tsconfig_resolver
from repowise.core.ingestion.graph import GraphBuilder
from repowise.core.ingestion.models import FileInfo, Import, ParsedFile
from repowise.core.ingestion.tsconfig_resolver import TsconfigResolver
from repowise.core.ingestion.vite_aliases import is_vite_config, vite_aliases

_ROOT = Path("/repo/app")


def _read(config: str) -> list[tuple[str, str | None]]:
    return [
        (find, None if target is None else target.as_posix())
        for find, target in vite_aliases(config.encode(), _ROOT)
    ]


def _one(replacement: str) -> list[tuple[str, str | None]]:
    return _read(
        f"export default defineConfig({{ resolve: {{ alias: {{ '@': {replacement} }} }} }})"
    )


class TestReadAliases:
    @pytest.mark.parametrize(
        "replacement",
        [
            'import.meta.dirname + "/src"',
            "__dirname + '/src'",
            "`${import.meta.dirname}/src`",
            'path.resolve(__dirname, "./src")',
            'path.resolve(__dirname, "src")',
            "resolve(import.meta.dirname, 'src')",
            "path.join(process.cwd(), 'src')",
            "path.resolve('src')",
            'fileURLToPath(new URL("./src", import.meta.url))',
            "url.fileURLToPath(new URL('./src', import.meta.url))",
            "new URL('./src', import.meta.url).pathname",
            "'/src'",
            "path.join(__dirname, '/src')",
        ],
    )
    def test_literal_forms_name_the_src_directory(self, replacement: str) -> None:
        [(find, target)] = _one(replacement)
        assert find == "@"
        assert target == "/repo/app/src"

    @pytest.mark.parametrize(
        "replacement",
        [
            "srcDir",  # a variable
            "path.resolve(root, 'src')",  # a computed base
            "getSrc()",  # a helper call
            "path.resolve(__dirname, process.env.SRC)",  # an env-chosen segment
            "`${base}/src`",  # a template over a variable
            "require.resolve('some-pkg')",  # a package, not a directory
            "'vue/dist/vue.esm-bundler.js'",  # a bare package redirect
            "path.resolve(__dirname, '/abs')",  # leaves the repository
            "helpers.resolve('src')",  # not a path module
            "__dirname + 'src'",  # glues onto the directory name
            "'./src'",  # Vite keeps it relative to the importer
        ],
    )
    def test_dynamic_forms_claim_the_find_without_a_target(self, replacement: str) -> None:
        assert _one(replacement) == [("@", None)]

    def test_url_escapes_are_decoded(self) -> None:
        assert _one("fileURLToPath(new URL('./my%20src', import.meta.url))") == [
            ("@", "/repo/app/my src")
        ]

    def test_trailing_slash_dropped_only_when_both_sides_carry_one(self) -> None:
        assert _one("'/src/'") == [("@", "/repo/app/src")]
        both = "export default { resolve: { alias: { '@/': '/src/' } } }"
        assert _read(both) == [("@", "/repo/app/src")]
        find_only = (
            "export default { resolve: { alias: { '@/': path.resolve(__dirname, 'src') } } }"
        )
        assert _read(find_only) == [("@/", "/repo/app/src")]

    def test_repeated_key_keeps_its_last_value(self) -> None:
        config = "export default { resolve: { alias: { '@': '/old', '~': '/lib', '@': '/new' } } }"
        assert _read(config) == [("@", "/repo/app/new"), ("~", "/repo/app/lib")]

    def test_array_form_in_order_and_regex_find_skipped(self) -> None:
        config = """
        export default {
          resolve: {
            alias: [
              { find: '~', replacement: path.resolve(__dirname, 'lib') },
              { find: /^#/, replacement: 'x' },
              { find: "@", replacement: fileURLToPath(new URL('./src', import.meta.url)) },
            ],
          },
        }
        """
        assert [f for f, _ in _read(config)] == ["~", "@"]

    def test_object_form_keeps_order_and_skips_spreads(self) -> None:
        config = """
        const base = {}
        export default {
          resolve: {
            alias: {
              ...base.aliases,
              "@components": path.resolve(__dirname, "src/components"),
              '@': path.resolve(__dirname, 'src'),  // trailing comment
              [computed]: path.resolve(__dirname, 'x'),
            },
          },
        }
        """
        assert [f for f, _ in _read(config)] == ["@components", "@"]

    def test_alias_named_by_a_variable_is_skipped(self) -> None:
        assert _read("export default { resolve: { alias: aliases } }") == []

    def test_config_names(self) -> None:
        assert is_vite_config("vite.config.ts")
        assert is_vite_config("vitest.config.mts")
        assert is_vite_config("vite.config.mjs")
        assert not is_vite_config("vite.config.json")
        assert not is_vite_config("webpack.config.js")
        assert not is_vite_config("vite.config.backup.ts")


def _resolver(tmp_path: Path, files: dict[str, str], path_set: set[str]) -> TsconfigResolver:
    for rel, text in files.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    return TsconfigResolver(repo_path=tmp_path, path_set=path_set)


_VITE = "export default defineConfig({ resolve: { alias: { '@': %s } } })\n"


class TestResolveThroughVite:
    def test_at_alias_resolves_without_a_tsconfig(self, tmp_path: Path) -> None:
        resolver = _resolver(
            tmp_path,
            {"vite.config.ts": _VITE % 'import.meta.dirname + "/src"'},
            {"src/components/Button.tsx", "src/lib/index.ts", "src/main.ts"},
        )
        importer = str(tmp_path / "src/main.ts")
        assert resolver.resolve("@/components/Button", importer) == "src/components/Button.tsx"
        assert resolver.resolve("@/lib", importer) == "src/lib/index.ts"
        # ``@scope/pkg`` is a package, not ``@`` + ``/``.
        assert resolver.resolve("@vue/runtime", importer) is None
        assert tmp_path / "vite.config.ts" in resolver.read_paths

    def test_vitest_config_is_read_the_same_way(self, tmp_path: Path) -> None:
        resolver = _resolver(
            tmp_path,
            {"vitest.config.mts": _VITE % 'path.resolve(__dirname, "./src")'},
            {"src/util.ts"},
        )
        assert resolver.resolve("@/util", str(tmp_path / "test/util.test.ts")) == "src/util.ts"

    def test_tsconfig_paths_take_precedence(self, tmp_path: Path) -> None:
        resolver = _resolver(
            tmp_path,
            {
                "tsconfig.json": json.dumps({"compilerOptions": {"paths": {"@/*": ["./app/*"]}}}),
                "vite.config.ts": _VITE % "'/src'",
            },
            {"app/x.ts", "src/x.ts", "src/only.ts"},
        )
        importer = str(tmp_path / "main.ts")
        assert resolver.resolve("@/x", importer) == "app/x.ts"
        # Where the tsconfig maps to nothing, the Vite alias still answers.
        assert resolver.resolve("@/only", importer) == "src/only.ts"

    def test_alias_applies_only_under_its_config(self, tmp_path: Path) -> None:
        resolver = _resolver(
            tmp_path,
            {"packages/web/vite.config.js": _VITE % "path.resolve(__dirname, 'src')"},
            {"packages/web/src/a.js", "packages/api/main.js"},
        )
        assert (
            resolver.resolve("@/a", str(tmp_path / "packages/web/src/b.js"))
            == "packages/web/src/a.js"
        )
        assert resolver.resolve("@/a", str(tmp_path / "packages/api/main.js")) is None

    def test_first_matching_entry_decides(self, tmp_path: Path) -> None:
        config = """export default { resolve: { alias: [
          { find: '@', replacement: path.resolve(__dirname, 'src') },
          { find: '@/foo', replacement: path.resolve(__dirname, 'other') },
          { find: '~', replacement: getLib() },
          { find: '~', replacement: '/lib' },
        ] } }
        """
        resolver = _resolver(tmp_path, {"vite.config.ts": config}, {"other/x.ts", "lib/y.ts"})
        importer = str(tmp_path / "main.ts")
        # Vite hands ``@/foo/x`` to ``@`` (src/foo/x is missing), never to ``@/foo``.
        assert resolver.resolve("@/foo/x", importer) is None
        # An entry whose replacement is not read still claims its ``find``.
        assert resolver.resolve("~/y", importer) is None

    def test_dynamic_alias_adds_nothing(self, tmp_path: Path) -> None:
        resolver = _resolver(
            tmp_path,
            {"vite.config.ts": _VITE % "path.resolve(root, 'src')"},
            {"src/a.ts"},
        )
        assert resolver.resolve("@/a", str(tmp_path / "src/main.ts")) is None


def _parsed(tmp_path: Path, rel: str, imports: list[str]) -> ParsedFile:
    return ParsedFile(
        file_info=FileInfo(
            path=rel,
            abs_path=str(tmp_path / rel),
            language="javascript",
            size_bytes=10,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=rel == "src/main.jsx",
        ),
        symbols=[],
        imports=[
            Import(
                raw_statement=f"import x from '{spec}'",
                module_path=spec,
                imported_names=["x"],
                is_relative=False,
                resolved_file=None,
            )
            for spec in imports
        ],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


def test_graph_edge_through_a_vite_alias(tmp_path: Path) -> None:
    """A Vite-only ``@`` alias gives the imported file its inbound edge."""
    (tmp_path / "vite.config.js").write_text(
        _VITE % 'fileURLToPath(new URL("./src", import.meta.url))', encoding="utf-8"
    )
    builder = GraphBuilder(repo_path=tmp_path)
    builder.add_file(_parsed(tmp_path, "src/main.jsx", ["@/pages/Home"]))
    builder.add_file(_parsed(tmp_path, "src/pages/Home.jsx", []))
    wire_tsconfig_resolver(builder, tmp_path)
    builder.build()
    assert builder.graph().has_edge("src/main.jsx", "src/pages/Home.jsx")
