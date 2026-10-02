"""One origin per file: production, test, vendored, docs_example, generated, tooling, build."""

from __future__ import annotations

import pytest

from repowise.core.code_origin import (
    CODE_ORIGINS,
    code_origin,
    is_build_file,
    is_generated_header,
    is_migration_path,
    is_vendored_or_generated_path,
    ship_rank,
)

_JQUERY = (
    "/*!\n"
    " * jQuery JavaScript Library v3.6.0\n"
    " * https://jquery.com/\n"
    " * Copyright OpenJS Foundation and other contributors\n"
    " * Released under the MIT license\n"
    " */\n"
    "(function (global, factory) {\n"
)
_TYPEAHEAD = (
    "/*!\n"
    " * typeahead.js 0.10.5\n"
    " * https://github.com/twitter/typeahead.js\n"
    " * Copyright 2013-2014 Twitter, Inc. and other contributors; Licensed MIT\n"
    " */\n"
)
_OWN_HEADER = (
    "// Copyright (c) 2024 Acme Corp. All rights reserved.\n"
    "// Licensed under the MIT License.\n"
    "export function boot() {}\n"
)


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("src/flask/app.py", "production"),
        ("app.py", "production"),
        ("src/examples.py", "production"),  # a file named for examples is code
        ("pkg/external_api/client.go", "production"),
        ("pkg/external/client.go", "production"),  # external/ only at the root
        ("tests/test_app.py", "test"),
        ("src/app.test.ts", "test"),
        ("vendor/github.com/x/y.go", "vendored"),
        ("web/node_modules/lodash/index.js", "vendored"),
        ("static/bower_components/jquery/jquery.js", "vendored"),
        ("third_party/zlib/inflate.c", "vendored"),
        ("src/thirdparty/docopt.py", "vendored"),
        ("external/abseil/str.cc", "vendored"),
        ("static/js/app.min.js", "vendored"),
        ("assets/site.min.css", "vendored"),
        ("docs_src/tutorial/first_steps.py", "docs_example"),
        ("docs/conf.py", "docs_example"),
        ("doc/man_docs.go", "production"),  # cobra's doc/ is a shipped package
        ("src/x/examples/a.py", "production"),  # a nested examples/ can ship
        ("pkg/samples/registry.go", "production"),
        ("src/tutorials/steps.py", "production"),
        ("examples/a.py", "docs_example"),
        ("docs/examples/a.py", "docs_example"),
        ("examples/tutorial/app.py", "docs_example"),
        ("example/main.go", "docs_example"),
        ("samples/hello/Program.cs", "docs_example"),
        ("tutorials/part1/app.js", "docs_example"),
        ("tutorial_01/app.py", "docs_example"),
        ("scripts/release.py", "tooling"),
        (".github/scripts/label.js", "tooling"),
        ("tools/gen.go", "tooling"),
        ("benchmarks/bench_app.py", "tooling"),
        ("app/migrations/0001_initial.py", "tooling"),
        ("db/alembic/versions/abc_add.py", "tooling"),
        ("setup.py", "build"),
        ("vite.config.ts", "build"),
        ("fabfile.py", "tooling"),
        ("eslint.config.mjs", "tooling"),
        ("db/migrate/20240101_add_users.rb", "tooling"),
        ("src/__generated__/schema.ts", "generated"),
        ("api/user_pb2.py", "generated"),
        ("api/user_grpc.pb.go", "generated"),
        ("Forms/Main.Designer.cs", "generated"),
        ("src/client.generated.ts", "generated"),
        ("esql/src/main/generated/org/x/FooEvaluator.java", "generated"),
        ("compute/src/main/generated-src/org/x/IntBlock.java", "generated"),
        ("target/generated-sources/annotations/X.java", "generated"),
        ("src/generator/emit.py", "production"),  # the generator itself ships
        ("src/native/external/zlib/inflate.c", "vendored"),
        ("src/native/external/libunwind/include/libunwind.h", "vendored"),
        ("src/native/external/rapidjson/prettywriter.hpp", "vendored"),
        # external/ in a non-native tree is a package about external services.
        ("x-pack/inference/src/main/java/org/x/external/http/Sender.java", "production"),
        ("src/pkg/external/client.py", "production"),
        ("sdk/llms/src/providers/vendors/openai.ts", "production"),
        # min/ needs a third-party banner (test below); the path alone is not enough
        ("src/min/app.js", "production"),
        ("lib/min/util.js", "production"),
        ("min/site.css", "production"),
        ("src/min/compute.py", "production"),
        # external/ is vendored at the root or as a native tree's library shelf
        ("src/external/foo.cpp", "production"),
        ("libs/external/x/a.h", "production"),
        ("a/b/External/Foo.cpp", "production"),
        ("src/native/external/zlib.cmake", "build"),  # no library directory: a CMake module
        # a bare generated/ only as a build output
        ("app/generated/handlers.py", "production"),
        ("tests/generated/test_x.py", "test"),
        ("src/generated/schema.ts", "production"),
        ("build/generated/source/X.java", "generated"),
        ("build-tools-internal/src/main/java/org/x/Plugin.java", "tooling"),
        ("build-tools/src/main/java/org/x/Task.java", "tooling"),
        ("buildSrc/src/main/kotlin/conventions.gradle.kts", "build"),
        ("buildSrc/src/main/kotlin/Versions.kt", "tooling"),
        ("build-logic/src/main/kotlin/Plugin.kt", "tooling"),
        ("packages/build-tools/src/index.ts", "production"),  # a shipped package
    ],
)
def test_path_rules(path: str, expected: str) -> None:
    assert code_origin(path) == expected


def test_every_answer_is_one_of_the_seven() -> None:
    assert set(CODE_ORIGINS) == {
        "production",
        "test",
        "vendored",
        "docs_example",
        "generated",
        "tooling",
        "build",
    }


def test_precedence_is_generated_vendored_build_test_docs_tooling() -> None:
    assert code_origin("vendor/foo_test.go") == "vendored"
    assert code_origin("third_party/zlib/CMakeLists.txt") == "vendored"
    assert code_origin("tests/fixtures/app/build.gradle") == "build"
    assert code_origin("examples/hello/CMakeLists.txt", is_test=True) == "build"
    assert code_origin("scripts/build.gradle.kts") == "build"
    assert code_origin("docs/tests/test_x.py") == "test"
    assert code_origin("examples/scripts/run.py") == "docs_example"
    assert code_origin("vendor/x.go", "// Code generated by protoc. DO NOT EDIT.\n") == "generated"


def test_stored_test_flag_wins_over_the_path() -> None:
    assert code_origin("src/checks.py", is_test=True) == "test"
    assert code_origin("tests/test_app.py", is_test=False) == "production"


def test_generated_banner_in_the_header() -> None:
    assert code_origin("pkg/x.go", b"// Code generated by stringer. DO NOT EDIT.\n") == "generated"
    assert code_origin("pkg/x.py", '"""Auto-generated by the build."""\n') == "generated"


def test_generated_banner_negatives() -> None:
    # A string literal mentioning codegen is not a banner.
    assert code_origin("pkg/x.py", 'MSG = "auto-generated client"\n') == "production"
    # A banner below the second line is not a header.
    assert not is_generated_header("a = 1\nb = 2\n# DO NOT EDIT\n")


_ES_EVALUATOR = (
    "// Copyright Elasticsearch B.V. Licensed under the Elastic License 2.0.\n"
    "package org.elasticsearch.xpack.esql.expression;\n"
    "\n"
    "import java.lang.Override;\n"
    "import org.elasticsearch.compute.data.Block;\n"
    "\n"
    "/**\n"
    " * {@link ExpressionEvaluator} implementation for {@link Greatest}.\n"
    " * This class is generated. Edit {@code EvaluatorImplementer} instead.\n"
    " */\n"
    "public final class GreatestBooleanEvaluator {\n"
)


def test_generated_sentence_in_the_class_doc_comment() -> None:
    path = "server/src/main/java/org/x/GreatestBooleanEvaluator.java"
    assert code_origin(path, _ES_EVALUATOR) == "generated"
    # Ingestion's skip rule still reads the first two lines only.
    assert not is_generated_header(_ES_EVALUATOR)


def test_generated_sentence_must_sit_in_a_comment() -> None:
    text = 'package x;\n\nString msg = "This file is generated by the build";\n'
    assert code_origin("src/main/java/x/Msg.java", text) == "production"


def test_do_not_edit_below_the_header_is_not_a_banner() -> None:
    text = "package x;\n\nclass T {\n  // DO NOT EDIT this table without the schema\n}\n"
    assert code_origin("src/main/java/x/T.java", text) == "production"


_REF_STUB = (
    "// Licensed to the .NET Foundation under one or more agreements.\n"
    "// ----------------------------------------------------------------\n"
    "// Changes to this file must follow the https://aka.ms/api-review process.\n"
    "// ----------------------------------------------------------------\n"
    "namespace System\n{\n"
    "    public partial struct DateTime\n    {\n"
    "        public DateTime(int year, int month, int day) { throw null; }\n"
)


def test_reference_assembly_stub_is_generated() -> None:
    path = "src/libraries/System.Runtime/ref/System.Runtime.cs"
    assert code_origin(path, _REF_STUB) == "generated"
    body = "namespace L { public class C { public int M() { throw null; } } }\n"
    assert code_origin("src/tools/illink/src/linker/ref/Linker/C.cs", body) == "generated"


def test_reference_stub_needs_the_stub_body_and_the_ref_dir() -> None:
    real = "namespace App { public class Ref { public int M() => 1; } }\n"
    assert code_origin("src/App/ref/Ref.cs", real) == "production"
    assert code_origin("src/libraries/System.Runtime/src/DateTime.cs", _REF_STUB) == "production"
    assert code_origin("src/libraries/System.Runtime/ref/System.Runtime.cs") == "production"


def test_path_only_vendored_or_generated() -> None:
    assert is_vendored_or_generated_path("src/native/external/zlib/trees.c")
    assert is_vendored_or_generated_path("esql/src/main/generated/X.java")
    assert not is_vendored_or_generated_path("src/Monaco/monacoSRC/min/vs/loader.js")
    assert not is_vendored_or_generated_path("src/pkg/external/client.py")
    assert not is_vendored_or_generated_path("tests/test_app.py")


@pytest.mark.parametrize("header", [_JQUERY, _TYPEAHEAD])
@pytest.mark.parametrize("folder", ["docs/_static", "static/js", "assets", "public/lib"])
def test_third_party_header_under_asset_dirs_is_vendored(header: str, folder: str) -> None:
    assert code_origin(f"{folder}/lib.js", header) == "vendored"


def test_third_party_header_outside_asset_dirs_stays() -> None:
    assert code_origin("src/lib.js", _JQUERY) == "production"


def test_own_license_header_is_not_vendored() -> None:
    assert code_origin("static/js/boot.js", _OWN_HEADER) == "production"
    assert code_origin("docs/_static/boot.js", _OWN_HEADER) == "docs_example"


def test_own_release_banner_is_not_vendored() -> None:
    banner = "/*! acme v1.2.0 | (c) Acme | MIT License */\n"
    assert code_origin("static/acme.js", banner, project="acme") == "production"
    assert code_origin("static/acme.js", banner, project="other") == "vendored"


def test_own_banner_matches_the_project_name_loosely() -> None:
    banner = "/*! MyLib v2.0.1 | (c) Someone | MIT License */\n"
    assert code_origin("static/mylib.js", banner, project="my-lib") == "production"
    assert code_origin("static/mylib.js", banner, project="my_lib") == "production"


def test_header_banner_must_sit_in_a_comment() -> None:
    # A version string in code with a copyright assignment is not a banner.
    text = 'copyright = "2010 Pallets"\nrelease = "Flask 3.0.1"\n'
    assert code_origin("docs/conf.py", text) == "docs_example"
    assert code_origin("static/conf.py", text) == "production"


def test_header_is_read_from_the_first_twenty_lines_only() -> None:
    text = "\n" * 25 + _JQUERY
    assert code_origin("static/js/lib.js", text) == "production"


def test_windows_separators() -> None:
    assert code_origin("docs_src\\tutorial\\app.py") == "docs_example"


def test_generator_inputs_are_not_their_output() -> None:
    # A template carries the banner it stamps on its output.
    assert code_origin("compute/src/main/java/org/x/X-Block.java.st", _ES_EVALUATOR) == (
        "production"
    )
    # A generator script holds the banner in a string, after its own code.
    script = (
        "import os\n"
        "from util import write\n"
        "\n"
        'PROLOG = """// Licensed under MIT.\n'
        "/*\n"
        "This file is generated using the logic from genEvents.py\n"
        '*/"""\n'
    )
    assert code_origin("src/codegen/genEvents.py", script) == "production"


def test_generated_sentence_about_an_import_is_not_a_banner() -> None:
    text = (
        "/**\n * @license\n * Copyright Google LLC\n */\n\n"
        "import {signal} from '@angular/core';\n"
        "// This file is generated at build-time, error is expected here.\n"
        "import MANIFEST from '../assets/api/manifest.json';\n"
        "\n"
        "export class ApiManager {}\n"
    )
    assert code_origin("adev/src/app/api-manager.service.ts", text) == "production"


_MONACO = (
    "/*!-----------------------------------------------------------\n"
    " * Copyright (c) Microsoft Corporation. All rights reserved.\n"
    " * Version: 0.47.0(69991d66135e4a1fc1cf0b1ac4ad25d429866a0d)\n"
    " * Released under the MIT license\n"
    " *-----------------------------------------------------------*/"
    'define("vs/base/common/worker/simpleWorker.nls.de",{});\n'
)


def test_minified_tree_needs_a_third_party_banner() -> None:
    assert code_origin("src/Monaco/monacoSRC/min/vs/loader.js", _MONACO, project="PowerToys") == (
        "vendored"
    )
    assert code_origin("src/min/app.js", "export function boot() {}\n") == "production"
    assert code_origin("src/min/app.js", _OWN_HEADER, project="acme") == "production"


@pytest.mark.parametrize(
    "line",
    [
        "// This file is generated when you run make docs.",
        "// This file was generated once, then edited by hand.",
        "// This class is generated on demand by the factory below.",
    ],
)
def test_generated_sentence_needs_a_machine_output_completion(line: str) -> None:
    text = "package x;\n\n" + line + "\npublic class A {}\n"
    assert code_origin("src/main/java/x/A.java", text) == "production"


@pytest.mark.parametrize(
    "line",
    [
        "// This file is generated by GenerateTests.csx.",
        "// This file is auto-generated from a template file.",
        "// WARNING: This file is generated and should not be modified directly.",
        "// THIS FILE IS GENERATED. DO NOT HAND EDIT.",
    ],
)
def test_generated_sentence_completions(line: str) -> None:
    text = "using System;\n\n" + line + "\nnamespace A {}\n"
    assert code_origin("src/A.cs", text) == "generated"


def test_reference_stub_markers_are_anchored() -> None:
    mention = (
        "namespace L { // callers may throw null; see docs\n public class C { int M() => 1; } }\n"
    )
    assert code_origin("src/Lib/ref/C.cs", mention) == "production"


@pytest.mark.parametrize(
    "path",
    [
        "build.gradle.kts",
        "ktor-server/ktor-server-core/build.gradle.kts",
        "settings.gradle",
        "gradle/plugins/ktorbuild.kmp.gradle.kts",
        "caffeine/build.gradle",
        "pom.xml",
        "server/pom.xml",
        "CMakeLists.txt",
        "src/lib/CMakeLists.txt",
        "cmake/FindOpenSSL.cmake",
        "Makefile",
        "src/Makefile.am",
        "rules.mk",
        "meson.build",
        "BUILD",
        "pkg/BUILD.bazel",
        "tools/defs.bzl",
        "Directory.Build.props",
        "src/Directory.Build.targets",
        "eng/Versions.props",
        "build.rs",
        "crates/core/build.rs",
        "Cargo.toml",
        "setup.py",
        "noxfile.py",
        "magefile.go",
        "Package.swift",
        "webpack.config.js",
        "packages/app/vite.config.mts",
        "rollup.config.prod.mjs",
        "gulpfile.js",
        "Rakefile",
        "foo.gemspec",
        "build.cake",
        "build/tasks.cake",
    ],
)
def test_build_files_by_type(path: str) -> None:
    assert is_build_file(path)
    assert code_origin(path) == "build"


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # Near misses: code named like a build step, which ships.
        ("src/build.rs", "production"),  # a ``mod build;``, not a Cargo build script
        ("homeassistant/setup.py", "production"),  # a nested setup.py is a module
        ("src/build/compiler.ts", "tooling"),  # the build/ directory rule, not the type
        ("src/builder.py", "production"),
        ("src/gradle/GradlePlugin.java", "production"),
        ("lib/webpack.js", "production"),
        ("src/makefile_parser.py", "production"),
        ("src/cmake.py", "production"),
        ("app/props.ts", "production"),
        ("src/Button.props.ts", "production"),
        ("cake.cs", "production"),  # an app named cake, not a Cake script
        ("src/Cake/CakeHost.cs", "production"),
        ("src/BuildInfo.java", "production"),
        ("src/main/kotlin/Settings.kt", "production"),
        ("lib/rake_task.rb", "production"),
        ("src/vite.ts", "production"),
        ("cmd/build/main.go", "tooling"),
        ("src/BUILD.md", "production"),
    ],
)
def test_build_near_misses_are_not_build_files(path: str, expected: str) -> None:
    assert not is_build_file(path)
    assert code_origin(path) == expected


def test_migration_paths() -> None:
    assert is_migration_path("app/migrations/0001_initial.py")
    assert is_migration_path("db/migrate/20240101_add.rb")
    assert is_migration_path("alembic/versions/abc.py")
    assert not is_migration_path("api/versions/v1.py")
    assert not is_migration_path("src/migrate.py")


def test_ship_rank_puts_product_code_first_and_tests_last() -> None:
    assert ship_rank("src/app.py") == 0
    assert ship_rank("build.gradle.kts") == 1
    assert ship_rank("scripts/release.py") == 1
    assert ship_rank("vendor/x/y.go") == 1
    assert ship_rank("tests/test_app.py") == 2


def test_build_rs_under_src_is_a_dead_code_root_but_keeps_its_origin() -> None:
    # A crate root under src/ (rust-lang/rust) is never flagged as dead...
    assert is_build_file("src/bootstrap/build.rs")
    assert is_build_file("src/tools/x/build.rs")
    # ...but only a build.rs with no src/ above it leaves the fix lists.
    assert code_origin("src/bootstrap/build.rs") == "production"
    assert code_origin("crates/cli/src/commands/build.rs") == "production"
    assert not is_build_file("src/build.rs")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        # A deps/ directory holding a library is a vendored copy.
        ("deps/wslay/lib/wslay_event.c", "vendored"),
        ("internal/warpc/deps/parson/parson.c", "vendored"),
        ("deps/deps.go", "production"),  # hugo's own package named deps
        # C and C++ only: a first-party deps package elsewhere stays production.
        ("internal/deps/resolver/resolve.go", "production"),
        ("src/deps/graph/mod.rs", "production"),
        ("lib/deps/tree/walk.js", "production"),
        ("deps/Makefile.am", "production"),
        ("src/deps.py", "production"),
        # Benchmarks beside the code they measure are tooling.
        ("absl/synchronization/mutex_benchmark.cc", "tooling"),
        ("absl/random/internal/randen_benchmarks.cc", "tooling"),
        ("absl/synchronization/mutex.cc", "production"),
        ("src/benchmark_runner.py", "production"),
        # Cargo builds every crate's examples/ as example targets.
        ("crates/grep/examples/simplegrep.rs", "docs_example"),
        ("apps/examples/desktop-app/src-tauri/src/main.rs", "docs_example"),
        ("crates/core/src/examples/registry.rs", "production"),  # a module of the crate
        ("pkg/x/examples/a.py", "production"),  # other languages: root only
        ("src/main/kotlin/org/example/App.kt", "production"),
    ],
)
def test_deps_benchmarks_and_cargo_examples(path: str, expected: str) -> None:
    assert code_origin(path) == expected
