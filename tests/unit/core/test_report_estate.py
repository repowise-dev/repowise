"""`repowise report estate`: the workspace-scope section seam and the estate sections,
read from stored workspace artifacts."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from repowise.core.report.build import assemble, available_sections, collect_workspace
from repowise.core.report.model import (
    SCOPES,
    Section,
    WorkspaceReportContext,
    report_sections,
)
from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    ContractStore,
    save_contract_store,
)
from repowise.core.workspace.diagnostics import (
    ExtractionDiagnostics,
    RepoDiagnostics,
    UnmatchedConsumer,
)
from repowise.core.workspace.system_graph import (
    SystemEdge,
    SystemGraph,
    SystemNode,
    save_system_graph,
)

_PROV = {"head": "a" * 40, "extracted_at": "2026-10-01T00:00:00+00:00"}


def _node(repo: str, **kw) -> SystemNode:
    return SystemNode(id=repo, repo=repo, service_path=None, name=repo, **kw)


def _edge(src: str, dst: str, kind: str = "http") -> SystemEdge:
    return SystemEdge(
        id=f"{src}->{dst}:{kind}",
        source=src,
        target=dst,
        kind=kind,
        match_type="exact",
        confidence=0.9,
        weight=2,
        structural=True,
        strategy="exact_id",
    )


def _unmatched(repo: str, cid: str, reason: str = "no_provider", path: str = "client.py"):
    return UnmatchedConsumer(repo, path, cid, cid.split("::")[0], reason)


def _consumer(repo: str, cid: str, path: str = "client.py", line: int = 3, **meta) -> Contract:
    return Contract(repo, cid, cid.split("::")[0], "consumer", path, "call", 0.9, line=line, meta=meta)


def _workspace(root: Path, *, edges: bool = True, dangling: int = 1) -> Path:
    """A three-repo workspace: web calls api; worker is isolated."""
    unmatched = [_unmatched("web", f"http::GET::/missing/{i}") for i in range(dangling)]
    unmatched += [
        _unmatched("web", "http::GET::/self", "internal_only"),
        _unmatched("web", "http::GET::/v1/charges", path="pay.py"),
    ]
    graph = SystemGraph(
        generated_at="2026-10-01T00:00:00+00:00",
        nodes=[
            _node("web", consumer_count=3),
            _node("api", provider_count=2),
            _node("worker", provider_count=1, is_isolated=True),
        ],
        edges=[_edge("web", "api")] if edges else [],
        diagnostics=ExtractionDiagnostics(
            total_providers=3,
            total_consumers=4,
            total_links=1,
            repo_breakdown=[
                RepoDiagnostics("web", consumers_by_type={"http": 4}, consumer_count=4),
                RepoDiagnostics("api", providers_by_type={"http": 2}, provider_count=2),
            ],
            unmatched_consumers=unmatched,
            unrecognised_call_files={"web": 1},
        ),
        repo_provenance={"web": _PROV, "api": _PROV, "worker": _PROV, "docs": _PROV},
    )
    save_system_graph(graph, root)
    store = ContractStore(
        contracts=[
            _consumer("web", "http::GET::/missing/0", line=12, target_repo="billing"),
            _consumer("web", "http::GET::/v1/charges", path="pay.py", external=True, host="api.stripe.com"),
        ],
        contract_links=[
            ContractLink(
                "http::GET::/users", "http", "exact", 0.9,
                "api", "routes.py", "users", None, "web", "client.py", "call", None,
            )
        ],
    )
    save_contract_store(store, root)
    (root / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos:\n"
        + "".join(f"  - path: {r}\n    alias: {r}\n" for r in ("web", "api", "worker", "docs")),
        encoding="utf-8",
    )
    return root


def _ctx(root: Path) -> WorkspaceReportContext:
    return WorkspaceReportContext(
        root,
        repos=[
            {"alias": "web", "head_commit": "a" * 40},
            {"alias": "api", "head_commit": "b" * 40},
            {"alias": "worker", "head_commit": "a" * 40},
            {"alias": "docs", "name": "docs", "indexed": False},
        ],
    )


async def _estate(ctx: WorkspaceReportContext) -> dict[str, dict]:
    sections = await collect_workspace(ctx, available_sections("estate"))
    return {s["id"]: {c["name"]: c for c in s["checks"]} for s in sections}


# ---------------------------------------------------------------------------
# The seam
# ---------------------------------------------------------------------------


def test_every_registered_section_declares_a_known_scope():
    available_sections()  # imports the local sections
    for name in report_sections.names:
        assert report_sections.get(name).scope in SCOPES, name
    estate = available_sections("estate")
    assert estate and all(s.scope == "workspace" for _, s in estate)
    with pytest.raises(ValueError, match="scope"):
        Section("Bad", "b", None, scope="estate")


async def test_workspace_sections_run_once_and_repo_sections_skip_them(tmp_path, monkeypatch):
    from repowise.core.report import build

    calls = []

    async def ws(ctx):
        calls.append(ctx)
        raise OSError(f"cannot read {ctx.ws_root / 'x.json'}")

    async def entry(_ctx):
        return {}

    sections = [("ws", Section("Ws", "w", ws, scope="workspace"))]
    monkeypatch.setattr(build, "_index_entry", entry)
    assert await build.collect_repo(None, sections) == ({}, [])
    (section,) = await collect_workspace(WorkspaceReportContext(tmp_path), sections)
    assert len(calls) == 1
    assert section["repo"] is None and section["failed"] is True
    assert section["checks"][0]["details"] == ["cannot read x.json"]
    assert section["checks"][0]["documentation"]["url"].endswith("#repowise-report-assessment-path")


def test_assemble_appends_workspace_sections_after_the_repos():
    repo = {"id": "hotspots", "repo": "shop", "checks": []}
    ws = {"id": "estate-summary", "repo": None, "checks": []}
    doc = assemble({"kind": "workspace", "name": "w"}, [({"alias": "shop"}, [repo])], workspace=[ws])
    assert doc["sections"] == [repo, ws]


# ---------------------------------------------------------------------------
# The estate sections
# ---------------------------------------------------------------------------


async def test_without_a_system_graph_every_estate_section_is_unresolved(tmp_path):
    found = await _estate(WorkspaceReportContext(tmp_path))
    items = [c for checks in found.values() for c in checks.values()]
    assert len(found) == 6 and all(c["basis"] == "unresolved" for c in items)
    assert "repowise update --workspace" in items[0]["reason"]


async def test_summary_names_unindexed_unread_and_stale_repos(tmp_path):
    found = await _estate(_ctx(_workspace(tmp_path)))
    summary = found["estate-summary"]
    repos = summary["repositories"]
    assert repos["reason"].startswith("4 configured: 3 indexed, 4 read by contract extraction")
    assert "not indexed, so nothing about them is known: docs" in repos["details"]
    assert any(d.startswith("api: contracts read at aaaaaaaaaaaa") for d in repos["details"])
    assert summary["services"]["reason"].endswith("1 isolated")
    evidence = summary["evidence"]
    assert evidence["reason"].startswith("partial") and evidence["basis"] == "measured"
    assert any("worker" in d for d in evidence["details"])


async def test_architecture_is_not_determined_without_cross_service_edges(tmp_path):
    found = await _estate(_ctx(_workspace(tmp_path, edges=False)))
    arch = found["estate-map"]["architecture"]
    assert arch["score"] == -1 and arch["basis"] == "unresolved"
    assert arch["reason"].startswith("not determined:")
    assert found["estate-summary"]["evidence"]["reason"].startswith("insufficient")


async def test_a_partial_score_says_so_beside_it(tmp_path):
    found = await _estate(_ctx(_workspace(tmp_path)))
    arch = found["estate-map"]["architecture"]
    assert arch["score"] is not None and arch["score"] >= 0 and arch["basis"] == "inferred"
    assert "partial evidence (isolated services" in arch["reason"]
    deps = found["estate-map"]["dependencies"]
    assert deps["details"] == ["web -> api: http, 2 link(s), exact_id, confidence 0.90"]


async def test_dangling_calls_are_named_capped_and_counted(tmp_path):
    found = await _estate(_ctx(_workspace(tmp_path, dangling=25)))
    dangling = found["estate-contracts"]["dangling-calls"]
    assert dangling["reason"] == (
        "25 consumer(s) matched no provider in another service (http 25): "
        "no provider in the workspace 25"
    )
    assert dangling["details"][0] == (
        "web client.py:12 -> billing: http::GET::/missing/0 (no provider in the workspace)"
    )
    assert dangling["details"][1].startswith("web client.py -> target not resolved: http::GET::")
    assert "and 5 more" in dangling["details"]
    assert dangling["details"][-1] == "1 call(s) to public hosts outside the workspace not counted"
    assert len(dangling["data"]["dangling"]) == 25  # the JSON keeps every row
    assert all("self" not in r["contract_id"] for r in dangling["data"]["dangling"])
    contracts = found["estate-contracts"]["contracts"]
    assert "1 cross-repo link(s) (exact 1)" in contracts["reason"]
    assert "web -> api: 1 http link(s)" in contracts["details"]
    calls = found["estate-contracts"]["http-calls"]
    assert calls["reason"] == (
        "4 HTTP call(s) read as contracts, 0 located but not resolvable, "
        "1 file(s) calling out through code no extractor reads"
    )


def test_dangling_calls_read_the_in_service_split_either_way():
    from repowise.core.report.sections.estate_contracts import dangling_calls

    graph = SystemGraph(
        diagnostics=ExtractionDiagnostics(
            unmatched_consumers=[
                _unmatched("a", "grpc::X/*"),
                _unmatched("a", "grpc::X/*"),
                _unmatched("a", "grpc::Y/*", "internal_only"),
                _unmatched("a", "http::GET::/v1/charges", "external_host"),
            ]
        )
    )
    rows = dangling_calls(graph, None)  # no contract store: the diagnostics alone decide
    assert [(r["contract_id"], r["target"], r["external"]) for r in rows] == [
        ("grpc::X/*", None, False),
        ("http::GET::/v1/charges", None, True),
    ]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def test_cli_estate_outside_a_workspace_says_so(tmp_path: Path) -> None:
    from repowise.cli.main import cli

    result = CliRunner().invoke(cli, ["report", "estate", str(tmp_path)])
    assert result.exit_code == 1 and "covers a workspace" in result.output


def test_cli_estate_reads_the_stored_artifacts(tmp_path: Path, monkeypatch) -> None:
    from repowise.cli.main import cli

    monkeypatch.delenv("REPOWISE_DB_URL", raising=False)
    ws = _workspace((tmp_path / "ws").resolve())
    out = tmp_path / "estate.json"
    result = CliRunner().invoke(cli, ["report", "estate", str(ws), "--format", "json", "--out", str(out)])
    assert result.exit_code == 0, result.output
    doc = json.loads(out.read_text(encoding="utf-8"))
    assert doc["kind"] == "estate" and doc["scope"] == {"kind": "workspace", "name": "ws"}
    assert [s["id"] for s in doc["sections"]] == [
        "estate-summary",
        "estate-map",
        "estate-contracts",
        "estate-health",
        "estate-seams",
        "estate-start",
    ]
    assert all(s["repo"] is None for s in doc["sections"])
    assert [r.get("indexed", True) for r in doc["repos"]] == [False] * 4  # no member is indexed
    md = CliRunner().invoke(cli, ["report", "estate", str(ws), "--sections", "estate-map"])
    assert md.exit_code == 0 and "# Estate: ws" in md.output and "## Estate summary" not in md.output
