"""Unit tests for the lightweight (regex-tier) import extractors + resolvers.

Covers Elixir, Dart, Clojure, Haskell, Erlang, and F#: each import form the
extractor claims, the declared-module index (declaration + path-convention
inverse), relative/package forms, and false-positive guards.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import networkx as nx

from repowise.core.ingestion.lightweight_imports import (
    LIGHTWEIGHT_IMPORT_LANGUAGES,
    extract_lightweight_imports,
)
from repowise.core.ingestion.lightweight_imports.clojure import extract_clojure_imports
from repowise.core.ingestion.lightweight_imports.dart import extract_dart_imports
from repowise.core.ingestion.lightweight_imports.elixir import extract_elixir_imports
from repowise.core.ingestion.lightweight_imports.erlang import extract_erlang_imports
from repowise.core.ingestion.lightweight_imports.haskell import extract_haskell_imports
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.resolvers import resolve_import
from repowise.core.ingestion.resolvers.clojure import resolve_clojure_import
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.ingestion.resolvers.dart import resolve_dart_import
from repowise.core.ingestion.resolvers.elixir import resolve_elixir_import
from repowise.core.ingestion.resolvers.erlang import resolve_erlang_import
from repowise.core.ingestion.resolvers.haskell import resolve_haskell_import


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


def _modules(imports) -> list[str]:
    return [imp.module_path for imp in imports]


def _file_info(rel: str, lang: str) -> FileInfo:
    return FileInfo(
        path=rel,
        abs_path=f"/tmp/{rel}",
        language=lang,  # type: ignore[arg-type]
        size_bytes=0,
        git_hash="",
        last_modified=datetime.now(),
        is_test=False,
        is_config=False,
        is_api_contract=False,
        is_entry_point=False,
    )


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    def test_registered_languages(self) -> None:
        assert {
            "elixir",
            "dart",
            "clojure",
            "haskell",
            "erlang",
            "fsharp",
        } == LIGHTWEIGHT_IMPORT_LANGUAGES

    def test_other_language_returns_empty(self) -> None:
        info = _file_info("x.zig", "zig")
        assert extract_lightweight_imports(info, b'const std = @import("std");') == []

    def test_resolver_dispatch_reaches_lightweight_resolver(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"lib/jason.ex": "defmodule Jason do\nend\n", "lib/jason/encoder.ex": ""},
        )
        assert resolve_import("Jason", "lib/jason/encoder.ex", "elixir", ctx) == "lib/jason.ex"


# ---------------------------------------------------------------------------
# Elixir
# ---------------------------------------------------------------------------


class TestElixirExtraction:
    def test_alias_import_use_require(self) -> None:
        src = (
            "defmodule Foo do\n"
            "  alias Foo.Bar\n"
            "  import Foo.Baz\n"
            "  require Logger\n"
            "  use GenServer\n"
            "end\n"
        )
        assert _modules(extract_elixir_imports(src)) == [
            "Foo.Bar",
            "Foo.Baz",
            "Logger",
            "GenServer",
        ]

    def test_brace_expansion_and_multiline(self) -> None:
        src = "alias Foo.{Bar, Baz.Qux,\n  Deep}\n"
        assert _modules(extract_elixir_imports(src)) == [
            "Foo.Bar",
            "Foo.Baz.Qux",
            "Foo.Deep",
        ]

    def test_as_option_keeps_module(self) -> None:
        src = "alias Foo.Bar, as: B\n"
        assert _modules(extract_elixir_imports(src)) == ["Foo.Bar"]

    def test_skips_erlang_atoms_and_module_macro(self) -> None:
        src = "import :math\nalias __MODULE__.Inner\n"
        assert extract_elixir_imports(src) == []

    def test_import_marks_wildcard_names(self) -> None:
        imports = extract_elixir_imports("import Foo.Bar\nalias Foo.Baz\n")
        assert imports[0].imported_names == ["*"]
        assert imports[1].imported_names == []

    def test_dedup(self) -> None:
        src = "alias Foo.Bar\nrequire Foo.Bar\n"
        assert _modules(extract_elixir_imports(src)) == ["Foo.Bar"]


class TestElixirResolution:
    def test_declared_module(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "lib/jason.ex": "defmodule Jason do\nend\n",
                "lib/jason/decoder.ex": "defmodule Jason.Decoder do\nend\n",
            },
        )
        assert resolve_elixir_import("Jason.Decoder", "lib/jason.ex", ctx) == (
            "lib/jason/decoder.ex"
        )

    def test_trailing_strip_hits_nested_module(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "lib/foo/bar.ex": "defmodule Foo.Bar do\n  defmodule Baz do\n  end\nend\n",
                "lib/consumer.ex": "",
            },
        )
        assert resolve_elixir_import("Foo.Bar.Baz", "lib/consumer.ex", ctx) == "lib/foo/bar.ex"

    def test_path_convention_inverse_for_undeclared_file(self, tmp_path: Path) -> None:
        # File without a defmodule head still resolves via lib/foo/bar.ex → Foo.Bar
        ctx = _ctx(
            tmp_path,
            {"lib/my_app/web_router.ex": "# generated\n", "lib/other.ex": ""},
        )
        assert resolve_elixir_import("MyApp.WebRouter", "lib/other.ex", ctx) == (
            "lib/my_app/web_router.ex"
        )

    def test_umbrella_inverse(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"apps/core/lib/core/worker.ex": "# no decl\n", "apps/web/lib/web.ex": ""},
        )
        assert resolve_elixir_import("Core.Worker", "apps/web/lib/web.ex", ctx) == (
            "apps/core/lib/core/worker.ex"
        )

    def test_stdlib_dropped_after_local_miss(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/foo.ex": "defmodule Foo do\nend\n"})
        assert resolve_elixir_import("GenServer", "lib/foo.ex", ctx) is None
        assert resolve_elixir_import("Mix.Project", "lib/foo.ex", ctx) is None

    def test_local_shadows_stdlib_name(self, tmp_path: Path) -> None:
        # A repo that IS a stdlib-named library resolves locally first.
        ctx = _ctx(
            tmp_path,
            {"lib/logger.ex": "defmodule Logger do\nend\n", "lib/use.ex": ""},
        )
        assert resolve_elixir_import("Logger", "lib/use.ex", ctx) == "lib/logger.ex"

    def test_unknown_module_is_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/foo.ex": "defmodule Foo do\nend\n"})
        assert resolve_elixir_import("Phoenix.Controller", "lib/foo.ex", ctx) == (
            "external:Phoenix.Controller"
        )

    def test_self_reference_returns_none(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/foo.ex": "defmodule Foo do\nend\n"})
        assert resolve_elixir_import("Foo", "lib/foo.ex", ctx) is None


# ---------------------------------------------------------------------------
# Dart
# ---------------------------------------------------------------------------


class TestDartExtraction:
    def test_import_export_part(self) -> None:
        src = (
            "import 'package:args/src/parser.dart';\n"
            "import 'dart:async';\n"
            "import 'utils.dart' as utils;\n"
            "export 'src/api.dart';\n"
            "part 'impl.dart';\n"
        )
        imports = extract_dart_imports(src)
        assert _modules(imports) == [
            "package:args/src/parser.dart",
            "dart:async",
            "utils.dart",
            "src/api.dart",
            "impl.dart",
        ]
        by_module = {imp.module_path: imp for imp in imports}
        assert by_module["src/api.dart"].is_reexport
        assert not by_module["impl.dart"].is_reexport
        assert by_module["utils.dart"].is_relative
        assert not by_module["package:args/src/parser.dart"].is_relative

    def test_part_of_uri_and_name_forms(self) -> None:
        assert _modules(extract_dart_imports("part of 'lib.dart';\n")) == ["lib.dart"]
        assert _modules(extract_dart_imports("part of my.library;\n")) == [
            "library:my.library"
        ]


class TestDartResolution:
    def test_sdk_uri_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/main.dart": ""})
        assert resolve_dart_import("dart:async", "lib/main.dart", ctx) is None

    def test_package_self_import(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "pubspec.yaml": "name: args\nversion: 2.0.0\n",
                "lib/src/parser.dart": "",
                "lib/args.dart": "",
            },
        )
        assert resolve_dart_import(
            "package:args/src/parser.dart", "lib/args.dart", ctx
        ) == "lib/src/parser.dart"

    def test_monorepo_package_import(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "packages/core/pubspec.yaml": "name: core\n",
                "packages/core/lib/core.dart": "",
                "packages/app/pubspec.yaml": "name: app\n",
                "packages/app/lib/main.dart": "",
            },
        )
        assert resolve_dart_import(
            "package:core/core.dart", "packages/app/lib/main.dart", ctx
        ) == "packages/core/lib/core.dart"

    def test_foreign_package_is_labelled_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"pubspec.yaml": "name: app\n", "lib/main.dart": ""})
        assert resolve_dart_import("package:http/http.dart", "lib/main.dart", ctx) == (
            "external:pub:http"
        )

    def test_relative_with_parent_traversal(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/src/a.dart": "", "lib/b.dart": ""})
        assert resolve_dart_import("../b.dart", "lib/src/a.dart", ctx) == "lib/b.dart"

    def test_part_of_library_name(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "lib/args.dart": "library args;\npart 'src/impl.dart';\n",
                "lib/src/impl.dart": "part of args;\n",
            },
        )
        assert resolve_dart_import("library:args", "lib/src/impl.dart", ctx) == "lib/args.dart"

    def test_unresolved_library_name_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"lib/a.dart": ""})
        assert resolve_dart_import("library:nope", "lib/a.dart", ctx) is None


# ---------------------------------------------------------------------------
# Clojure
# ---------------------------------------------------------------------------


class TestClojureExtraction:
    def test_ns_require_vectors(self) -> None:
        src = (
            "(ns foo.bar\n"
            "  (:require [baz.qux :as q]\n"
            "            [other.ns :refer [x y]]\n"
            "            plain.ns))\n"
        )
        assert _modules(extract_clojure_imports(src)) == [
            "baz.qux",
            "other.ns",
            "plain.ns",
        ]

    def test_refer_vector_members_not_captured(self) -> None:
        src = "(ns a (:require [b.c :refer [important-fn other-fn]]))"
        assert _modules(extract_clojure_imports(src)) == ["b.c"]

    def test_use_and_standalone_require(self) -> None:
        src = "(ns a (:use [old.style]))\n(require '[dyn.ns :as d])\n"
        assert _modules(extract_clojure_imports(src)) == ["old.style", "dyn.ns"]

    def test_import_block_not_captured(self) -> None:
        src = "(ns a (:import (java.util Date) [java.io File]))"
        assert extract_clojure_imports(src) == []

    def test_strings_and_comments_skipped(self) -> None:
        src = '(ns a (:require [b.c]))\n; (:require [commented.out])\n(def s "(:require [fake.ns])")\n'
        assert _modules(extract_clojure_imports(src)) == ["b.c"]


class TestClojureResolution:
    def test_declared_ns(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "src/foo/bar.clj": "(ns foo.bar)\n",
                "src/foo/core.clj": "(ns foo.core)\n",
            },
        )
        assert resolve_clojure_import("foo.bar", "src/foo/core.clj", ctx) == "src/foo/bar.clj"

    def test_dash_underscore_inverse(self, tmp_path: Path) -> None:
        # File with no ns decl: src/my_lib/web_utils.clj → my-lib.web-utils
        ctx = _ctx(
            tmp_path,
            {"src/my_lib/web_utils.clj": "; no ns\n", "src/core.clj": "(ns core)\n"},
        )
        assert resolve_clojure_import("my-lib.web-utils", "src/core.clj", ctx) == (
            "src/my_lib/web_utils.clj"
        )

    def test_cljc_and_cljs_share_mapping(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"src/shared/util.cljc": "(ns shared.util)\n", "src/app.cljs": "(ns app)\n"},
        )
        assert resolve_clojure_import("shared.util", "src/app.cljs", ctx) == (
            "src/shared/util.cljc"
        )

    def test_core_namespaces_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"src/a.clj": "(ns a)\n"})
        assert resolve_clojure_import("clojure.string", "src/a.clj", ctx) is None
        assert resolve_clojure_import("cljs.reader", "src/a.clj", ctx) is None

    def test_unknown_ns_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"src/a.clj": "(ns a)\n"})
        assert resolve_clojure_import("ring.core", "src/a.clj", ctx) == "external:ring.core"


# ---------------------------------------------------------------------------
# Haskell
# ---------------------------------------------------------------------------


class TestHaskellExtraction:
    def test_import_forms(self) -> None:
        src = (
            "module Main where\n"
            "import Data.Aeson\n"
            "import qualified Data.Map as M\n"
            "import Data.Text (Text, pack)\n"
            'import "lens" Control.Lens\n'
            "import safe qualified Trusted.Mod\n"
        )
        assert _modules(extract_haskell_imports(src)) == [
            "Data.Aeson",
            "Data.Map",
            "Data.Text",
            "Control.Lens",
            "Trusted.Mod",
        ]

    def test_indented_or_commented_import_not_captured(self) -> None:
        src = "-- import Fake.Mod\nlet x = 1\n  import Nested.NotReal\n"
        assert extract_haskell_imports(src) == []


class TestHaskellResolution:
    def test_declared_module(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "src/Data/Aeson/Types.hs": "module Data.Aeson.Types where\n",
                "src/Data/Aeson.hs": "module Data.Aeson where\n",
            },
        )
        assert resolve_haskell_import("Data.Aeson.Types", "src/Data/Aeson.hs", ctx) == (
            "src/Data/Aeson/Types.hs"
        )

    def test_local_shadows_base_prefix(self, tmp_path: Path) -> None:
        # The aeson repo's own Data.Aeson resolves locally despite the Data. prefix.
        ctx = _ctx(
            tmp_path,
            {"src/Data/Aeson.hs": "module Data.Aeson where\n", "app/Main.hs": ""},
        )
        assert resolve_haskell_import("Data.Aeson", "app/Main.hs", ctx) == "src/Data/Aeson.hs"

    def test_capitalized_path_inverse(self, tmp_path: Path) -> None:
        # No module decl: trailing capitalized segments derive the name.
        ctx = _ctx(
            tmp_path,
            {"lib/Foo/Bar.hs": "-- no decl\n", "app/Main.hs": "module Main where\n"},
        )
        assert resolve_haskell_import("Foo.Bar", "app/Main.hs", ctx) == "lib/Foo/Bar.hs"

    def test_base_modules_dropped(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"app/Main.hs": "module Main where\n"})
        assert resolve_haskell_import("Control.Monad", "app/Main.hs", ctx) is None
        assert resolve_haskell_import("Prelude", "app/Main.hs", ctx) is None

    def test_unknown_module_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"app/Main.hs": "module Main where\n"})
        assert resolve_haskell_import("Network.Wai", "app/Main.hs", ctx) == (
            "external:Network.Wai"
        )


# ---------------------------------------------------------------------------
# Erlang
# ---------------------------------------------------------------------------


class TestErlangExtraction:
    def test_includes_and_behaviour(self) -> None:
        src = (
            '-module(my_worker).\n'
            '-behaviour(gen_server).\n'
            '-include("records.hrl").\n'
            '-include_lib("kernel/include/logger.hrl").\n'
        )
        assert _modules(extract_erlang_imports(src)) == [
            "records.hrl",
            "lib:kernel/include/logger.hrl",
            "gen_server",
        ]

    def test_qualified_calls_marked(self) -> None:
        src = "-module(a).\nrun() -> my_util:do(1), lists:map(F, L).\n"
        modules = _modules(extract_erlang_imports(src))
        assert "call:my_util" in modules
        assert "call:lists" in modules

    def test_calls_in_comments_and_strings_skipped(self) -> None:
        src = '-module(a).\n%% my_util:do()\nf() -> io_lib:format("calls x:y(", []).\n'
        modules = _modules(extract_erlang_imports(src))
        assert "call:my_util" not in modules
        assert "call:x" not in modules
        assert "call:io_lib" in modules


class TestErlangResolution:
    def test_include_importer_relative_and_app_include(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "src/my_worker.erl": "-module(my_worker).\n",
                "include/records.hrl": "",
            },
        )
        assert resolve_erlang_import("records.hrl", "src/my_worker.erl", ctx) == (
            "include/records.hrl"
        )

    def test_include_lib_local_umbrella_app(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {
                "apps/core/include/core.hrl": "",
                "apps/web/src/web.erl": "-module(web).\n",
            },
        )
        assert resolve_erlang_import(
            "lib:core/include/core.hrl", "apps/web/src/web.erl", ctx
        ) == "apps/core/include/core.hrl"

    def test_include_lib_foreign_app_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"src/a.erl": "-module(a).\n"})
        assert resolve_erlang_import("lib:kernel/include/logger.hrl", "src/a.erl", ctx) == (
            "external:kernel"
        )

    def test_qualified_call_local_hit(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"src/my_util.erl": "-module(my_util).\n", "src/a.erl": "-module(a).\n"},
        )
        assert resolve_erlang_import("call:my_util", "src/a.erl", ctx) == "src/my_util.erl"

    def test_qualified_call_miss_is_dropped_not_external(self, tmp_path: Path) -> None:
        ctx = _ctx(tmp_path, {"src/a.erl": "-module(a).\n"})
        assert resolve_erlang_import("call:lists", "src/a.erl", ctx) is None
        assert resolve_erlang_import("call:some_dep", "src/a.erl", ctx) is None

    def test_behaviour_local_then_otp_drop(self, tmp_path: Path) -> None:
        ctx = _ctx(
            tmp_path,
            {"src/my_behaviour.erl": "-module(my_behaviour).\n", "src/a.erl": "-module(a).\n"},
        )
        assert resolve_erlang_import("my_behaviour", "src/a.erl", ctx) == (
            "src/my_behaviour.erl"
        )
        assert resolve_erlang_import("gen_server", "src/a.erl", ctx) is None


# ---------------------------------------------------------------------------
# F#
# ---------------------------------------------------------------------------


class TestFSharpExtraction:
    def test_open_forms(self) -> None:
        from repowise.core.ingestion.lightweight_imports.fsharp import (
            extract_fsharp_imports,
        )

        src = (
            "namespace MyApp.Core\n"
            "open System\n"
            "open MyApp.Domain\n"
            "open type System.Math\n"
            "let x = 1\n"
        )
        assert _modules(extract_fsharp_imports(src)) == [
            "System",
            "MyApp.Domain",
            "System.Math",
        ]


class TestFSharpResolution:
    def test_unambiguous_module_resolves(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.fsharp import resolve_fsharp_import

        ctx = _ctx(
            tmp_path,
            {
                "src/Domain.fs": "module MyApp.Domain\n",
                "src/App.fs": "module MyApp.App\nopen MyApp.Domain\n",
            },
        )
        assert resolve_fsharp_import("MyApp.Domain", "src/App.fs", ctx) == "src/Domain.fs"

    def test_ambiguous_namespace_yields_no_edge(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.fsharp import resolve_fsharp_import

        ctx = _ctx(
            tmp_path,
            {
                "src/A.fs": "namespace MyApp.Core\n",
                "src/B.fs": "namespace MyApp.Core\n",
                "src/C.fs": "module MyApp.App\n",
            },
        )
        assert resolve_fsharp_import("MyApp.Core", "src/C.fs", ctx) is None

    def test_nested_module_binding_not_indexed(self, tmp_path: Path) -> None:
        # `module Helpers =` is a nested binding, not a file-level decl.
        from repowise.core.ingestion.resolvers.fsharp import resolve_fsharp_import

        ctx = _ctx(
            tmp_path,
            {
                "src/Lib.fs": "module MyApp.Lib\nmodule Helpers =\n    let x = 1\n",
                "src/App.fs": "module MyApp.App\n",
            },
        )
        # trailing strip: open MyApp.Lib.Helpers hits the file-level MyApp.Lib
        assert resolve_fsharp_import("MyApp.Lib.Helpers", "src/App.fs", ctx) == "src/Lib.fs"

    def test_dotnet_namespaces_dropped(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.fsharp import resolve_fsharp_import

        ctx = _ctx(tmp_path, {"src/App.fs": "module MyApp.App\n"})
        assert resolve_fsharp_import("System.IO", "src/App.fs", ctx) is None
        assert resolve_fsharp_import("FSharp.Control", "src/App.fs", ctx) is None

    def test_unknown_namespace_external(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.resolvers.fsharp import resolve_fsharp_import

        ctx = _ctx(tmp_path, {"src/App.fs": "module MyApp.App\n"})
        assert resolve_fsharp_import("Newtonsoft.Json", "src/App.fs", ctx) == (
            "external:Newtonsoft.Json"
        )


class TestFSharpCompileOrder:
    def test_adjacent_spine_edges(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.languages.fsharp_compile_order import (
            add_fsharp_compile_order_edges,
        )

        (tmp_path / "src").mkdir(parents=True)
        (tmp_path / "src/App.fsproj").write_text(
            "<Project>\n  <ItemGroup>\n"
            '    <Compile Include="Domain.fs" />\n'
            '    <Compile Include="Logic.fs" />\n'
            '    <Compile Include="Program.fs" />\n'
            '    <Compile Include="$(Generated)/Gen.fs" />\n'
            "  </ItemGroup>\n</Project>\n"
        )
        graph = nx.DiGraph()
        for p in ("src/Domain.fs", "src/Logic.fs", "src/Program.fs"):
            graph.add_node(p)
        added = add_fsharp_compile_order_edges(graph, tmp_path)
        assert added == 2
        assert graph.has_edge("src/Logic.fs", "src/Domain.fs")
        assert graph.has_edge("src/Program.fs", "src/Logic.fs")
        edge = graph["src/Logic.fs"]["src/Domain.fs"]
        assert edge["edge_type"] == "imports"
        assert edge["hint_source"] == "compile_order"

    def test_existing_edge_wins(self, tmp_path: Path) -> None:
        from repowise.core.ingestion.languages.fsharp_compile_order import (
            add_fsharp_compile_order_edges,
        )

        (tmp_path / "App.fsproj").write_text(
            '<Project><ItemGroup><Compile Include="A.fs" /><Compile Include="B.fs" />'
            "</ItemGroup></Project>"
        )
        graph = nx.DiGraph()
        graph.add_node("A.fs")
        graph.add_node("B.fs")
        graph.add_edge("B.fs", "A.fs", edge_type="imports", imported_names=["X"])
        added = add_fsharp_compile_order_edges(graph, tmp_path)
        assert added == 0
        assert graph["B.fs"]["A.fs"]["imported_names"] == ["X"]
