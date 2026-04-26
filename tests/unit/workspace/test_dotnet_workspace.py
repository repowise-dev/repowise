"""Workspace-level tests for .NET / C# support added in Batch 5.

Covers:
- ASP.NET HTTP contract extraction (provider attributes + minimal API +
  HttpClient consumer)
- gRPC-dotnet contract extraction (MapGrpcService, generated-base
  inheritance, generated-Client consumers)
- Cross-repo .NET package dependency scanner: ProjectReference and
  internal-NuGet (PackageReference id matching a sibling AssemblyName).
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from repowise.core.workspace.cross_repo import (
    _scan_csproj,
    detect_package_dependencies,
)
from repowise.core.workspace.extractors.grpc_extractor import GrpcExtractor
from repowise.core.workspace.extractors.http_extractor import HttpExtractor


# ---------------------------------------------------------------------------
# HTTP — ASP.NET
# ---------------------------------------------------------------------------


class TestAspNetHttpExtraction:
    def test_attribute_routing_with_class_prefix(self, tmp_path: Path) -> None:
        (tmp_path / "Controllers").mkdir()
        (tmp_path / "Controllers" / "UsersController.cs").write_text(
            dedent(
                """\
                using Microsoft.AspNetCore.Mvc;
                namespace Acme.Api;

                [ApiController]
                [Route("api/users")]
                public class UsersController : ControllerBase
                {
                    [HttpGet("{id:int}")]
                    public IActionResult Get(int id) => Ok();

                    [HttpPost]
                    public IActionResult Create() => Ok();
                }
                """
            )
        )
        contracts = HttpExtractor().extract(tmp_path, repo_alias="api")
        provider_paths = {(c.meta["method"], c.meta["path"]) for c in contracts if c.role == "provider"}
        assert ("GET", "/api/users/{param}") in provider_paths
        assert ("POST", "/api/users") in provider_paths

    def test_minimal_api(self, tmp_path: Path) -> None:
        (tmp_path / "Program.cs").write_text(
            dedent(
                """\
                var app = WebApplication.CreateBuilder(args).Build();
                app.MapGet("/health", () => "ok");
                app.MapPost("/orders", (Order o) => Results.Created());
                """
            )
        )
        contracts = HttpExtractor().extract(tmp_path, repo_alias="api")
        paths = {(c.meta["method"], c.meta["path"]) for c in contracts if c.role == "provider"}
        assert ("GET", "/health") in paths
        assert ("POST", "/orders") in paths

    def test_httpclient_consumer(self, tmp_path: Path) -> None:
        (tmp_path / "OrderClient.cs").write_text(
            dedent(
                """\
                public class OrderClient {
                    private readonly HttpClient _http;
                    public async Task Pull() {
                        await _http.GetAsync("/api/orders/123");
                    }
                }
                """
            )
        )
        contracts = HttpExtractor().extract(tmp_path, repo_alias="web")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert any(
            c.meta["client"] == "httpclient"
            and c.meta["method"] == "GET"
            and c.meta["path"] == "/api/orders/123"
            for c in consumers
        )


# ---------------------------------------------------------------------------
# gRPC-dotnet
# ---------------------------------------------------------------------------


class TestGrpcDotnetExtraction:
    def test_map_grpc_service_provider(self, tmp_path: Path) -> None:
        (tmp_path / "Program.cs").write_text(
            dedent(
                """\
                builder.Services.AddGrpc();
                var app = builder.Build();
                app.MapGrpcService<GreeterService>();
                """
            )
        )
        contracts = GrpcExtractor().extract(tmp_path, repo_alias="api")
        provider_services = {c.meta["service"] for c in contracts if c.role == "provider"}
        # MapGrpcService<T>() captures the literal type-arg name. Either
        # `Greeter` (raw) or `GreeterService` (suffixed) is correct here.
        assert "GreeterService" in provider_services or "Greeter" in provider_services

    def test_generated_base_provider(self, tmp_path: Path) -> None:
        (tmp_path / "GreeterImpl.cs").write_text(
            dedent(
                """\
                namespace Acme;
                public class GreeterImpl : Greeter.GreeterBase
                {
                }
                """
            )
        )
        contracts = GrpcExtractor().extract(tmp_path, repo_alias="api")
        assert any(c.role == "provider" and c.meta["service"] == "Greeter" for c in contracts)

    def test_generated_client_consumer(self, tmp_path: Path) -> None:
        (tmp_path / "Caller.cs").write_text(
            dedent(
                """\
                using Grpc.Net.Client;
                public class Caller {
                    public void Go() {
                        var channel = GrpcChannel.ForAddress("https://api");
                        var client = new GreeterClient(channel);
                    }
                }
                """
            )
        )
        contracts = GrpcExtractor().extract(tmp_path, repo_alias="web")
        consumers = [c for c in contracts if c.role == "consumer"]
        assert any(c.meta["service"] == "Greeter" for c in consumers)


# ---------------------------------------------------------------------------
# Cross-repo .NET dependencies
# ---------------------------------------------------------------------------


def _csproj(deps: list[str] = (), assembly_name: str | None = None,
            packages: list[str] = ()) -> str:
    refs = "\n".join(f'    <ProjectReference Include="{p}" />' for p in deps)
    pkgs = "\n".join(f'    <PackageReference Include="{p}" Version="1.0.0" />' for p in packages)
    asm = f"<AssemblyName>{assembly_name}</AssemblyName>" if assembly_name else ""
    return dedent(
        f"""\
        <Project Sdk="Microsoft.NET.Sdk">
          <PropertyGroup>
            <TargetFramework>net8.0</TargetFramework>
            {asm}
          </PropertyGroup>
          <ItemGroup>
        {refs}
        {pkgs}
          </ItemGroup>
        </Project>
        """
    )


class TestScanCsproj:
    def _two_repo_workspace(self, root: Path) -> dict[str, Path]:
        api = root / "api-service"
        shared = root / "shared-libs"
        (api / "src" / "Api").mkdir(parents=True)
        (shared / "src" / "Common").mkdir(parents=True)
        (shared / "src" / "Common" / "Common.csproj").write_text(
            _csproj(assembly_name="Acme.Common")
        )
        return {"api": api, "shared": shared}

    def test_project_reference_emits_cross_repo_dep(self, tmp_path: Path) -> None:
        repos = self._two_repo_workspace(tmp_path)
        # api references shared via relative ProjectReference
        (repos["api"] / "src" / "Api" / "Api.csproj").write_text(
            _csproj(deps=[r"..\..\..\shared-libs\src\Common\Common.csproj"])
        )
        deps = _scan_csproj(repos["api"], repos, alias="api")
        assert any(
            d.source_repo == "api"
            and d.target_repo == "shared"
            and d.kind == "dotnet_project_ref"
            for d in deps
        )

    def test_internal_nuget_emits_cross_repo_dep(self, tmp_path: Path) -> None:
        repos = self._two_repo_workspace(tmp_path)
        # api consumes the published Acme.Common NuGet package whose
        # AssemblyName lives in `shared-libs`.
        (repos["api"] / "src" / "Api" / "Api.csproj").write_text(
            _csproj(packages=["Acme.Common"])
        )
        deps = _scan_csproj(repos["api"], repos, alias="api")
        assert any(
            d.source_repo == "api"
            and d.target_repo == "shared"
            and d.kind == "dotnet_nuget_internal"
            for d in deps
        )

    def test_external_nuget_does_not_emit_cross_repo_dep(self, tmp_path: Path) -> None:
        repos = self._two_repo_workspace(tmp_path)
        (repos["api"] / "src" / "Api" / "Api.csproj").write_text(
            _csproj(packages=["Newtonsoft.Json"])
        )
        deps = _scan_csproj(repos["api"], repos, alias="api")
        assert not any(d.kind == "dotnet_nuget_internal" for d in deps)

    def test_detect_package_dependencies_includes_csproj_scanner(self, tmp_path: Path) -> None:
        repos = self._two_repo_workspace(tmp_path)
        (repos["api"] / "src" / "Api" / "Api.csproj").write_text(
            _csproj(deps=[r"..\..\..\shared-libs\src\Common\Common.csproj"])
        )
        deps = detect_package_dependencies(repos)
        kinds = {d.kind for d in deps}
        assert "dotnet_project_ref" in kinds
