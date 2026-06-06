"""Tests for the architectural layer spine (generation.layers)."""

from __future__ import annotations

from repowise.core.generation.layers import (
    DEFAULT_LAYER,
    compute_layer_order,
    infer_layer,
)

# ---------------------------------------------------------------------------
# infer_layer — every file maps to exactly one layer
# ---------------------------------------------------------------------------


def test_infer_layer_matches_directory_hints():
    assert infer_layer("src/api/users.py") == "API"
    assert infer_layer("app/services/billing.py") == "Service"
    assert infer_layer("pkg/models/user.py") == "Data"
    assert infer_layer("web/components/Button.tsx") == "UI"
    assert infer_layer("src/middleware/auth.ts") == "Middleware"
    assert infer_layer("lib/utils/strings.py") == "Utility"
    assert infer_layer("config/settings.py") == "Config"
    assert infer_layer("tests/test_user.py") == "Test"
    assert infer_layer("src/types/dtos.ts") == "Types"


def test_infer_layer_recognizes_cli_command_surface():
    # Edge case A: a CLI command surface must not fall through to Application.
    assert infer_layer("packages/cli/src/repowise/cli/commands/init_cmd.py") == "CLI"
    assert infer_layer("src/cli/main.py") == "CLI"
    assert infer_layer("app/cmd/serve.py") == "CLI"


def test_infer_layer_uses_deepest_matching_directory():
    # The closest directory wins over a shallower one.
    assert infer_layer("services/api/handler.py") == "API"


def test_infer_layer_falls_back_for_unmatched_paths():
    assert infer_layer("main.py") == DEFAULT_LAYER
    assert infer_layer("random/folder/thing.py") == DEFAULT_LAYER


def test_infer_layer_spec_dirs_need_test_shaped_files():
    # A "specs/" directory of ordinary modules is a specification folder —
    # the scan continues outward instead of branding them tests.
    assert infer_layer("core/ingestion/languages/specs/dockerfile.py") == "Service"
    assert infer_layer("specs/openapi.py") == DEFAULT_LAYER
    # With a test-shaped filename the same dirs DO mean tests.
    assert infer_layer("specs/auth.spec.ts") == "Test"
    assert infer_layer("spec/test_auth.py") == "Test"


def test_infer_layer_unambiguous_test_dirs_take_any_file():
    # tests/ and __tests__/ are test roots regardless of filename.
    assert infer_layer("tests/helpers.py") == "Test"
    assert infer_layer("tests/conftest.py") == "Test"
    assert infer_layer("src/__tests__/render.tsx") == "Test"


def test_infer_layer_colocated_test_files_are_test():
    # Go and Jest colocate tests beside sources — the filename is the signal.
    assert infer_layer("middleware/compress_test.go") == "Test"
    assert infer_layer("src/components/Button.test.tsx") == "Test"
    assert infer_layer("rack-protection/spec/spec_helper.rb") == "Test"


def test_infer_layer_ambiguous_spec_dir_needs_language_corroboration():
    # Ruby declares spec/ as an unambiguous test-dir token: a Ruby file under
    # spec/ is RSpec material whatever its name, so config.ru is Test only when
    # the language is supplied. Omitting language leaves the ambiguous "spec"
    # token uncorroborated and the file falls through (not Test) — this
    # divergence is exactly what the curation/tour guard sites must respect by
    # passing language to infer_layer.
    assert infer_layer("spec/dummy/config.ru", "ruby") == "Test"
    assert infer_layer("spec/dummy/config.ru") != "Test"


def test_infer_layer_root_dot_dirs_are_tooling_config():
    # .agents/plugins/* must not mint a phantom Middleware layer.
    assert infer_layer(".agents/plugins/marketplace.json") == "Config"
    assert infer_layer(".github/workflows/ci.yml") == "Config"
    assert infer_layer(".claude/CLAUDE.md") == "Config"


def test_infer_layer_test_root_beats_deeper_hints():
    # A fixture under tests/models/ is a test, not Data — the test root wins
    # over any deeper directory hint.
    assert infer_layer("tests/models/__init__.py") == "Test"
    assert infer_layer("tests/test_apps/cliapp/app.py") == "Test"
    assert infer_layer("test/api/fixtures.py") == "Test"


# ---------------------------------------------------------------------------
# Case-sensitive camel-suffix test conventions (per language)
# ---------------------------------------------------------------------------


def test_infer_layer_camel_suffix_tests_per_language():
    # The convention's own case applies: FooTest.java is a test wherever it sits.
    assert infer_layer("gson/src/main/java/com/google/gson/JsonParserTest.java") == "Test"
    assert infer_layer("src/GsonTests.java") == "Test"
    assert infer_layer("src/SplitFunctionalIT.java") == "Test"
    assert infer_layer("app/PaymentServiceTest.kt") == "Test"
    assert infer_layer("app/CheckoutSpec.kt") == "Test"
    assert infer_layer("core/ParserSpec.scala") == "Test"
    assert infer_layer("core/RouterSuite.scala") == "Test"
    assert infer_layer("Billing/InvoiceTests.cs") == "Test"
    assert infer_layer("Billing/InvoiceSpecs.cs") == "Test"
    assert infer_layer("Sources/AppCore/RouterTests.swift") == "Test"
    assert infer_layer("src/Service/MailerTest.php") == "Test"
    assert infer_layer("src/ParserSpec.hs") == "Test"


def test_infer_layer_camel_suffix_false_positive_guards():
    # Lowercase boundaries and bare names never match (D-005): `latest`,
    # `contest`, `Test.java` (no prefix), `MyTestimony` are not tests.
    assert infer_layer("src/latest.java") != "Test"
    assert infer_layer("src/contest.cs") != "Test"
    assert infer_layer("src/Test.java") != "Test"
    assert infer_layer("app/MyTestimony.kt") != "Test"
    assert infer_layer("src/UNIT.java") != "Test"  # uppercase boundary before IT
    # Conventions don't leak across languages: Java's IT rule means nothing
    # for Python; Haskell's Spec rule means nothing for Go.
    assert infer_layer("src/SplitIT.py") != "Test"
    assert infer_layer("pkg/HandlerSpec.go") != "Test"


def test_infer_layer_camel_suffix_works_without_lowercase_filename_callers():
    # sinatra's vendored spec/okjson.rb regression: an ambiguous spec/ dir
    # with a non-test-shaped file stays non-test even with camel rules live.
    assert infer_layer("rack-protection/spec/okjson.rb") != "Test"


def test_infer_layer_multi_segment_test_roots():
    # Maven/Gradle/sbt sourceset roots mark any file beneath them.
    assert infer_layer("module/src/it/java/com/x/FlowVerifier.java") == "Test"
    assert infer_layer("module/src/integrationTest/java/com/x/Flow.java") == "Test"
    assert infer_layer("core/src/it/scala/com/x/Pipeline.scala") == "Test"
    # src/test/java was already covered by the generic "test" token.
    assert infer_layer("gson/src/test/java/com/google/gson/Helper.java") == "Test"
    # "it" alone is NOT a test token — only the full sourceset shape matches.
    assert infer_layer("docs/it/translation.md") != "Test"
    assert infer_layer("src/it/locale.py") != "Test"


def test_infer_layer_dotnet_test_project_dirs():
    # Sibling Foo.Tests/ projects are test roots for everything inside.
    assert infer_layer("Billing.Tests/InvoiceFixture.cs") == "Test"
    assert infer_layer("src/Billing.Tests/data/sample.json") == "Test"
    # Case matters: a lowercase "billing.tests" dir is not the convention.
    assert infer_layer("billing.tests/notes.md") != "Test"


# ---------------------------------------------------------------------------
# Per-language layer-dir hints
# ---------------------------------------------------------------------------


def test_infer_layer_go_internal_pkg_hints():
    assert infer_layer("internal/auth.go", "go") == "Service"
    assert infer_layer("pkg/render/render.go", "go") == "Service"
    # Without the language (or for another language) the hint never fires.
    assert infer_layer("internal/auth.go") == DEFAULT_LAYER
    assert infer_layer("internal/auth.py", "python") == DEFAULT_LAYER
    # A deeper generic hint still wins over the language hint.
    assert infer_layer("internal/handlers/auth.go", "go") == "API"


def test_infer_layer_rust_cli_hints():
    assert infer_layer("src/bin/extra.rs", "rust") == "CLI"
    assert infer_layer("crates/typst-cli/src/main.rs", "rust") == "CLI"
    assert infer_layer("src/bin/extra.rs") == DEFAULT_LAYER
    # Case-sensitive suffix; an unrelated "Bin" or non-rust file never matches.
    assert infer_layer("crates/typst-cli/src/main.py", "python") == DEFAULT_LAYER
    # A dir literally named "-cli" is not the convention (proper suffix only).
    assert infer_layer("-cli/main.rs", "rust") == DEFAULT_LAYER


def test_infer_layer_ruby_rails_jobs():
    assert infer_layer("app/jobs/cleanup_job.rb", "ruby") == "Service"
    assert infer_layer("app/jobs/cleanup.py", "python") == DEFAULT_LAYER


def test_infer_layer_dotnet_project_suffixes():
    assert infer_layer("src/Billing.Api/Program.cs", "csharp") == "API"
    assert infer_layer("Billing.Domain/Invoice.cs", "csharp") == "Service"
    assert infer_layer("Billing.Infrastructure/Repo.cs", "csharp") == "Data"
    # Generic deeper dirs still win; lowercase variants are not the convention.
    assert infer_layer("src/Billing.Api/models/Dto.cs", "csharp") == "Data"
    assert infer_layer("billing.api/Program.cs", "csharp") == DEFAULT_LAYER


def test_infer_layer_test_rules_beat_language_hints():
    # A camel test inside a hinted dir is still a test.
    assert infer_layer("internal/AuthTest.java", "java") == "Test"
    assert infer_layer("pkg/render/render_test.go", "go") == "Test"


# ---------------------------------------------------------------------------
# compute_layer_order — top→bottom by dependency direction
# ---------------------------------------------------------------------------


def test_compute_layer_order_follows_dependency_direction():
    file_layers = {
        "api/h.py": "API",
        "services/s.py": "Service",
        "models/m.py": "Data",
    }
    # API imports Service imports Data — a clean stack.
    edges = [
        ("api/h.py", "services/s.py"),
        ("services/s.py", "models/m.py"),
    ]
    order = compute_layer_order(file_layers, edges)
    assert order.index("API") < order.index("Service") < order.index("Data")


def test_compute_layer_order_ignores_external_and_intra_layer_edges():
    file_layers = {"api/a.py": "API", "api/b.py": "API", "data/d.py": "Data"}
    edges = [
        ("api/a.py", "api/b.py"),  # intra-layer — ignored
        ("api/a.py", "external:requests"),  # external — ignored
        ("api/a.py", "data/d.py"),  # API → Data
    ]
    order = compute_layer_order(file_layers, edges)
    assert order.index("API") < order.index("Data")


def test_compute_layer_order_stable_without_edges():
    file_layers = {"a.py": "API", "b.py": "Utility", "c.py": "Data"}
    order = compute_layer_order(file_layers, [])
    # Falls back to canonical rank: API above Data above Utility.
    assert order == ["API", "Data", "Utility"]


def test_compute_layer_order_single_layer():
    assert compute_layer_order({"a.py": "API"}, []) == ["API"]
    assert compute_layer_order({}, []) == []


def test_compute_layer_order_tests_never_top_the_stack():
    # Tests import everything and are imported by nothing — by raw import
    # math they'd win "top consumer" in every codebase. They must be pinned
    # after the runtime layers instead.
    file_layers = {
        "api/h.py": "API",
        "services/s.py": "Service",
        "tests/test_h.py": "Test",
        "tests/test_s.py": "Test",
    }
    edges = [
        ("api/h.py", "services/s.py"),
        ("tests/test_h.py", "api/h.py"),
        ("tests/test_h.py", "services/s.py"),
        ("tests/test_s.py", "services/s.py"),
        ("tests/test_s.py", "api/h.py"),
    ]
    order = compute_layer_order(file_layers, edges)
    assert order[-1] == "Test"
    # Test edges don't distort the runtime ordering either.
    assert order.index("API") < order.index("Service")


class TestRubySpecDirToken:
    def test_ruby_file_under_spec_is_test_without_corroboration(self):
        # RSpec convention: support helpers and vendored fixtures under
        # spec/ are test material whatever their filename.
        assert infer_layer("sinatra-contrib/spec/okjson.rb", language="ruby") == "Test"

    def test_spec_dir_stays_ambiguous_for_other_languages(self):
        # Polyglot fairness: ruby's rule never leaks to other languages.
        assert infer_layer("specs/openapi.yaml", language="yaml") != "Test"
        assert infer_layer("spec/helper.py", language="python") != "Test"


def test_infer_layer_gradle_test_sourcesets():
    # okio regression: multiplatform test sourcesets (src/jvmTest,
    # src/commonTest) are test roots for any file beneath them.
    assert infer_layer("okio/src/jvmTest/kotlin/okio/AsyncSocket.kt", "kotlin") == "Test"
    assert infer_layer("okio/src/commonTest/kotlin/okio/util/Helpers.kt", "kotlin") == "Test"
    assert infer_layer("m/src/integrationTest/kotlin/F.kt", "kotlin") == "Test"
    # Main sourcesets and non-src camel dirs stay untouched.
    assert infer_layer("okio/src/commonMain/kotlin/okio/Buffer.kt", "kotlin") != "Test"
    assert infer_layer("okio/src/hashFunctions/kotlin/H.kt", "kotlin") != "Test"
    # The wildcard requires the camel "Test" suffix — lowercase doesn't fire
    # (src/test is its own exact rule via the generic token).
    assert infer_layer("a/src/latest/kotlin/F.kt", "kotlin") != "Test"


def test_infer_layer_dotnet_specs_project_dirs():
    # Polly evidence: BDD-style sibling test projects named Foo.Specs/.
    assert infer_layer("test/Polly.Specs/Retry/RetrySpecs.cs", "csharp") == "Test"


def test_infer_layer_c_cpp_include_is_api():
    # A C/C++ library's installed public headers are its API surface.
    assert infer_layer("include/uv.h", "c") == "API"
    assert infer_layer("include/uv/unix.h", "c") == "API"
    assert infer_layer("include/fmt/format.h", "cpp") == "API"
    # Polyglot fairness: the hint rides on the file's own language.
    assert infer_layer("include/util.py", "python") != "API"
    # Root-anchored: okio's vendored linux uapi headers must not mint an
    # API layer from deep inside a Kotlin repo.
    assert infer_layer("okio/src/linuxMain/headers/include/uapi/linux/stat.h", "cpp") != "API"


def test_is_support_path_covers_doc_dirs():
    from repowise.core.generation.layers import is_support_path

    # Doc sites and runnable doc snippets are support material (libuv's
    # docs/code/*/main.c, docfx templates, vitepress sites).
    assert is_support_path("docs/code/spawn/main.c")
    assert is_support_path("docs/template/public/main.js")
    assert is_support_path("website/.vitepress/theme/index.ts")
    assert is_support_path("doc/build.py")
    assert is_support_path("examples/hello/main.go")
    # Real code is not support — names containing "docs" don't fire.
    assert not is_support_path("src/docs_generator.py")
    assert not is_support_path("lib/init.lua")
