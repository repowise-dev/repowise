"""`report estate` roll-ups: health, churn hotspots and bug magnets per repo, the
riskiest seams and where to start, read from each member's index and the workspace store."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from repowise.core.persistence.models import (
    GitCommit,
    GitMetadata,
    GraphNode,
    HealthFileMetric,
    HealthSnapshot,
    Page,
    Repository,
)
from repowise.core.report.build import available_sections, collect_workspace
from repowise.core.report.model import WorkspaceReportContext
from repowise.core.workspace.config import WorkspaceConfig
from repowise.core.workspace.registry import open_repo_db
from repowise.core.workspace.store import refresh_workspace_store
from repowise.core.workspace.system_graph import (
    SystemEdge,
    SystemGraph,
    SystemNode,
    save_system_graph,
)

_DAY = datetime(2026, 10, 1, tzinfo=UTC)


async def _index(repo: Path, alias: str, *, commits: int, health: float | None, files: dict) -> None:
    """A real ``wiki.db``: *commits* commits, and per file ``(pagerank, hotspot, magnet,
    fixes, origin, written_words)``."""
    (repo / ".repowise").mkdir(parents=True, exist_ok=True)
    engine, factory = await open_repo_db(repo)
    rid = f"repo-{alias}"
    async with factory() as session:
        session.add(Repository(id=rid, name=alias, url="", local_path=str(repo)))
        await session.flush()
        for i in range(commits):
            session.add(GitCommit(repository_id=rid, sha=f"{i:040d}", committed_at=_DAY))
        for path, (rank, hot, magnet, fixes, origin, words) in files.items():
            session.add(GraphNode(repository_id=rid, node_id=path, node_type="file", pagerank=rank))
            session.add(
                GitMetadata(
                    repository_id=rid,
                    file_path=path,
                    is_hotspot=hot,
                    temporal_hotspot_score=rank,
                    bug_magnet=magnet,
                    prior_defect_count=fixes,
                    fix_mass=float(fixes),
                    last_fix_at=_DAY if fixes else None,
                    history_only=origin == "config",
                )
            )
            session.add(
                HealthFileMetric(repository_id=rid, file_path=path, code_origin=origin, score=8.0)
            )
            if words is not None:
                session.add(
                    Page(
                        id=f"file_page:{path}",
                        repository_id=rid,
                        page_type="file_page",
                        title=path,
                        content=" ".join(["word"] * words),
                        target_path=path,
                        source_hash="h",
                        model_name="m" if words else "",
                        provider_name="anthropic" if words else "template",
                        generation_level=2,
                        created_at=_DAY,
                        updated_at=_DAY,
                    )
                )
        # a node that is not a file a reader opens
        session.add(GraphNode(repository_id=rid, node_id="external:os", node_type="file", pagerank=0.9))
        if health is not None:
            session.add(HealthSnapshot(repository_id=rid, average_health=health))
        await session.commit()
    await engine.dispose()


def _node(repo: str) -> SystemNode:
    return SystemNode(id=repo, repo=repo, service_path=None, name=repo)


def _edge(src: str, dst: str, kind: str = "http", confidence: float = 0.9) -> SystemEdge:
    return SystemEdge(
        id=f"{src}->{dst}:{kind}",
        source=src,
        target=dst,
        kind=kind,
        match_type="exact",
        confidence=confidence,
        weight=1,
        structural=kind != "co_change",
        strategy="exact_id",
    )


async def _estate(tmp_path: Path) -> dict[str, dict]:
    """web -> api -> lib (package, co-changing); old has one commit; docs is not indexed."""
    ws = tmp_path / "ws"
    await _index(
        ws / "web",
        "web",
        commits=30,
        health=6.5,
        files={
            "app.py": (0.5, True, True, 4, "production", None),
            "util.py": (0.2, True, False, 1, "production", 120),
            "vendor.js": (0.9, False, False, 0, "vendored", None),
            "Dockerfile": (0.8, False, False, 0, "config", None),
        },
    )
    await _index(
        ws / "api", "api", commits=12, health=9.1, files={"main.go": (0.4, False, False, 0, "production", 0)}
    )
    await _index(
        ws / "lib", "lib", commits=8, health=None, files={"lib.cs": (0.3, False, False, 0, "production", None)}
    )
    await _index(
        ws / "old", "old", commits=1, health=8.0, files={"old.py": (0.3, False, False, 0, "production", None)}
    )
    (ws / "docs").mkdir()
    (ws / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos:\n"
        + "".join(f"  - path: {r}\n    alias: {r}\n" for r in ("web", "api", "lib", "old", "docs")),
        encoding="utf-8",
    )
    save_system_graph(
        SystemGraph(
            generated_at="2026-10-01T00:00:00+00:00",
            nodes=[_node(r) for r in ("web", "api", "lib", "old")],
            edges=[
                _edge("web", "api"),
                _edge("api", "lib", "package", 1.0),
                _edge("api", "lib", "co_change", 0.5),
                _edge("old", "api", "grpc", 0.75),
            ],
        ),
        ws,
    )
    await refresh_workspace_store(WorkspaceConfig.load(ws), ws)
    ctx = WorkspaceReportContext(
        ws,
        repos=[
            {"alias": r, "head_commit": "a" * 40} for r in ("web", "api", "lib", "old")
        ]
        + [{"alias": "docs", "name": "docs", "indexed": False}],
    )
    wanted = [s for s in available_sections("estate") if s[0] in ("estate-health", "estate-seams", "estate-start")]
    sections = await collect_workspace(ctx, wanted)
    return {s["id"]: {c["name"]: c for c in s["checks"]} for s in sections}


async def test_estate_rollups_read_each_index_and_the_store(tmp_path):
    found = await _estate(tmp_path)

    health = found["estate-health"]["health"]
    assert health["reason"].startswith("3 of 5 repo(s) carry a health snapshot")
    assert health["details"][0].startswith("web: 6.5 average file health")
    assert "indexed with no health snapshot: lib" in health["details"]
    assert "not indexed: docs" in health["details"]

    churn = found["estate-health"]["churn-hotspots"]
    assert churn["reason"].startswith("2 churn hotspot file(s) across 1 of 3 repo(s)")
    assert "not a defect" in churn["reason"]
    assert churn["details"][0] == "web: 2 of 3 code file(s), 30 commits indexed: app.py, util.py"
    assert "too little history to measure (commits indexed): old (1)" in churn["details"]
    assert any(d.startswith("history read, no churn hotspot: api (newest commit") for d in churn["details"])

    magnets = found["estate-health"]["bug-magnets"]
    assert magnets["reason"].startswith("1 bug magnet file(s) across 1 of 3 repo(s)")
    assert magnets["details"][0] == "web app.py: 4 bug-fix commit(s) in the window, last 2026-10-01"

    seams = found["estate-seams"]["seams"]
    assert seams["basis"] == "inferred" and "heuristic" in seams["method"]
    assert seams["reason"].startswith(
        "3 service pair(s) joined by an edge, 1 with no contract between them, of which 1 "
        "change together (co-change measured for 1)"
    )
    assert seams["details"][0].startswith(
        "api -> lib: seam score 4.5, package, confidence 1.00, reach 3 service(s), "
        "co-change 0.50 with no contract"
    )
    # old -> api is a grpc contract: its short history does not touch its score
    assert not any("not measured" in d for d in seams["details"])

    services = found["estate-start"]["start-services"]
    assert services["details"][0] == "lib (service): a change reaches 3 service(s)"
    files = found["estate-start"]["start-files"]
    # production code only, undocumented first; a template page counts as none
    assert files["details"][0] == "lib: lib.cs (undocumented)"
    assert "web: app.py (undocumented), util.py (120 doc words)" in files["details"]
    assert "api: main.go (undocumented)" in files["details"]
    assert all("external:" not in d and "vendor.js" not in d and "Dockerfile" not in d for d in files["details"])


async def test_without_history_or_store_each_input_says_so(tmp_path):
    ws = tmp_path / "ws"
    await _index(ws / "one", "one", commits=1, health=None, files={"a.py": (0.1, False, False, 0, "production", None)})
    (ws / ".repowise-workspace.yaml").write_text(
        "version: 1\nrepos:\n  - path: one\n    alias: one\n", encoding="utf-8"
    )
    ctx = WorkspaceReportContext(ws, repos=[{"alias": "one", "head_commit": "a" * 40}])
    (section,) = await collect_workspace(
        ctx, [s for s in available_sections("estate") if s[0] == "estate-health"]
    )
    checks = {c["name"]: c for c in section["checks"]}
    assert checks["health"]["basis"] == "unresolved"
    assert "workspace store is missing" in checks["health"]["reason"]
    assert checks["churn-hotspots"]["score"] == -1
    assert checks["churn-hotspots"]["reason"] == "no repo holds enough indexed history to measure churn"


async def test_start_files_rank_on_exact_words_not_page_length(tmp_path):
    from repowise.core.report.sections.estate_start import _targets

    repo = tmp_path / "web"
    files = {"long.py": (0.5, False, False, 0, "production", 1), "short.py": (0.4, False, False, 0, "production", 1)}
    await _index(repo, "web", commits=2, health=None, files=files)
    engine, factory = await open_repo_db(repo)
    async with factory() as session:
        # long.py: more characters, fewer words; short.py: fewer characters, more words
        (await session.get(Page, "file_page:long.py")).content = "x" * 40
        (await session.get(Page, "file_page:short.py")).content = "a b c d e"
        await session.commit()
        rows = await _targets(session, "repo-web")
    await engine.dispose()
    assert [(r["path"], r["doc_words"]) for r in rows] == [("long.py", 1), ("short.py", 5)]
