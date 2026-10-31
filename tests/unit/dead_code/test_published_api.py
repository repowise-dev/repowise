"""Public API of a published package is demoted, not reported as dead."""

from __future__ import annotations

from pathlib import Path

from repowise.core.analysis.dead_code.models import DeadCodeFindingData, DeadCodeKind
from repowise.core.analysis.dead_code.published_api import (
    PUBLISHED_API_CONFIDENCE,
    BuildFacts,
    demote_published_api,
)
from repowise.core.ingestion.resolvers.dotnet.index import build_index


def _project(root: Path, rel: str, properties: str = "") -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup>{properties}</PropertyGroup></Project>'
    )


def _export(path: str, name: str) -> DeadCodeFindingData:
    return DeadCodeFindingData(
        kind=DeadCodeKind.UNUSED_EXPORT,
        file_path=path,
        symbol_name=name,
        symbol_kind="class",
        confidence=0.6,
        reason=f"Public symbol '{name}' has no importers",
        safe_to_delete=False,
        evidence=[],
        lines=None,
        last_commit_at=None,
        commit_count_90d=0,
        primary_owner=None,
        age_days=None,
    )


def _demote(root: Path, *findings: DeadCodeFindingData) -> list[DeadCodeFindingData]:
    languages = {f.file_path: "csharp" for f in findings}
    return demote_published_api(list(findings), languages, _facts(root))


def _facts(root: Path) -> BuildFacts:
    return BuildFacts(repo_root=root, dotnet_index=build_index(root))


def test_a_packable_project_publishes_its_public_types(tmp_path: Path) -> None:
    _project(tmp_path, "src/Lib/Lib.csproj", "<PackageId>Acme.Lib</PackageId>")
    (finding,) = _demote(tmp_path, _export("src/Lib/Retry.cs", "RetrySyntax"))
    assert finding.confidence == PUBLISHED_API_CONFIDENCE
    assert "published package 'Acme.Lib'" in finding.evidence[-1]


def test_an_api_baseline_marks_a_shipped_library(tmp_path: Path) -> None:
    """``PublicAPI.Shipped.txt`` in a folder of its own, as Polly keeps it."""
    _project(tmp_path, "src/Polly/Polly.csproj")
    (tmp_path / "src/Polly/.PublicAPI").mkdir()
    (tmp_path / "src/Polly/.PublicAPI/PublicAPI.Shipped.txt").write_text("Polly.Policy\n")
    (finding,) = _demote(tmp_path, _export("src/Polly/Caching/AbsoluteTtl.cs", "AbsoluteTtl"))
    assert finding.confidence == PUBLISHED_API_CONFIDENCE


def test_an_app_project_is_left_alone(tmp_path: Path) -> None:
    """An SDK project is packable by default; silence is not a publish opt-in."""
    _project(tmp_path, "src/Web/Web.csproj")
    _project(tmp_path, "src/Tool/Tool.csproj", "<IsPackable>false</IsPackable>")
    web = _export("src/Web/ViewModels/LoginViewModel.cs", "LoginViewModel")
    tool = _export("src/Tool/Thing.cs", "Thing")
    _demote(tmp_path, web, tool)
    assert web.confidence == 0.6 and tool.confidence == 0.6
    assert web.evidence == [] and tool.evidence == []


def test_a_language_without_a_rule_is_left_alone(tmp_path: Path) -> None:
    _project(tmp_path, "src/Lib/Lib.csproj", "<PackageId>Acme.Lib</PackageId>")
    finding = _export("src/Lib/tool.py", "helper")
    demote_published_api([finding], {finding.file_path: "python"}, _facts(tmp_path))
    assert finding.confidence == 0.6


def test_a_project_nested_in_a_packaged_one_is_judged_on_its_own(tmp_path: Path) -> None:
    """A samples project inside the library folder ships nothing."""
    _project(tmp_path, "Lib/Lib.csproj", "<PackageId>My.Lib</PackageId>")
    _project(tmp_path, "Lib/Samples/Sample.csproj")
    sample = _export("Lib/Samples/Demo.cs", "Demo")
    library = _export("Lib/Api.cs", "Api")
    _demote(tmp_path, sample, library)
    assert sample.confidence == 0.6 and sample.evidence == []
    assert library.confidence == PUBLISHED_API_CONFIDENCE


# --- Other ecosystems: one rule table, read from each build's own manifest ---


def _write(root: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def _demoted(root: Path, language: str, *paths: str) -> list[bool]:
    findings = [_export(p, "Thing") for p in paths]
    demote_published_api(findings, dict.fromkeys(paths, language), BuildFacts(repo_root=root))
    return [f.confidence == PUBLISHED_API_CONFIDENCE for f in findings]


def test_a_go_library_publishes_its_importable_packages(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "go.mod": "module example.com/lib\n",
            "lib.go": "package lib\n",
            "parser/parse.go": "package parser\n",
            "internal/util/util.go": "package util\n",
            "cmd/tool/main.go": "package main\n",
        },
    )
    assert _demoted(
        tmp_path, "go", "lib.go", "parser/parse.go", "internal/util/util.go", "cmd/tool/main.go"
    ) == [True, True, False, False]


def test_a_go_program_module_is_left_alone(tmp_path: Path) -> None:
    """A module whose root is ``package main`` is an app, whatever else it holds."""
    _write(
        tmp_path,
        {"go.mod": "module example.com/app\n", "main.go": "package main\n", "pkg/x/x.go": "package x\n"},
    )
    assert _demoted(tmp_path, "go", "pkg/x/x.go") == [False]


def test_a_rust_library_crate_publishes_but_a_binary_crate_does_not(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "Cargo.toml": '[workspace]\nmembers = ["crates/*"]\n',
            "crates/lib/Cargo.toml": (
                '[package]\nname = "lib"\n[[bin]]\nname = "x"\npath = "src/cli/x.rs"\n'
            ),
            "crates/lib/src/lib.rs": "",
            "crates/lib/src/bin/tool.rs": "",
            "crates/app/Cargo.toml": '[package]\nname = "app"\n',
            "crates/app/src/main.rs": "",
            "crates/internal/Cargo.toml": '[package]\nname = "internal"\npublish = false\n',
            "crates/internal/src/lib.rs": "",
        },
    )
    assert _demoted(
        tmp_path,
        "rust",
        "crates/lib/src/walk.rs",
        "crates/lib/src/bin/tool.rs",
        "crates/lib/src/cli/x.rs",
        "crates/app/src/cli.rs",
        "crates/internal/src/x.rs",
    ) == [True, False, False, False, False]


def test_a_maven_module_publishes_only_with_deploy_evidence(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "lib/pom.xml": "<project><artifactId>lib</artifactId>"
            "<distributionManagement></distributionManagement></project>",
            "app/pom.xml": "<project><artifactId>app</artifactId>"
            "<build><plugins><plugin><artifactId>spring-boot-maven-plugin</artifactId>"
            "</plugin></plugins></build></project>",
            "plain/pom.xml": "<project><artifactId>plain</artifactId></project>",
        },
    )
    assert _demoted(
        tmp_path, "java", "lib/src/main/java/A.java", "app/src/main/java/B.java", "plain/src/main/java/C.java"
    ) == [True, False, False]


def test_a_gradle_module_publishes_through_its_root_build(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "build.gradle": "subprojects { apply plugin: 'maven-publish' }\n",
            "core/build.gradle.kts": 'plugins { `java-library` }\n',
            "sample/build.gradle.kts": "plugins { application }\n",
        },
    )
    assert _demoted(
        tmp_path, "kotlin", "core/src/main/kotlin/A.kt", "sample/src/main/kotlin/B.kt"
    ) == [True, False]


def test_a_python_distribution_publishes_its_public_modules(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "pyproject.toml": '[build-system]\nrequires = ["hatchling"]\n[project]\nname = "my-lib"\n',
            "src/my_lib/__init__.py": "",
            "src/my_lib/api.py": "",
            "src/my_lib/_impl.py": "",
            "src/my_lib/_impl.pyi": "",
            "src/my_lib/_vendor/x.py": "",
            "tests/test_api.py": "",
        },
    )
    assert _demoted(
        tmp_path,
        "python",
        "src/my_lib/api.py",
        "src/my_lib/_impl.py",
        "src/my_lib/_impl.pyi",
        "src/my_lib/_vendor/x.py",
        "tests/test_api.py",
    ) == [True, False, False, False, False]


def test_a_python_app_is_left_alone(tmp_path: Path) -> None:
    """No build backend is how an app pyproject reads (``uv init`` without ``--lib``)."""
    _write(tmp_path, {"pyproject.toml": '[project]\nname = "app"\n', "app/__init__.py": ""})
    assert _demoted(tmp_path, "python", "app/views.py") == [False]


def test_an_npm_package_publishes_unless_private(tmp_path: Path) -> None:
    _write(
        tmp_path,
        {
            "package.json": '{"name": "root", "private": true}',
            "packages/lib/package.json": '{"name": "lib", "exports": "./src/index.ts"}',
            "packages/lib/src/esm/package.json": '{"type": "module"}',
            "packages/site/package.json": '{"name": "site", "private": true, "main": "x.js"}',
            "packages/cli/package.json": '{"name": "cli", "bin": "cli.js"}',
            "packages/server/package.json": '{"name": "server", "main": "dist/main.js"}',
            "packages/old/package.json": '{"name": "old", "main": "index.js", "files": ["lib"]}',
        },
    )
    assert _demoted(
        tmp_path,
        "typescript",
        "packages/lib/src/esm/util.ts",
        "packages/site/src/a.ts",
        "packages/cli/src/b.ts",
        "src/c.ts",
        "packages/server/src/app.ts",
        "packages/old/lib/d.js",
    ) == [True, False, False, False, False, True]
