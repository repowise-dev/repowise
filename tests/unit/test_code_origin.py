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
        # a `tests/` folder inside a JS-family `src` tree is a product feature
        ("packages/web/src/app/repos/[id]/tests/page.tsx", "production"),
        ("packages/web/src/components/tests/tests-view.tsx", "production"),
        ("packages/web/src/components/tests/tests-view.test.tsx", "test"),
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
        # A product's own docs routes ship; only a root docs tree is docs.
        ("app/docs/page.tsx", "production"),
        ("src/pages/docs/[slug].tsx", "production"),
        ("pkg/docs_src/handler.py", "production"),
        ("examples/tutorial/app.py", "docs_example"),
        ("example/main.go", "docs_example"),
        ("samples/hello/Program.cs", "docs_example"),
        ("tutorials/part1/app.js", "docs_example"),
        ("tutorial_01/app.py", "docs_example"),
        ("scripts/release.py", "tooling"),
        (".github/scripts/label.js", "tooling"),
        ("tools/gen.go", "tooling"),
        # Dart's ``tool/`` (flutter samples' CI scripts), but not a shipped one.
        ("tool/ci_script.dart", "tooling"),
        ("desktop_photo_search/material/tool/grind.dart", "tooling"),
        ("lib/src/tool/picker.dart", "production"),
        ("lib/tool/picker.dart", "production"),
        ("bin/tool/run.dart", "production"),
        ("packages/app/lib/tool/picker.dart", "production"),
        ("src/cmd/go/internal/tool/tool.go", "production"),
        ("tool/release.py", "production"),
        ("benchmarks/bench_app.py", "tooling"),
        ("app/migrations/0001_initial.py", "tooling"),
        ("db/alembic/versions/abc_add.py", "tooling"),
        ("setup.py", "build"),
        ("vite.config.ts", "build"),
        ("fabfile.py", "tooling"),
        ("eslint.config.mjs", "tooling"),
        ("db/migrate/20240101_add_users.rb", "tooling"),
        ("db/data_migrations/20240101_backfill.rb", "tooling"),
        ("src/django_test_migrations/logic.py", "production"),
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
        # the Dart SDK headers a Flutter FFI plugin vendors under dart-sdk/
        ("pedometer/src/dart-sdk/include/dart_tools_api.h", "vendored"),
        ("src/dart-sdk/include/internal/dart_api_dl_impl.h", "vendored"),
        ("lib/dart_sdk_utils.dart", "production"),
        ("src/dartsdk/x.c", "production"),
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
        # a bundler's output, by name; a module about bundles is hand-written
        ("apps/kit/Resources/CanvasA2UI/a2ui.bundle.js", "generated"),
        ("dist/main.bundle.mjs", "generated"),
        ("dist/Main.Bundle.JS", "generated"),
        ("src/Forms/Main.designer.cs", "generated"),
        ("src/logging/diagnostic-support-bundle.ts", "production"),
        # a library release unpacked in a Salesforce static resource, or its source mirror
        ("force-app/main/default/staticresources/Libs/arbor-v0.92/src/etc.js", "vendored"),
        ("StaticResourceSources/arbor-v0.92/lib/arbor.js", "vendored"),
        ("src/staticresources/jquery-ui-1.12.1/ui.js", "vendored"),
        ("force-app/main/default/staticresources/npsp-slds/modal.js", "production"),
        ("force-app/main/default/staticresources/lmsvf/templates.js", "production"),
        ("static/arbor-v0.92/src/etc.js", "production"),  # static resources only
        ("src/api/v1.2/handler.js", "production"),
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


@pytest.mark.parametrize("period", [".", ""])
@pytest.mark.parametrize("path", ["src/modules/ZoomIt/ZoomIt/resource.h", "src/app/resource.h"])
def test_visual_cpp_resource_header_is_generated_without_skipping_ingestion(
    path: str, period: str
) -> None:
    text = (
        "//{{NO_DEPENDENCIES}}\n"
        f"// Microsoft Visual C++ generated include file{period}\n"
        "// Used by ZoomIt.rc\n"
        "//\n"
        "//////////////////////////////\n"
        "// Non-localizable\n"
        "\n"
        '#define FILE_DESCRIPTION "Sysinternals Screen Magnifier"\n'
        '#define INTERNAL_NAME "ZoomIt"\n'
        '#define ORIGINAL_FILENAME "PowerToys.ZoomIt.exe"\n'
    )
    assert code_origin(path, text) == "generated"
    # The resource IDs must remain in the graph for their includers.
    assert not is_generated_header(text)


@pytest.mark.parametrize(
    "comment",
    [
        "Workaround for a Microsoft Visual C++ generated warning",
        "Workaround for Microsoft Visual C++ generated include file.",
        "Microsoft Visual C++ generated include file. See the workaround below.",
    ],
)
def test_visual_cpp_banner_like_prose_stays_production(comment: str) -> None:
    text = f"//{{{{NO_DEPENDENCIES}}}}\n// {comment}\n#define RESOURCE_ID 101\n"
    assert code_origin("src/app/resource.h", text) == "production"
    assert not is_generated_header(text)


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
    assert is_vendored_or_generated_path("pedometer/src/dart-sdk/include/dart_api_dl.c")
    assert not is_vendored_or_generated_path("pedometer/src/health_connect/foo.c")
    assert not is_vendored_or_generated_path("src/Monaco/monacoSRC/min/vs/loader.js")
    assert not is_vendored_or_generated_path("src/pkg/external/client.py")
    assert not is_vendored_or_generated_path("tests/test_app.py")
    assert is_vendored_or_generated_path("src/staticresources/Libs/arbor-v0.92/src/etc.js")
    assert not is_vendored_or_generated_path("src/staticresources/Libs/npsp-slds/modal.js")


@pytest.mark.parametrize("header", [_JQUERY, _TYPEAHEAD])
@pytest.mark.parametrize(
    "folder",
    [
        "docs/_static",
        "static/js",
        "assets",
        "public/lib",
        "force-app/main/default/staticresources/Bootstrap/js",
        "StaticResourceSources/jquery",
    ],
)
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
        "Package@swift-6.0.swift",
        "Package@swift-5.10.swift",
        "Package@swift-6.swift",
        "Package@swift-6.0.1.swift",
        "sub/Package@swift-6.0.swift",
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
        ("PackageManager.swift", "production"),
        ("Package+Extensions.swift", "production"),
        ("Sources/Alamofire/PackageManager.swift", "production"),
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


def test_prefixed_migration_dirs_under_db() -> None:
    # Rails keeps retired and data migrations in prefixed directories under db/.
    assert is_migration_path("db/old_migrations/x.rb")
    assert is_migration_path("db/data_migrations/x.rb")
    # Elsewhere a name containing or ending in "migrations" is ordinary source.
    assert not is_migration_path("app/migrations_helper/x.rb")
    assert not is_migration_path("src/django_test_migrations/logic.py")
    assert not is_migration_path("django_test_migrations/contrib/unittest_case.py")


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
        ("deps/wslay.c", "production"),  # directly in deps/, no library directory
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


def _docs_site(tmp_path, folder: str, marker: str) -> None:
    (tmp_path / folder).mkdir(parents=True)
    (tmp_path / folder / marker).write_text("")


def test_docs_named_folder_with_a_site_config_is_docs(tmp_path) -> None:
    _docs_site(tmp_path, "documentation-website/Writerside", "writerside.cfg")
    _docs_site(tmp_path, "doc", "mkdocs.yml")
    _docs_site(tmp_path, "web/docs-site", "docusaurus.config.ts")
    snippet = "documentation-website/Writerside/snippets/dao/src/main/kotlin/Tables.kt"
    assert code_origin(snippet, repo_root=tmp_path) == "docs_example"
    assert code_origin("doc/gen.go", repo_root=tmp_path) == "docs_example"
    assert code_origin("web/docs-site/src/a.ts", repo_root=tmp_path) == "docs_example"
    # Without the repository root only the path rules apply.
    assert code_origin(snippet) == "production"


def test_docs_named_package_without_a_site_config_stays_production(tmp_path) -> None:
    (tmp_path / "doc").mkdir()
    (tmp_path / "doc" / "man_docs.go").write_text("package doc\n")
    (tmp_path / "documentation" / "api").mkdir(parents=True)
    (tmp_path / "documentation" / "api" / "handlers.go").write_text("")
    _docs_site(tmp_path, "doctor", "mkdocs.yml")  # not a docs name
    assert code_origin("doc/man_docs.go", repo_root=tmp_path) == "production"
    assert code_origin("documentation/api/handlers.go", repo_root=tmp_path) == "production"
    assert code_origin("doctor/check.py", repo_root=tmp_path) == "production"


@pytest.mark.parametrize(
    ("folder", "files", "path"),
    [
        ("doc-service/src", ("conf.py", "index.md"), "doc-service/src/conf.py"),
        ("doc_utils", ("conf.py", "index.rst"), "doc_utils/conf.py"),
        ("docs-api/app", ("conf.py", "_config.yml", "index.md"), "docs-api/app/conf.py"),
        ("doc", ("conf.py",), "doc/conf.py"),  # Sphinx needs its index beside conf.py
        ("doc", ("_config.yml",), "doc/site.py"),  # Jekyll needs _posts or _layouts
    ],
)
def test_generic_config_names_are_not_docs_site_evidence(tmp_path, folder, files, path) -> None:
    (tmp_path / folder).mkdir(parents=True)
    for name in files:
        (tmp_path / folder / name).write_text("")
    assert code_origin(path, repo_root=tmp_path) == "production"


def test_sphinx_and_jekyll_count_under_a_plain_doc_folder(tmp_path) -> None:
    _docs_site(tmp_path, "doc/source", "conf.py")
    (tmp_path / "doc" / "source" / "index.rst").write_text("")
    _docs_site(tmp_path, "documentation", "_config.yml")
    (tmp_path / "documentation" / "_layouts").mkdir()
    assert code_origin("doc/source/ext/roles.py", repo_root=tmp_path) == "docs_example"
    assert code_origin("documentation/plugins/x.rb", repo_root=tmp_path) == "docs_example"


_DART_PART = (
    "// Copyright 2020 The Flutter Authors.\n"
    "// Use of this source code is governed by a BSD-style license.\n"
    "\n"
    "// GENERATED CODE - DO NOT MODIFY BY HAND\n"
    "\n"
    "part of 'photo.dart';\n"
)


@pytest.mark.parametrize(
    "path",
    [
        "lib/src/photo.g.dart",
        "lib/models/user.freezed.dart",
        "lib/router/app_router.gr.dart",
        "test/api_test.mocks.dart",
        "lib/gen/assets.gen.dart",
    ],
)
def test_dart_codegen_outputs_are_generated_by_name(path: str) -> None:
    assert code_origin(path) == "generated"
    assert code_origin(path, _DART_PART) == "generated"
    assert is_vendored_or_generated_path(path)


def test_dart_injectable_config_stays_production() -> None:
    assert code_origin("lib/di/injection.config.dart") == "production"


def test_generated_banner_after_a_license() -> None:
    assert code_origin("lib/src/photo.dart", _DART_PART) == "generated"
    # Ingestion's skip rule still reads the first two lines only.
    assert not is_generated_header(_DART_PART)


@pytest.mark.parametrize(
    "line",
    [
        "// DO NOT MODIFY BY HAND: run tool/gen.dart",
        "/// Do not modify by hand; edit schema.yaml",
        "# Do not modify by hand without reading the runbook",
        "// Generated code: see docs",
        "// Generated code. See README",
    ],
)
def test_banner_like_prose_after_a_license_stays_production(line: str) -> None:
    text = f"// Copyright Acme.\n// Licensed MIT.\n{line}\nlibrary x;\n"
    assert code_origin("lib/src/x.dart", text) == "production"


def test_late_generated_comment_in_hand_written_dart_stays_production() -> None:
    text = (
        "// Copyright Acme.\n"
        "import 'package:flutter/widgets.dart';\n"
        "\n"
        "class Photo {\n"
        "  // GENERATED CODE - DO NOT MODIFY BY HAND lives in photo.g.dart\n"
        "}\n"
    )
    assert code_origin("lib/src/photo.dart", text) == "production"
    # "Generated code" as prose, not as the banner, past the first two lines.
    prose = (
        "// Copyright Acme.\n// Licensed MIT.\n"
        "// Generated code goes to lib/gen; edit the templates.\nlibrary x;\n"
    )
    assert code_origin("lib/src/x.dart", prose) == "production"


def test_minified_head_is_generated() -> None:
    blob = "!function(t,n){" + "a=1;" * 1100
    assert code_origin("chat/src/backendMain/resources/js/chat.js", blob) == "generated"
    assert code_origin("web/app.js", blob.encode()) == "generated"
    # A license line first does not hide the bundle.
    assert code_origin("web/app.js", "/*! lib v1.2 | MIT */\n" + blob) == "generated"
    # Without content the path alone decides.
    assert code_origin("chat/src/backendMain/resources/js/chat.js") == "production"


def test_long_hand_written_js_stays_production() -> None:
    body = "export function f(a, b) {\n  return a + b;\n}\n" * 200
    assert code_origin("src/math.js", body) == "production"
    # A long line or two among ordinary code is not a bundle.
    long_line = "const TABLE = [" + "1, " * 600 + "];\n"
    assert code_origin("src/table.js", long_line + body) == "production"
    # A short one-line file is too little to judge.
    assert code_origin("src/one.js", "export const x = 1;" * 50) == "production"


@pytest.mark.parametrize(
    "text",
    [
        '(self["webpackChunkapp"] = self["webpackChunkapp"] || []).push([[179], {\n',
        "(self.webpackChunk_N_E=self.webpackChunk_N_E||[]).push([[888],{\n",
        '(window["webpackJsonp"] = window["webpackJsonp"] || []).push([[0], {\n',
        "/******/ (() => { // webpackBootstrap\n/******/ \tvar __webpack_modules__ = ({});\n"
        "/******/ \tfunction __webpack_require__(moduleId) {\n",
    ],
)
def test_webpack_chunks_and_runtime_are_generated(text: str) -> None:
    assert code_origin("static/js/main.4f1c2a.js", text) == "generated"


@pytest.mark.parametrize(
    "text",
    [
        "declare const __webpack_require__: (id: string) => unknown;\n",
        "if (typeof __webpack_require__ === 'function') {\n  load();\n}\n",
        "// Webpack names chunks webpackChunk<name> on self.\nconst key = 'webpackChunk' + name;\n",
    ],
)
def test_code_about_webpack_is_not_a_chunk(text: str) -> None:
    assert code_origin("src/loader.ts", text) == "production"


def test_cocoapods_target_support_files_are_generated() -> None:
    path = "ios/Pods/Target Support Files/Pods-Runner/Pods-Runner-dummy.m"
    assert code_origin(path) == "generated"
    assert is_vendored_or_generated_path(path)
    # Generated wins over the "Tests" target name the test classifier reads.
    assert code_origin("Pods/Target Support Files/Pods-AppTests/Pods-AppTests-dummy.m") == (
        "generated"
    )


def test_a_first_party_pods_package_stays_production() -> None:
    assert code_origin("internal/k8s/pods/watcher.go") == "production"
    assert code_origin("pods/support/files.go") == "production"


def test_a_nested_docs_root_counts_only_beside_its_site_config(tmp_path) -> None:
    _docs_site(tmp_path, "website", "docusaurus.config.js")
    (tmp_path / "website" / "docs").mkdir()
    (tmp_path / "app" / "docs").mkdir(parents=True)
    assert code_origin("website/docs/examples/a.ts", repo_root=tmp_path) == "docs_example"
    assert code_origin("app/docs/page.tsx", repo_root=tmp_path) == "production"
    assert code_origin("docs/guide/a.py", repo_root=tmp_path) == "docs_example"
