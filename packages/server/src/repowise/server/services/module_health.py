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
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from statistics import median

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.persistence.models import (
    DeadCodeFinding,
    DecisionRecord,
    GitMetadata,
    GraphNode,
    WikiSymbol,
)


def _module_of(file_path: str) -> str:
    parts = file_path.split("/", 1)
    return parts[0] if len(parts) > 1 else "root"


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

    # Composite health (0–100). Higher is better. Weighted:
    #  -25 if siloed (>80% one owner)
    #  -20 * (hotspot_count / file_count)
    #  -25 * dead_pct
    #  -15 * (avg_churn / 100)
    #  +15 * doc_pct
    #  +10 if bus_factor >= 2 median, else -10
    score = 100.0
    if primary_pct > 0.8:
        score -= 25
    if file_count:
        score -= 20 * (hotspot_count / file_count)
    score -= 25 * dead_pct
    score -= 15 * (avg_churn / 100.0)
    score += 15 * doc_pct
    score += 10 if med_bus >= 2 else -10
    score = max(0.0, min(100.0, score))

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
        mod = _module_of(m.file_path)
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
        mod = _module_of(path)
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
        mod = _module_of(path)
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


# Avoid unused-import warning — GraphNode is reserved for future fan-in.
_ = GraphNode
