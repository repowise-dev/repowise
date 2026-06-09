"""Module Health aggregator.

Per-module rollup combining ownership, churn, dead code, docs, and
decisions. No core ingestion changes — purely composes existing tables.

A "module" is the top-level directory of a file path (matches the existing
``OwnershipEntry`` convention). For nested module views we expose
``module_path`` as the path prefix the caller passed in, which can be
arbitrarily deep.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median

from sqlalchemy import func as sa_func
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    HealthFileMetric,
    WikiSymbol,
)


def module_of(file_path: str) -> str:
    parts = file_path.split("/", 1)
    return parts[0] if len(parts) > 1 else "root"


def _compute_health_score(
    *,
    is_silo: bool,
    hotspot_fraction: float,
    dead_pct: float,
    churn_pct: float,
    doc_pct: float,
    bus_factor_median: float,
) -> float:
    score = 100.0
    if is_silo:
        score -= 25
    score -= 20 * hotspot_fraction
    score -= 25 * dead_pct
    score -= 15 * (churn_pct / 100.0)
    score += 15 * doc_pct
    score += 10 if bus_factor_median >= 2 else -10
    return max(0.0, min(100.0, score))


def _under_module(file_path: str, module_path: str) -> bool:
    if module_path in ("", "root"):
        return "/" not in file_path
    return file_path == module_path or file_path.startswith(module_path + "/")


@dataclass
class _ModuleAccumulator:
    module_path: str
    files: list[GitMetadata] = field(default_factory=list)
    symbol_count: int = 0
    doc_covered_symbols: int = 0
    dead_code_count: int = 0
    dead_code_lines: int = 0
    owners: Counter = field(default_factory=Counter)
    owner_emails: dict[str, str | None] = field(default_factory=dict)
    contributors: set[str] = field(default_factory=set)
    decision_ids: list[str] = field(default_factory=list)
    top_hotspot_paths: list[tuple[float, str]] = field(default_factory=list)


def _score(acc: _ModuleAccumulator) -> dict:
    file_count = len(acc.files)
    hotspot_count = sum(1 for m in acc.files if m.is_hotspot)
    dead_pct = (acc.dead_code_count / file_count) if file_count else 0.0
    doc_pct = (
        acc.doc_covered_symbols / acc.symbol_count if acc.symbol_count else 0.0
    )

    bus_factors = [m.bus_factor or 0 for m in acc.files]
    med_bus = float(median(bus_factors)) if bus_factors else 0.0
    min_bus = min(bus_factors) if bus_factors else 0
    avg_churn = (
        sum(m.churn_percentile or 0.0 for m in acc.files) / file_count * 100.0
        if file_count
        else 0.0
    )

    primary_owner, primary_pct = None, 0.0
    if acc.owners:
        name, cnt = acc.owners.most_common(1)[0]
        primary_owner = name
        primary_pct = cnt / file_count if file_count else 0.0

    # Composite health (0-100). Higher is better. Weighted:
    #  -25 if siloed (>80% one owner)
    #  -20 * (hotspot_count / file_count)
    #  -25 * dead_pct
    #  -15 * (avg_churn / 100)
    #  +15 * doc_pct
    #  +10 if bus_factor >= 2 median, else -10
    score = _compute_health_score(
        is_silo=primary_pct > 0.8,
        hotspot_fraction=hotspot_count / file_count if file_count else 0.0,
        dead_pct=dead_pct,
        churn_pct=avg_churn,
        doc_pct=doc_pct,
        bus_factor_median=med_bus,
    )

    return {
        "file_count": file_count,
        "symbol_count": acc.symbol_count,
        "hotspot_count": hotspot_count,
        "dead_code_count": acc.dead_code_count,
        "dead_code_lines": acc.dead_code_lines,
        "avg_churn_percentile": avg_churn,
        "median_bus_factor": med_bus,
        "min_bus_factor": min_bus,
        "primary_owner": primary_owner,
        "primary_owner_pct": primary_pct,
        "is_silo": primary_pct > 0.8,
        "decision_count": len(acc.decision_ids),
        "doc_coverage_pct": doc_pct * 100.0,
        "health_score": score,
    }


async def aggregate_modules(
    session: AsyncSession, repo_id: str
) -> dict[str, _ModuleAccumulator]:
    """Single-pass aggregator. O(files + symbols + dead + decisions)."""

    files = (
        await session.execute(
            select(GitMetadata).where(GitMetadata.repository_id == repo_id)
        )
    ).scalars().all()

    accs: dict[str, _ModuleAccumulator] = defaultdict(
        lambda: _ModuleAccumulator(module_path="")
    )

    for m in files:
        mod = module_of(m.file_path)
        acc = accs[mod]
        if not acc.module_path:
            acc.module_path = mod
        acc.files.append(m)
        if m.primary_owner_name:
            acc.owners[m.primary_owner_name] += 1
            acc.owner_emails.setdefault(m.primary_owner_name, m.primary_owner_email)
        # Authors -> contributors set
        try:
            for a in json.loads(m.top_authors_json or "[]"):
                if a.get("name"):
                    acc.contributors.add(a["name"])
        except json.JSONDecodeError:
            pass
        if m.is_hotspot:
            acc.top_hotspot_paths.append(
                ((m.temporal_hotspot_score or 0.0), m.file_path)
            )

    # Symbols + docs by module.
    sym_rows = (
        await session.execute(
            select(
                WikiSymbol.file_path,
                WikiSymbol.docstring,
            ).where(WikiSymbol.repository_id == repo_id)
        )
    ).all()
    for path, doc in sym_rows:
        mod = module_of(path)
        acc = accs.get(mod)
        if acc is None:
            continue
        acc.symbol_count += 1
        if doc and str(doc).strip():
            acc.doc_covered_symbols += 1

    # Dead-code findings by module.
    dead_rows = (
        await session.execute(
            select(DeadCodeFinding.file_path, DeadCodeFinding.lines).where(
                DeadCodeFinding.repository_id == repo_id
            )
        )
    ).all()
    for path, lines in dead_rows:
        mod = module_of(path)
        acc = accs.get(mod)
        if acc is None:
            continue
        acc.dead_code_count += 1
        acc.dead_code_lines += int(lines or 0)

    # Decisions by affected module.
    decisions = (
        await session.execute(
            select(
                DecisionRecord.id, DecisionRecord.affected_modules_json
            ).where(DecisionRecord.repository_id == repo_id)
        )
    ).all()
    for did, modules_json in decisions:
        try:
            mods = json.loads(modules_json or "[]")
        except json.JSONDecodeError:
            mods = []
        for mod in mods:
            acc = accs.get(mod)
            if acc is not None:
                acc.decision_ids.append(did)

    return accs


def summarize(acc: _ModuleAccumulator) -> dict:
    """Flatten accumulator to summary fields (matches ModuleHealthSummary)."""

    out = _score(acc)
    out["module_path"] = acc.module_path
    return out


def detail_extras(acc: _ModuleAccumulator) -> dict:
    """Extras only present in ModuleHealthDetail."""

    acc.top_hotspot_paths.sort(reverse=True)
    return {
        "owners": [
            {
                "name": name,
                "email": acc.owner_emails.get(name),
                "file_count": cnt,
                "pct": cnt / len(acc.files) if acc.files else 0.0,
            }
            for name, cnt in acc.owners.most_common(20)
        ],
        "top_hotspots": [p for _, p in acc.top_hotspot_paths[:10]],
        "governing_decisions": acc.decision_ids,
        "contributor_count": len(acc.contributors),
    }


def read_repo_health_score(db_path: Path) -> float | None:
    """Return the canonical repo health score from a repo-local wiki.db."""
    if not db_path.exists():
        return None
    try:
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT score, COALESCE(nloc, 1) FROM health_file_metrics"
            ).fetchall()
    except sqlite3.OperationalError:
        return None
    except Exception:
        return None

    if not row:
        return None

    weights = [max(int(nloc or 0), 1) for _, nloc in row]
    total_weight = sum(weights)
    if total_weight:
        average = (
            sum(
                float(score) * weight
                for (score, _), weight in zip(row, weights, strict=True)
            )
            / total_weight
        )
    else:
        average = sum(float(score) for score, _ in row) / len(row)
    return max(0.0, min(100.0, round(average * 10.0, 1)))


async def build_single_file_health(
    session: AsyncSession, repo_id: str, file_path: str
) -> dict | None:
    """Build a ModuleHealthDetail-compatible dict for a single file.

    Returns None when the file has no git_metadata row (i.e. we know
    nothing about it).
    """

    # 1. git_metadata — required for ownership / churn / hotspot
    git_row = (
        await session.execute(
            select(GitMetadata).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path == file_path,
            )
        )
    ).scalar_one_or_none()
    if git_row is None:
        return None

    # 2. health_file_metrics — optional
    hfm = (
        await session.execute(
            select(HealthFileMetric.score).where(
                HealthFileMetric.repository_id == repo_id,
                HealthFileMetric.file_path == file_path,
            )
        )
    ).scalar_one_or_none()

    # 3. dead_code_findings for this file
    dead_rows = (
        await session.execute(
            select(
                sa_func.count(DeadCodeFinding.id),
                sa_func.coalesce(sa_func.sum(DeadCodeFinding.lines), 0),
            ).where(
                DeadCodeFinding.repository_id == repo_id,
                DeadCodeFinding.file_path == file_path,
            )
        )
    ).one()
    dead_code_count, dead_code_lines = int(dead_rows[0]), int(dead_rows[1])

    # 4. wiki_symbols for doc coverage
    sym_rows = (
        await session.execute(
            select(
                sa_func.count(WikiSymbol.id),
                sa_func.count(
                    sa_func.nullif(sa_func.trim(WikiSymbol.docstring), "")
                ),
            ).where(
                WikiSymbol.repository_id == repo_id,
                WikiSymbol.file_path == file_path,
            )
        )
    ).one()
    symbol_count, doc_covered = int(sym_rows[0]), int(sym_rows[1])

    # Derive values
    churn_pct = (git_row.churn_percentile or 0.0) * 100.0
    bus_factor = git_row.bus_factor or 0
    is_hotspot = bool(git_row.is_hotspot)
    primary_owner = git_row.primary_owner_name
    primary_owner_pct = git_row.primary_owner_commit_pct or 0.0
    is_silo = primary_owner_pct > 0.8
    doc_coverage_pct = (doc_covered / symbol_count * 100.0) if symbol_count else 0.0

    # Composite health score — use health_file_metrics score (0-10 -> 0-100)
    # when available, otherwise derive from the same formula as _score().
    if hfm is not None:
        health_score = max(0.0, min(100.0, float(hfm) * 10.0))
    else:
        health_score = _compute_health_score(
            is_silo=is_silo,
            hotspot_fraction=1.0 if is_hotspot else 0.0,
            dead_pct=1.0 if dead_code_count > 0 else 0.0,
            churn_pct=churn_pct,
            doc_pct=doc_coverage_pct / 100.0,
            bus_factor_median=float(bus_factor),
        )

    # Build owners list from top_authors_json
    owners_list: list[dict] = []
    if primary_owner:
        owners_list.append({
            "name": primary_owner,
            "email": git_row.primary_owner_email,
            "file_count": 1,
            "pct": primary_owner_pct,
        })
    try:
        for a in json.loads(git_row.top_authors_json or "[]"):
            name = a.get("name")
            if name and name != primary_owner:
                owners_list.append({
                    "name": name,
                    "email": a.get("email"),
                    "file_count": 1,
                    "pct": a.get("pct", 0.0),
                })
    except json.JSONDecodeError:
        pass

    # Contributors
    contributors: set[str] = set()
    try:
        for a in json.loads(git_row.top_authors_json or "[]"):
            if a.get("name"):
                contributors.add(a["name"])
    except json.JSONDecodeError:
        pass

    return {
        "module_path": file_path,
        "file_count": 1,
        "symbol_count": symbol_count,
        "hotspot_count": 1 if is_hotspot else 0,
        "dead_code_count": dead_code_count,
        "dead_code_lines": dead_code_lines,
        "avg_churn_percentile": churn_pct,
        "median_bus_factor": float(bus_factor),
        "min_bus_factor": bus_factor,
        "primary_owner": primary_owner,
        "primary_owner_pct": primary_owner_pct,
        "is_silo": is_silo,
        "decision_count": 0,
        "doc_coverage_pct": doc_coverage_pct,
        "health_score": health_score,
        # Detail extras
        "owners": owners_list,
        "top_hotspots": [file_path] if is_hotspot else [],
        "governing_decisions": [],
        "contributor_count": len(contributors),
    }


