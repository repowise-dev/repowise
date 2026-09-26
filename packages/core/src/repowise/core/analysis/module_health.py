"""Per-module rollup of ownership, churn, dead code, docs and decisions.

Sync, no I/O, no clock. Rows may be dicts, dataclasses or ORM rows (read
through :func:`.health.rows.field`); JSON columns may be text or already
decoded.

A module is the top-level directory of a file path (:func:`module_label`), the
bucket key every ownership surface uses. It is not ``HealthFileMetric.module``,
which names the enclosing package boundary.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from statistics import median
from typing import Any

from repowise.core.analysis.health.aggregation import module_label
from repowise.core.analysis.health.rows import field as row_field
from repowise.core.analysis.health.rows import json_field


def module_health_score(
    *,
    is_silo: bool,
    hotspot_fraction: float,
    dead_pct: float,
    churn_pct: float,
    doc_pct: float,
    bus_factor_median: float,
) -> float:
    """Composite health, 0-100, higher is better."""
    score = 100.0
    if is_silo:
        score -= 25
    score -= 20 * hotspot_fraction
    score -= 25 * dead_pct
    score -= 15 * (churn_pct / 100.0)
    score += 15 * doc_pct
    score += 10 if bus_factor_median >= 2 else -10
    return max(0.0, min(100.0, score))


@dataclass
class ModuleAccumulator:
    module_path: str
    files: list[Any] = field(default_factory=list)
    symbol_count: int = 0
    doc_covered_symbols: int = 0
    dead_code_count: int = 0
    dead_code_lines: int = 0
    owners: Counter = field(default_factory=Counter)
    owner_emails: dict[str, str | None] = field(default_factory=dict)
    contributors: set[str] = field(default_factory=set)
    decision_ids: list[str] = field(default_factory=list)
    top_hotspot_paths: list[tuple[float, str]] = field(default_factory=list)


def _score(acc: ModuleAccumulator) -> dict:
    file_count = len(acc.files)
    hotspot_count = sum(1 for m in acc.files if row_field(m, "is_hotspot"))
    dead_pct = (acc.dead_code_count / file_count) if file_count else 0.0
    doc_pct = acc.doc_covered_symbols / acc.symbol_count if acc.symbol_count else 0.0

    bus_factors = [row_field(m, "bus_factor") or 0 for m in acc.files]
    med_bus = float(median(bus_factors)) if bus_factors else 0.0
    min_bus = min(bus_factors) if bus_factors else 0
    avg_churn = (
        sum(row_field(m, "churn_percentile") or 0.0 for m in acc.files) / file_count * 100.0
        if file_count
        else 0.0
    )

    primary_owner, primary_pct = None, 0.0
    if acc.owners:
        name, cnt = acc.owners.most_common(1)[0]
        primary_owner = name
        primary_pct = cnt / file_count if file_count else 0.0

    score = module_health_score(
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


def aggregate_modules(
    git_rows: Iterable[Any],
    symbol_rows: Iterable[Any],
    dead_rows: Iterable[Any],
    decision_rows: Iterable[Any],
) -> dict[str, ModuleAccumulator]:
    """Single pass over each input. O(files + symbols + dead + decisions).

    ``git_rows`` are full ``git_metadata`` rows and define the modules: symbols,
    dead code (``file_path``, ``lines``) and decisions (``id``,
    ``affected_modules_json``) that land outside them are dropped.
    """
    accs: dict[str, ModuleAccumulator] = defaultdict(lambda: ModuleAccumulator(module_path=""))

    for m in git_rows:
        file_path = row_field(m, "file_path")
        mod = module_label(file_path)
        acc = accs[mod]
        if not acc.module_path:
            acc.module_path = mod
        acc.files.append(m)
        owner = row_field(m, "primary_owner_name")
        if owner:
            acc.owners[owner] += 1
            acc.owner_emails.setdefault(owner, row_field(m, "primary_owner_email"))
        for a in json_field(m, "top_authors_json", []):
            if a.get("name"):
                acc.contributors.add(a["name"])
        if row_field(m, "is_hotspot"):
            acc.top_hotspot_paths.append(
                ((row_field(m, "temporal_hotspot_score") or 0.0), file_path)
            )

    for s in symbol_rows:
        acc = accs.get(module_label(row_field(s, "file_path")))
        if acc is None:
            continue
        acc.symbol_count += 1
        doc = row_field(s, "docstring")
        if doc and str(doc).strip():
            acc.doc_covered_symbols += 1

    for d in dead_rows:
        acc = accs.get(module_label(row_field(d, "file_path")))
        if acc is None:
            continue
        acc.dead_code_count += 1
        acc.dead_code_lines += int(row_field(d, "lines") or 0)

    for dec in decision_rows:
        for mod in json_field(dec, "affected_modules_json", []):
            acc = accs.get(mod)
            if acc is not None:
                acc.decision_ids.append(row_field(dec, "id"))

    return accs


def summarize(acc: ModuleAccumulator) -> dict:
    """Flatten accumulator to summary fields (matches ModuleHealthSummary)."""

    out = _score(acc)
    out["module_path"] = acc.module_path
    return out


def detail_extras(acc: ModuleAccumulator) -> dict:
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


__all__ = [
    "ModuleAccumulator",
    "aggregate_modules",
    "detail_extras",
    "module_health_score",
    "summarize",
]
