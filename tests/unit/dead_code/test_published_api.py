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
