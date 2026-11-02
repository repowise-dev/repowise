"""A Go, Rust, JVM or Ruby plan's test command runs the package that owns the test."""

from __future__ import annotations

import re
from pathlib import Path

from repowise.core.analysis.health.refactoring import commands
from repowise.core.analysis.health.refactoring.batches import stage_batches
from repowise.core.analysis.health.refactoring.commands import commands_for_tests, runs_for_tests
from repowise.core.analysis.health.refactoring.recommendations import (
    ValidationEvidence,
    ValidationPlan,
    build_validation_plan,
)
from repowise.core.analysis.health.refactoring.serving import validation_from_profile
from repowise.core.analysis.test_reachability import ReachedBy
from repowise.core.ingestion.package_roots import scan_package_manifests
from tests.unit.health.test_recommendations import _plan


def _write(root: Path, files: dict[str, str]) -> None:
    for path, text in files.items():
        (root / path).parent.mkdir(parents=True, exist_ok=True)
        (root / path).write_text(text, encoding="utf-8")


def _commands(root: Path, tests: list[str]) -> list[str]:
    return commands_for_tests(tests, runs=runs_for_tests(root, scan_package_manifests(root), tests))


def test_go_runs_each_test_directory_inside_its_module(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "go.mod": "module example.com/shop\n",
            "internal/cart/cart_test.go": "package cart\n",
            "internal/tax/tax_test.go": "package tax\n",
            "services/api/go.mod": "module example.com/api\n",
            "services/api/handler/h_test.go": "package handler\n",
            "services/api/root_test.go": "package api\n",
        },
    )
    tests = [
        "internal/cart/cart_test.go::TestTotal",
        "internal/tax/tax_test.go",
        "services/api/handler/h_test.go::TestOk",
        "services/api/root_test.go",
    ]
    assert _commands(tmp_path, tests) == [
        "go -C services/api test . ./handler",
        "go test ./internal/cart ./internal/tax",
    ]


def test_go_without_a_module_gets_no_command(tmp_path: Path) -> None:
    _write(tmp_path, {"cart/cart_test.go": "package cart\n"})
    assert _commands(tmp_path, ["cart/cart_test.go"]) == []


def test_rust_runs_the_owning_crate_or_its_integration_targets(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers = ["crates/parser"]\n',
            "crates/parser/Cargo.toml": '[package]\nname = "parser"\n',
            "crates/parser/src/lib.rs": "",
            "crates/parser/tests/api.rs": "",
            "crates/parser/tests/words/main.rs": "",
            "tools/x/Cargo.toml": '[package]\nname = "x"\n',
            "tools/x/src/lib.rs": "",
        },
    )
    integration = ["crates/parser/tests/api.rs::parses_word", "crates/parser/tests/words/main.rs"]
    assert _commands(tmp_path, integration) == ["cargo test -p parser --test api --test words"]
    # A unit test lives in the crate's own targets, so the whole crate runs.
    # Cargo refuses a crate inside the root workspace that does not list it.
    unit = "crates/parser/src/lib.rs::tests::parses_empty"
    assert _commands(tmp_path, [*integration, unit, "tools/x/src/lib.rs::tests::y"]) == [
        "cargo test -p parser"
    ]


def test_rust_crate_outside_any_workspace_is_found_by_its_manifest(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "tools/x/Cargo.toml": '[package]\nname = "x"\n',
            "tools/x/src/lib.rs": "",
            "Cargo.toml": '[workspace]\nmembers = ["a"]\n',
            "nested/y/Cargo.toml": '[package]\nname = "y"\n\n[workspace]\n',
            "nested/y/src/lib.rs": "",
        },
    )
    # Its own [workspace] makes a nested crate a root cargo accepts.
    assert _commands(tmp_path, ["nested/y/src/lib.rs"]) == [
        "cargo test --manifest-path nested/y/Cargo.toml"
    ]
    (tmp_path / "Cargo.toml").unlink()
    assert _commands(tmp_path, ["tools/x/src/lib.rs"]) == [
        "cargo test --manifest-path tools/x/Cargo.toml"
    ]


def test_rust_reads_declared_test_targets(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": (
                '[package]\nname = "rg"\nautotests = false\n\n'
                '[[test]]\nname = "integration"\npath = "tests/tests.rs"\n'
            ),
            "tests/tests.rs": "",
            "tests/util.rs": "",
        },
    )
    assert _commands(tmp_path, ["tests/tests.rs"]) == ["cargo test -p rg --test integration"]
    # A module the target includes is no target of its own.
    assert _commands(tmp_path, ["tests/util.rs"]) == ["cargo test -p rg"]


def test_rust_outside_every_crate_gets_no_command(tmp_path: Path) -> None:
    _write(tmp_path, {"Cargo.toml": "[workspace]\nmembers = []\n", "scripts/a.rs": ""})
    assert _commands(tmp_path, ["scripts/a.rs"]) == []


def _pom(artifact: str, body: str = "") -> str:
    return f"<project><groupId>x</groupId><artifactId>{artifact}</artifactId>{body}</project>"


def test_maven_runs_the_reactor_module_with_the_wrapper(tmp_path: Path) -> None:
    modules = "<packaging>pom</packaging><modules><module>core</module></modules>"
    _write(
        tmp_path,
        {
            "pom.xml": _pom("root", modules),
            "mvnw": "",
            "core/pom.xml": _pom("core"),
            "core/src/test/java/x/CartTest.java": "",
            "tools/pom.xml": _pom("tools"),
            "tools/src/test/java/x/ToolTest.java": "",
        },
    )
    tests = [
        "core/src/test/java/x/CartTest.java::CartTest::total",
        "tools/src/test/java/x/ToolTest.java",
    ]
    assert _commands(tmp_path, tests) == [
        "./mvnw -f tools/pom.xml test",
        "./mvnw -pl core -am test",
    ]


def test_a_root_with_both_builds_runs_maven(tmp_path: Path) -> None:
    _write(tmp_path, {"pom.xml": _pom("app"), "build.gradle": "", "src/test/java/ATest.java": ""})
    assert _commands(tmp_path, ["src/test/java/ATest.java"]) == ["mvn test"]


def test_gradle_runs_the_project_the_settings_include(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "settings.gradle.kts": (
                'include("app", "lib:core")\n'
                'project(":lib:core").projectDir = file("libs/core")\n'
            ),
            "gradlew": "",
            "app/build.gradle.kts": "",
            "app/src/test/kotlin/x/GreeterTest.kt": "",
            "libs/core/build.gradle": "",
            "libs/core/src/test/java/x/CoreTest.java": "",
            "examples/demo/build.gradle.kts": "",
            "examples/demo/src/test/kotlin/DemoTest.kt": "",
        },
    )
    tests = [
        "app/src/test/kotlin/x/GreeterTest.kt::GreeterTest::saysHi",
        "libs/core/src/test/java/x/CoreTest.java",
        # A build the root settings do not include gets no command.
        "examples/demo/src/test/kotlin/DemoTest.kt",
    ]
    assert _commands(tmp_path, tests) == ["./gradlew :app:test", "./gradlew :lib:core:test"]


def test_ruby_runs_spec_files_or_the_rake_test_task(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Gemfile": 'gem "minitest"\n',
            "Rakefile": "Minitest::TestTask.create\n",
            "test/routing_test.rb": "",
            "engines/billing/Gemfile": 'gem "rspec"\n',
            "engines/billing/spec/invoice_spec.rb": "",
            "engines/billing/spec/price_spec.rb": "",
            "engines/billing/spec/support/helper.rb": "",
        },
    )
    specs = ["engines/billing/spec/invoice_spec.rb", "engines/billing/spec/price_spec.rb"]
    assert _commands(tmp_path, ["test/routing_test.rb::RoutingTest::test_get", *specs]) == [
        "bundle exec rake test",
        "cd engines/billing && bundle exec rspec spec/invoice_spec.rb spec/price_spec.rb",
    ]
    # A spec helper is no example file, so the whole suite runs.
    helper = "engines/billing/spec/support/helper.rb"
    assert _commands(tmp_path, [*specs, helper]) == ["cd engines/billing && bundle exec rspec"]


def test_ruby_without_a_gemfile_or_test_task_gets_no_command(tmp_path: Path) -> None:
    _write(tmp_path, {"test/a_test.rb": "", "lib/Gemfile": "", "lib/test/b_test.rb": ""})
    assert _commands(tmp_path, ["test/a_test.rb", "lib/test/b_test.rb"]) == []


def test_ruby_runs_rspec_only_for_a_gem_that_uses_it(tmp_path: Path) -> None:
    spec = "spec/cart_spec.rb"
    _write(tmp_path, {"Gemfile": 'gem "minitest"\n', spec: ""})
    assert _commands(tmp_path, [spec]) == []
    (tmp_path / "Rakefile").write_text("task :test do\nend\n", encoding="utf-8")
    assert _commands(tmp_path, [spec]) == ["bundle exec rake test"]
    (tmp_path / "shop.gemspec").write_text('s.add_development_dependency "rspec"\n', "utf-8")
    assert _commands(tmp_path, [spec]) == [f"bundle exec rspec {spec}"]
    (tmp_path / "shop.gemspec").unlink()
    (tmp_path / ".rspec").write_text("--require spec_helper\n", encoding="utf-8")
    assert _commands(tmp_path, [spec]) == [f"bundle exec rspec {spec}"]


def test_args_sharing_a_flag_merge_their_values(monkeypatch, tmp_path: Path) -> None:
    def per_test(repo, test: str):
        # A runner sees the whole id, so it can name the single test.
        return "dotnet test t.csproj", f"--filter={test.rsplit('::', 1)[-1]}"

    monkeypatch.setattr(commands, "_RUNNERS", (((".cs",), per_test),))
    tests = ["t/CartTests.cs::Shop.CartTests.Total", "t/CartTests.cs::Shop.CartTests.Empty"]
    runs = commands.runs_for_tests(tmp_path, {}, tests)
    assert commands_for_tests(tests, runs=runs) == [
        'dotnet test t.csproj --filter "Shop.CartTests.Empty|Shop.CartTests.Total"'
    ]
    assert commands_for_tests(tests[:1], runs=runs) == [
        'dotnet test t.csproj --filter "Shop.CartTests.Total"'
    ]
    # Repeated flags without ``=`` stay repeated, as cargo's ``--test`` needs.
    repeated = {"a.rs": ["cargo test -p a", "--test x"], "b.rs": ["cargo test -p a", "--test y"]}
    assert commands_for_tests(["a.rs", "b.rs"], runs=repeated) == [
        "cargo test -p a --test x --test y"
    ]


def test_the_wire_type_lists_every_stored_validation_field() -> None:
    source = (
        Path(__file__).resolve().parents[3] / "packages" / "types" / "src" / "refactoring.ts"
    ).read_text(encoding="utf-8")
    match = re.search(r"interface RecommendationValidation \{(.*?)\n\}", source, re.DOTALL)
    assert match, "RecommendationValidation is not declared in refactoring.ts"
    body = re.sub(r"/\*.*?\*/", "", match.group(1), flags=re.DOTALL)
    fields = set(re.findall(r"^\s*(\w+)\??:", body, re.MULTILINE))
    plan = ValidationPlan(
        "unknown", None, 0, [], False, [], [], [],
        test_runs={"a_test.go": ["go test", "."]},
    )
    assert fields == set(plan.as_dict())


def test_python_and_js_commands_are_unchanged_beside_other_languages() -> None:
    runs = {"a/a_test.go": ["go test", "./a"], "web/a.test.ts": ["npm test --", "web/a.test.ts"]}
    tests = ["tests/test_a.py", "web/a.test.ts", "a/a_test.go"]
    assert commands_for_tests(tests, runs=runs) == [
        "pytest tests/test_a.py",
        "npm test -- web/a.test.ts",
        "go test ./a",
    ]


def test_the_plan_stores_its_runs_and_batches_reuse_them() -> None:
    test = "internal/cart/cart_test.go::TestTotal"
    runs = {"internal/cart/cart_test.go": ["go test", "./internal/cart"]}
    suggestion = _plan("Total", file_path="internal/cart/cart.go")
    inferred = {suggestion.file_path: ReachedBy([test], "import-graph", 1)}
    plan = build_validation_plan(
        suggestion, {}, inferred, evidence=ValidationEvidence(test_runs=runs)
    )
    assert plan.commands == ["go test ./internal/cart"]
    stored = plan.as_dict()
    assert stored["test_runs"] == runs
    assert validation_from_profile(stored).test_runs == runs
    assert "test_runs" not in build_validation_plan(suggestion, {}, inferred).as_dict()
    detail = {
        "refactoring_type": "extract_method",
        "validation": {"tests": [test], "total": 1, "test_runs": runs},
    }
    batches = stage_batches(detail, [{"n": n, "action": "extract"} for n in range(1, 8)])
    assert batches is not None
    assert batches[0]["commands"] == ["go test ./internal/cart"]
