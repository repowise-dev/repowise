"""/api/repos/{repo_id}/overview-summary — one-call repo overview payload.

Replaces the dashboard's N-call waterfall with a single lightweight
aggregate: repo meta, stat strip (+ deltas vs the previous health
snapshot), server-built attention items, language distribution (from
graph_nodes, NOT a full graph export), top-hotspots slice, recent
decisions slice, savings headline, and health KPIs.
"""

from __future__ import annotations

import configparser
import contextlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.health.scoring import hotspot_health
from repowise.core.persistence import crud
from repowise.core.persistence.models import (
    DeadCodeFinding,
    GenerationJob,
    GitMetadata,
    GraphNode,
    Page,
)
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers.git import _hotspot_from_row
from repowise.server.services.knowledge_map import compute_knowledge_map
from repowise.server.services.module_health import top_level_module

router = APIRouter(
    prefix="/api/repos",
    tags=["overview"],
    dependencies=[Depends(verify_api_key)],
)

#: How many snapshots the health card's trend line plots. Also the width of the
#: scalar window this route reads, so the read and the chart cannot drift.
HEALTH_HISTORY_POINTS = 12


def _index_storage_bytes(repowise_dir: Path) -> int:
    """Total on-disk size of a repo's ``.repowise/`` directory."""
    if not repowise_dir.is_dir():
        return 0
    total = 0
    for path in repowise_dir.rglob("*"):
        if path.is_file():
            with contextlib.suppress(OSError):
                total += path.stat().st_size
    return total


def _remote_url(stored_url: str | None, local_path: str | None) -> str | None:
    """Best-effort git remote for a repo.

    ``repositories.url`` is client-supplied and empty for most CLI-registered
    repos, so fall back to reading ``origin`` out of ``.git/config``. Parsed
    rather than shelled out to: this runs on a page load, and ``git remote
    get-url`` would cost a process spawn per request for a string sitting in a
    file we can read.

    Used only to resolve a repo avatar, so every failure path returns ``None``
    and the UI falls back to initials.
    """
    if stored_url:
        return stored_url
    if not local_path:
        return None

    git_path = Path(local_path) / ".git"
    # Worktrees and submodules use a `.git` FILE holding `gitdir: <path>`; the
    # config lives in the main checkout, so follow the pointer before reading.
    if git_path.is_file():
        try:
            pointer = git_path.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if not pointer.startswith("gitdir:"):
            return None
        resolved = Path(pointer.split(":", 1)[1].strip())
        if not resolved.is_absolute():
            resolved = (Path(local_path) / resolved).resolve()
        # A worktree's gitdir is `<main>/.git/worktrees/<name>`; config is two
        # levels up. Fall back to the pointed-at dir for the submodule case.
        git_path = resolved.parent.parent if resolved.parent.name == "worktrees" else resolved

    config_path = git_path / "config"
    if not config_path.is_file():
        return None

    # Both flags are load-bearing, not defensive boilerplate:
    #
    # strict=False — git tolerates duplicate keys and writes them routinely. A
    # remote with two `fetch` refspecs is normal, and VS Code writes
    # `vscode-merge-base` twice under a branch section. Strict parsing raises
    # DuplicateOptionError on both, which would mean this project's own
    # checkout never resolves a remote.
    #
    # interpolation=None — ConfigParser expands `%` at get() time, so a
    # perfectly valid remote like `https://user%40company.com@dev.azure.com/...`
    # raises InterpolationSyntaxError. Left on, that exception escapes the
    # endpoint and turns the whole Overview into a 404 in order to render an
    # avatar.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(config_path, encoding="utf-8")
        for section in ('remote "origin"', 'remote "upstream"'):
            if parser.has_option(section, "url"):
                return parser.get(section, "url").strip() or None
    except (OSError, configparser.Error):
        return None
    return None


def _decision_slim(d: Any) -> dict:
    return {
        "id": d.id,
        "title": d.title,
        "status": d.status,
        "source": d.source,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "staleness_score": round(float(d.staleness_score or 0.0), 3),
    }


async def _savings_headline(repo_local_path: str | None) -> dict:
    """Distill + MCP savings totals from the omission-store sidecar.

    Headline numbers only — no per-day rollups, no transcript scan (the
    missed-savings scan reads agent transcripts and is too slow for an
    overview payload). Mirrors /distill-savings semantics otherwise.
    """
    if not repo_local_path:
        return {"available": False}
    db_path = Path(repo_local_path) / ".repowise" / "omissions" / "omissions.db"
    if not db_path.is_file():
        return {"available": False}

    from repowise.core.distill import tracking
    from repowise.core.distill.session_model import resolve_session_model
    from repowise.core.generation.cost_tracker import get_model_pricing

    try:
        conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=1)
    except sqlite3.Error:
        return {"available": False}
    try:
        summary = tracking.distill_summary(conn, since=None)
        mcp = tracking.mcp_savings_summary(conn, since=None)
    except sqlite3.Error:
        return {"available": False}
    finally:
        conn.close()

    resolved = resolve_session_model(Path(repo_local_path))
    rate = get_model_pricing(resolved.model)["input"]
    total_saved = summary["saved_tokens"] + mcp["tokens"]
    return {
        "available": True,
        "saved_tokens": summary["saved_tokens"],
        "mcp_tokens": mcp["tokens"],
        "total_saved_tokens": total_saved,
        "estimated_usd_saved": total_saved * rate / 1_000_000,
        "pricing_model": resolved.model,
    }


def _build_attention_items(
    decision_health: dict,
    knowledge_silos: list[dict],
    dead_safe: list[Any],
) -> list[dict]:
    """Flat, severity-tagged attention list — the server-side twin of the
    AttentionPanel item builder that used to live in the overview page."""
    items: list[dict] = []
    for d in decision_health.get("stale_decisions", []):
        items.append(
            {
                "id": f"stale-{d.id}",
                "type": "stale_decision",
                "title": d.title,
                "description": "Active decision drifting from the code it governs",
                "severity": "high",
                "target_id": d.id,
            }
        )
    for d in decision_health.get("proposed_awaiting_review", []):
        items.append(
            {
                "id": f"proposed-{d.id}",
                "type": "proposed_decision",
                "title": d.title,
                "description": "Auto-proposed decision awaiting review",
                "severity": "medium",
                "target_id": d.id,
            }
        )
    for fp in decision_health.get("ungoverned_hotspots", [])[:10]:
        items.append(
            {
                "id": f"ungoverned-{fp}",
                "type": "ungoverned_hotspot",
                "title": fp,
                "description": "High-churn file with no governing decision",
                "severity": "medium",
                "target_id": fp,
            }
        )
    for s in knowledge_silos[:10]:
        items.append(
            {
                "id": f"silo-{s['file_path']}",
                "type": "knowledge_silo",
                "title": s["file_path"],
                "description": (f"{round(s['owner_pct'] * 100)}% single-owner concentration"),
                "severity": "medium",
                "target_id": s["file_path"],
            }
        )
    for f in dead_safe[:10]:
        label = f.symbol_name or f.file_path
        items.append(
            {
                "id": f"dead-{f.id}",
                "type": "dead_code",
                "title": label,
                "description": f"Safe to delete ({f.lines} lines)",
                "severity": "low",
                "target_id": f.file_path,
            }
        )
    severity_rank = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: severity_rank.get(i["severity"], 3))
    return items


@router.get("/{repo_id}/overview-summary")
async def overview_summary(
    repo_id: str,
    session: AsyncSession = Depends(get_db_session),
) -> dict:
    """Everything the Overview page needs above the fold, in one call."""
    repo = await crud.get_repository(session, repo_id)
    if repo is None:
        raise HTTPException(status_code=404, detail="Repository not found")

    # --- Stat strip ------------------------------------------------------
    file_count = (
        await session.scalar(
            select(func.count(GraphNode.id)).where(
                GraphNode.repository_id == repo_id, GraphNode.node_type == "file"
            )
        )
        or 0
    )
    symbol_count = int(
        await session.scalar(
            select(func.sum(GraphNode.symbol_count)).where(GraphNode.repository_id == repo_id)
        )
        or 0
    )
    entry_point_count = (
        await session.scalar(
            select(func.count(GraphNode.id)).where(
                GraphNode.repository_id == repo_id,
                GraphNode.is_entry_point.is_(True),
            )
        )
        or 0
    )
    avg_confidence = float(
        await session.scalar(select(func.avg(Page.confidence)).where(Page.repository_id == repo_id))
        or 0.0
    )
    doc_coverage_pct = avg_confidence * 100
    total_pages = (
        await session.scalar(select(func.count(Page.id)).where(Page.repository_id == repo_id)) or 0
    )
    # Pages a model actually wrote, as opposed to the ones assembled from the
    # index alone. The discriminator is the provider: "template" is the stub
    # writer, so anything else means a real generation call produced the prose.
    #
    # Deliberately NOT additionally scoped to MODEL_WRITTEN_PAGE_TYPES. That
    # narrower query looks more principled and is a lie in practice: file and
    # symbol pages do get generated with a real provider on some runs, so
    # scoping by type puts model-written pages in the "assembled from the index"
    # bucket. Asking the question the honest way — did a model write this page —
    # needs no page-type carve-out, and the answer stays true whatever the
    # generator does next.
    #
    # Note a stubbed page (a provider call that failed and fell back) counts as
    # not-prose, which is correct: it has no prose.
    prose_pages = (
        await session.scalar(
            select(func.count(Page.id)).where(
                Page.repository_id == repo_id,
                Page.provider_name.is_not(None),
                Page.provider_name != "template",
            )
        )
        or 0
    )
    fresh_pages = (
        await session.scalar(
            select(func.count(Page.id)).where(
                Page.repository_id == repo_id, Page.freshness_status == "fresh"
            )
        )
        or 0
    )
    freshness_score = (fresh_pages / total_pages * 100) if total_pages > 0 else doc_coverage_pct
    dead_export_count = (
        await session.scalar(
            select(func.count(DeadCodeFinding.id)).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.kind == "unused_export",
                DeadCodeFinding.status == "open",
            )
        )
        or 0
    )
    hotspot_count = (
        await session.scalar(
            select(func.count(GitMetadata.id)).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.is_hotspot.is_(True),
            )
        )
        or 0
    )

    # Module-level silo counts (top-level-directory grouping — mirrors the
    # /ownership?granularity=module aggregation the page used to fetch).
    owner_rows = await session.execute(
        select(GitMetadata.file_path, GitMetadata.primary_owner_name).where(
            GitMetadata.repository_id == repo_id
        )
    )
    module_owner_files: dict[str, dict[str, int]] = {}
    module_file_totals: dict[str, int] = {}
    for fp, owner in owner_rows:
        module = top_level_module(fp)
        module_file_totals[module] = module_file_totals.get(module, 0) + 1
        if owner:
            bucket = module_owner_files.setdefault(module, {})
            bucket[owner] = bucket.get(owner, 0) + 1
    module_count = len(module_file_totals)
    silo_count = 0
    for module, owners in module_owner_files.items():
        top = max(owners.values(), default=0)
        if module_file_totals.get(module) and top / module_file_totals[module] > 0.8:
            silo_count += 1

    # --- Language distribution (server-side; replaces the full graph export)
    lang_rows = await session.execute(
        select(GraphNode.language, func.count(GraphNode.id))
        .where(GraphNode.repository_id == repo_id, GraphNode.node_type == "file")
        .group_by(GraphNode.language)
    )
    languages = sorted(
        ({"language": lang or "other", "file_count": n} for lang, n in lang_rows),
        key=lambda r: -r["file_count"],
    )

    # --- Health KPIs + deltas vs previous snapshot ------------------------
    # Loaded once and handed to both consumers below: the KPI rollup and the
    # defect-accuracy stat both want every per-file row, and letting each fetch
    # its own pulled the whole table twice per page load.
    health_metrics = await crud.get_health_metrics(session, repo_id)
    findings = await crud.get_health_findings(session, repo_id)
    health_summary = await crud.get_health_summary(
        session, repo_id, metrics=health_metrics, findings=findings
    )
    # Three scopes, three reads. This payload wants the *count* of retained
    # snapshots, the newest ``HEALTH_HISTORY_POINTS`` rows' scalars for the
    # sparkline, and the newest two rows' per-file maps for the file-count
    # delta — and nothing else. Loading the history as entities to get that
    # pulled every row's ``per_file_scores_json``: 2.8 MB on this index, ~9 MB
    # at the retention cap, for 375 KB of actual use. Capping the read at two
    # rows instead would be wrong in two visible ways — it would flatten the
    # sparkline and report a snapshot count of 2.
    snapshot = await crud.get_health_snapshot_headline(
        session, repo_id, recent=HEALTH_HISTORY_POINTS
    )
    # The headline is the *current* number, so it comes from the metrics loaded
    # above rather than off the newest snapshot: ``repowise update`` re-scores
    # health without writing a snapshot, so the stored value can lag the rows
    # every other figure on this page is built from. Costs no extra query.
    #
    # ``history`` and ``deltas`` below stay snapshot-derived on purpose. They
    # are a series of recorded runs, and there is no live value for "the run
    # before this one", so the delta means "since the last recorded snapshot".
    hotspot_paths = await crud.get_hotspot_file_paths(session, repo_id)
    hotspot_health_value = hotspot_health(health_metrics, hotspot_paths)
    last_indexed_at: str | None = None
    deltas: dict[str, float | None] = {
        "average_health": None,
        "hotspot_health": None,
        "file_count": None,
    }
    if snapshot.snapshot_count:
        last_indexed_at = snapshot.taken_at.isoformat() if snapshot.taken_at else None
    if len(snapshot.recent) >= 2:
        prev, cur = snapshot.recent[-2], snapshot.recent[-1]
        deltas["average_health"] = round(cur.average_health - prev.average_health, 2)
        deltas["hotspot_health"] = round(cur.hotspot_health - prev.hotspot_health, 2)
        file_counts = await crud.get_health_snapshot_file_counts(session, repo_id, limit=2)
        if len(file_counts) == 2 and all(file_counts):
            deltas["file_count"] = file_counts[1] - file_counts[0]

    severity_breakdown = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for f in findings:
        s = (f.severity or "").lower()
        if s in severity_breakdown:
            severity_breakdown[s] += 1

    # "Can you trust this score?" — the backtested precision of the defect
    # ranking, shown on the health card. Sourced here rather than from the stats
    # payload: this is a health number, and having the overview reach across
    # into another page's payload for it is what broke when that payload changed.
    #
    # ``prior_defect`` rows only, each carrying its parsed ``details``. Two
    # problems in one shape: the stat reads no other biomarker, so converting
    # the other ~90% of findings was waste; and it reads the fix count and the
    # window *out of* ``details``, which this call site never supplied — so
    # every flagged file reported ``recent_fixes: 1`` (333 of 999 have more, up
    # to 19) and ``window_days`` echoed the default instead of the indexed
    # value. The health dashboard computes the same stat from the real numbers,
    # so the two surfaces disagreed on one figure.
    # Parsed per row: one malformed blob must not cost the whole panel.
    prior_defect_rows: list[dict] = []
    for f in findings:
        if f.biomarker_type != "prior_defect":
            continue
        details: Any = {}
        with contextlib.suppress(Exception):
            details = json.loads(f.details_json) if f.details_json else {}
        prior_defect_rows.append(
            {
                "file_path": f.file_path,
                "biomarker_type": f.biomarker_type,
                "details": details,
            }
        )

    defect_accuracy = None
    try:
        from repowise.core.analysis.health.defect_accuracy import compute_defect_accuracy

        defect_accuracy = compute_defect_accuracy(
            [
                {
                    "file_path": m.file_path,
                    "score": m.score,
                    "nloc": m.nloc,
                    "has_test_file": m.has_test_file,
                    "module": m.module,
                }
                for m in health_metrics
            ],
            prior_defect_rows,
        )
    except Exception:
        # Best-effort: the card omits the panel rather than failing the page.
        defect_accuracy = None

    # --- Attention items + onboarding targets -----------------------------
    decision_health = await crud.get_decision_health_summary(session, repo_id)
    knowledge = await compute_knowledge_map(session, repo_id)
    dead_safe = [
        f
        for f in await crud.get_dead_code_findings(session, repo_id, status="open")
        if f.safe_to_delete
    ]
    attention = _build_attention_items(
        decision_health, knowledge.get("knowledge_silos", []), dead_safe
    )

    # --- Top hotspots + recent decisions slices ---------------------------
    hotspot_rows = (
        (
            await session.execute(
                select(GitMetadata)
                .where(GitMetadata.repository_id == repo_id, GitMetadata.is_hotspot.is_(True))
                .order_by(
                    GitMetadata.temporal_hotspot_score.desc().nulls_last(),
                    GitMetadata.churn_percentile.desc(),
                )
                .limit(8)
            )
        )
        .scalars()
        .all()
    )
    top_hotspots = [_hotspot_from_row(r).model_dump(mode="json") for r in hotspot_rows]

    decisions = await crud.list_decisions(session, repo_id, limit=8)
    recent_decisions = [_decision_slim(d) for d in decisions]

    # --- Sync status (last completed jobs + any active one) ---------------
    job_rows = (
        (
            await session.execute(
                select(GenerationJob)
                .where(GenerationJob.repository_id == repo_id)
                .order_by(GenerationJob.created_at.desc())
                .limit(30)
            )
        )
        .scalars()
        .all()
    )
    last_sync_at: str | None = None
    last_resync_at: str | None = None
    last_resync_dt = None
    last_sync_model: str | None = None
    active_job_id: str | None = None
    for j in job_rows:
        try:
            mode = json.loads(j.config_json or "{}").get("mode")
        except Exception:
            mode = None
        if j.status in ("pending", "running") and active_job_id is None:
            active_job_id = j.id
        if j.status == "completed" and j.finished_at:
            if mode == "full_resync" and last_resync_at is None:
                last_resync_at = j.finished_at.isoformat()
                last_resync_dt = j.finished_at
            elif mode != "full_resync" and last_sync_at is None:
                last_sync_at = j.finished_at.isoformat()
                last_sync_model = j.model_name or None

    # Fall back to the repository's own ``updated_at`` when no completed
    # server-side sync job exists. CLI / git-hook auto-syncs (``repowise
    # update``) refresh the index and bump ``repositories.updated_at`` via
    # ``upsert_repository``, but never create a GenerationJob row, so a
    # job-only derivation reports "never synced" even though the index is
    # current. Only adopt it when it is newer than the last full re-index so a
    # re-index is not relabelled as a sync.
    if (
        last_sync_at is None
        and repo.updated_at is not None
        and (last_resync_dt is None or repo.updated_at > last_resync_dt)
    ):
        last_sync_at = repo.updated_at.isoformat()

    savings = await _savings_headline(repo.local_path)
    repowise_dir = Path(repo.local_path) / ".repowise" if repo.local_path else Path(".repowise")

    # Serve the same read-time self-healed "indexed commit" as /api/repos and
    # /health/overview: prefer state.json's last_sync_commit over a possibly
    # stale DB row so the dashboard's indexed-commit display (and any freshness
    # signal derived from it) doesn't read "behind" after a no-op update left
    # the repositories row un-restamped.
    from repowise.server.mcp_server._meta import resolve_indexed_commit

    indexed_commit = resolve_indexed_commit(repo.head_commit, repo.local_path)

    return {
        "repo": {
            "id": repo.id,
            "name": repo.name,
            "local_path": repo.local_path,
            "default_branch": repo.default_branch,
            "head_commit": indexed_commit,
            "updated_at": repo.updated_at.isoformat() if repo.updated_at else None,
            "remote_url": _remote_url(repo.url, repo.local_path),
        },
        "stats": {
            "file_count": file_count,
            "symbol_count": symbol_count,
            "entry_point_count": entry_point_count,
            "doc_page_count": total_pages,
            "doc_prose_page_count": prose_pages,
            "doc_coverage_pct": doc_coverage_pct,
            "freshness_score": freshness_score,
            "dead_export_count": dead_export_count,
            "hotspot_count": hotspot_count,
            "silo_count": silo_count,
            "module_count": module_count,
            "deltas": deltas,
        },
        "health": {
            "average_health": health_summary.get("average_health"),
            "hotspot_health": hotspot_health_value,
            "worst_performer_path": health_summary.get("worst_performer_path"),
            "worst_performer_score": health_summary.get("worst_performer_score"),
            "open_findings": health_summary.get("open_findings", 0),
            # The two co-equal pillars surfaced alongside the defect headline.
            "maintainability_average": health_summary.get("maintainability_average"),
            "performance_average": health_summary.get("performance_average"),
            "performance_findings": health_summary.get("performance_findings", 0),
            "worst_performance_path": health_summary.get("worst_performance_path"),
            "worst_performance_score": health_summary.get("worst_performance_score"),
            "severity_breakdown": severity_breakdown,
            "defect_accuracy": defect_accuracy,
            "last_indexed_at": last_indexed_at,
            # The true total, not the length of the windowed read above.
            "snapshot_count": snapshot.snapshot_count,
            "history": [
                {
                    "taken_at": s.taken_at.isoformat() if s.taken_at else None,
                    "average_health": round(s.average_health, 2),
                    "hotspot_health": round(s.hotspot_health, 2),
                }
                for s in snapshot.recent
            ],
        },
        "languages": languages,
        "attention": attention,
        "onboarding_targets": knowledge.get("onboarding_targets", []),
        "top_hotspots": top_hotspots,
        "recent_decisions": recent_decisions,
        "savings": savings,
        "sync": {
            "last_sync_at": last_sync_at,
            "last_resync_at": last_resync_at,
            "last_sync_model": last_sync_model,
            "active_job_id": active_job_id,
            "page_count": total_pages,
            "index_storage_bytes": _index_storage_bytes(repowise_dir),
        },
    }
