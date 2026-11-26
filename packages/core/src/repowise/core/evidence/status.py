"""What runtime evidence is stored, where each run's time went, and how fresh it is."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime
from typing import Any

from repowise.core.analysis.health.coverage.freshness import coverage_freshness


def run_share(total: float | None, value: float | None) -> float | None:
    """*value*'s share of a run's *total*, rounded; ``None`` when either is unknown."""
    if total is None or value is None or total <= 0:
        return None
    return round(min(value / total, 1.0), 4)


def time_split(run: Any) -> dict[str, float | None]:
    """Where a stored profile run's time went: production repo code itself (waits
    and libraries excluded), waits, and libraries. ``None`` for a run stored
    before the split was recorded, or one whose file did not carry it."""
    total = run.total_value
    return {
        "repo_self_share": run_share(total, run.repo_self_value),
        "wait_share": run_share(total, run.wait_value),
        "library_share": run_share(total, run.library_value),
    }


def unattributed_share(run: Any) -> float | None:
    """The share of a stored profile run's time in none of :func:`time_split`'s
    buckets: test or tooling code, module bodies, unresolved frames, and
    stacks that run only the runtime (``evidence.profile_convert``). ``None``
    when the run did not record the split."""
    parts = (run.repo_self_value, run.wait_value, run.library_value)
    if run.total_value is None or any(v is None for v in parts):
        return None
    return run_share(run.total_value, max(run.total_value - sum(parts), 0.0))


#: Exporters whose speedscope file holds no samples because it was pruned:
#: pyinstrument's ``-r speedscope`` renderer drops frames under 1% of the run
#: and leaves their time on the caller. The sampled profile the ``evidence
#: setup`` recipe writes from pyinstrument counts its samples, so it is never
#: read as pruned.
_PRUNING_EXPORTERS = frozenset({"pyinstrument"})


def low_signal(run: Any) -> str | None:
    """Why a profile run (stored, or an ingested document) cannot rank with
    confidence, as one token; ``None`` for a run that counted its samples and
    for every other kind.

    ``pruned_1pct``: no sample counts, from an exporter that prunes small
    frames, so self times are inflated and small functions are missing.
    ``no_samples``: no sample counts otherwise, so no share has an interval.
    A deterministic tracer counts calls, not samples, and also reads
    ``no_samples``; its measured rows say ``ordering_only``.
    """
    if run.kind != "profile" or run.total_samples is not None:
        return None
    if (run.tool_name or "").lower() in _PRUNING_EXPORTERS:
        return "pruned_1pct"
    return "no_samples"


#: What fixes each :func:`low_signal` token, as ``evidence status`` and ``add`` print it.
LOW_SIGNAL_FIX = {
    "pruned_1pct": (
        "no sample counts, and frames under 1% of the run were pruned by the "
        "speedscope export; profile with the recipe `repowise evidence setup` prints "
        "(or py-spy) for counted samples"
    ),
    "no_samples": (
        "no sample counts, so no share has an interval; a sampling profiler "
        "(the recipe `repowise evidence setup` prints) records them"
    ),
}


def split_words(shares: Mapping[str, float | None]) -> str | None:
    """The split in words, repo code first: ``repo code itself 47%, waits 30%,
    libraries 12%``. ``None`` when the run did not record it."""
    repo = shares.get("repo_self_share")
    if repo is None:
        return None
    parts = [f"repo code itself {repo:.0%}"]
    for key, word in (("wait_share", "waits"), ("library_share", "libraries")):
        if shares.get(key) is not None:
            parts.append(f"{word} {shares[key]:.0%}")
    return ", ".join(parts)


def window_claim(
    start: datetime | None, end: datetime | None, commit: str | None, base_commit: str | None
) -> str:
    """What a production run's rows claim, in words: ``in production over
    2026-10-30 to 2026-11-26, deployed 1a2b3c4d5e6f``. A window inside one
    day names its hours (UTC); a commit the source did not name is
    ``deployed commit unknown``, and a window that spans deploys names the
    oldest commit it covered and the newest."""
    if start is not None and end is not None:
        if start.date() == end.date():
            span = f"{start:%Y-%m-%d %H:%M} to {end:%H:%M} UTC"
        else:
            span = f"{start:%Y-%m-%d} to {end:%Y-%m-%d}"
        words = f"in production over {span}"
    else:
        words = "in production, window unknown"
    if commit is None:
        return f"{words}, deployed commit unknown"
    if base_commit and base_commit != commit:
        return f"{words}, deployed {base_commit[:12]} to {commit[:12]}"
    return f"{words}, deployed {commit[:12]}"


def production_window(run: Any) -> dict[str, Any] | None:
    """The window a stored production run covers, the oldest commit it
    covered (``base_commit``, when the source knows) and :func:`window_claim`.
    ``None`` for every other class: their payloads carry no ``window`` key,
    so they do not grow."""
    if run.workload_class != "production":
        return None
    start, end = _utc(run.window_start), _utc(run.window_end)
    start = start.astimezone(UTC) if start else None
    end = end.astimezone(UTC) if end else None
    return {
        "start": start.isoformat() if start else None,
        "end": end.isoformat() if end else None,
        "base_commit": run.base_commit_sha,
        "claim": window_claim(start, end, run.commit_sha, run.base_commit_sha),
    }


def run_status(run: Any, reference_commit: str | None) -> dict[str, Any]:
    """One stored run as the status view shows it.

    *reference_commit* is the indexed commit: a run measured there is
    ``current``, at any other commit ``stale``, and ``unknown`` without one.
    A production run also names its ``window`` (:func:`production_window`).
    """
    out = {
        "run_id": run.id,
        "kind": run.kind,
        "workload": run.workload_name,
        "class": run.workload_class,
        "environment": run.environment,
        "value_kind": run.value_kind,
        "unit": run.unit,
        "tool": run.tool_name,
        "tool_version": run.tool_version,
        "commit_sha": run.commit_sha,
        "freshness": coverage_freshness(run.commit_sha, reference_commit),
        "dirty": run.dirty,
        # SQLite hands back the stored UTC time without its zone.
        "ingested_at": (
            run.ingested_at.replace(tzinfo=run.ingested_at.tzinfo or UTC).isoformat()
            if run.ingested_at
            else None
        ),
        "units": {
            "total": run.units_total,
            "matched": run.units_matched,
            "skipped": run.units_skipped,
            "outside_repo": run.units_outside_repo,
            "truncated": run.units_truncated,
        },
        "skipped": json.loads(run.skipped_json) if run.skipped_json else None,
        "total_value": run.total_value,
        **time_split(run),
        "unattributed_share": unattributed_share(run),
        "total_samples": run.total_samples,
        "low_signal": low_signal(run),
        "mapping_partial": bool(run.mapping_partial),
        "tests_failed": run.tests_failed,
    }
    window = production_window(run)
    if window is not None:
        out["window"] = window
    return out


def evidence_status(runs: Iterable[Any], reference_commit: str | None) -> dict[str, Any]:
    """The newest run of every workload (kind, name, environment), in the
    order of *runs* (newest ingest first), and how many runs each kind keeps.

    Newest is :func:`evidence.hotness.newest_runs`, the run the measured views
    rank: by measurement time, not ingest time, so re-adding an old profile
    does not make it the workload's newest here while the ranking keeps
    another. A partially mapped run is never a basis for ranking, so it is a
    workload's newest only when the workload has no complete run; it still
    counts as stored."""
    from .hotness import newest_runs

    runs = list(runs)
    by_kind: dict[str, list[Any]] = {}
    for run in runs:
        by_kind.setdefault(run.kind, []).append(run)
    newest: set[str] = set()
    for kind_runs in by_kind.values():
        complete = [r for r in kind_runs if not r.mapping_partial]
        newest |= newest_runs(complete)
        covered = {(r.workload_name, r.environment) for r in complete}
        newest |= newest_runs(
            r for r in kind_runs if (r.workload_name, r.environment) not in covered
        )
    return {
        "reference_commit": reference_commit,
        "runs_stored": {kind: len(kind_runs) for kind, kind_runs in sorted(by_kind.items())},
        "workloads": [run_status(run, reference_commit) for run in runs if run.id in newest],
    }


def _utc(when: datetime | None) -> datetime | None:
    # SQLite hands back the stored UTC time without its zone.
    return when.replace(tzinfo=when.tzinfo or UTC) if when else None


def freshness_entry(
    commit_sha: str | None,
    ingested_at: datetime | None,
    reference_commit: str | None,
    now: datetime,
) -> dict[str, Any]:
    """One stored measurement: the commit it measured, when it was ingested,
    its age in whole days, and ``current``/``stale``/``unknown`` against
    *reference_commit* (``coverage_freshness``)."""
    at = _utc(ingested_at)
    return {
        "present": True,
        "commit_sha": commit_sha,
        "ingested_at": at.isoformat() if at else None,
        "age_days": max((now - at).days, 0) if at else None,
        "freshness": coverage_freshness(commit_sha, reference_commit),
    }


#: Nothing stored: no commit, no age; freshness is unknown, never current.
ABSENT: dict[str, Any] = {
    "present": False,
    "commit_sha": None,
    "ingested_at": None,
    "age_days": None,
    "freshness": "unknown",
}


async def load_evidence_freshness(
    session: Any, repository_id: str, reference_commit: str | None, *, now: datetime | None = None
) -> dict[str, Any]:
    """How fresh the stored coverage and runtime evidence are against
    *reference_commit*: the newest coverage ingest, and the newest runtime
    run with how many runs are stored. One keyed read per store."""
    from ..persistence.crud.analysis.coverage import load_newest_ingest
    from ..persistence.crud.analysis.evidence import newest_evidence_run

    now = now or datetime.now(UTC)
    ingest = await load_newest_ingest(session, repository_id)
    coverage = (
        freshness_entry(ingest.ingested_commit_sha, ingest.ingested_at, reference_commit, now)
        if ingest is not None
        else dict(ABSENT)
    )
    runs, newest = await newest_evidence_run(
        session, repository_id, prefer_commit=reference_commit
    )
    runtime = (
        freshness_entry(newest.commit_sha, newest.ingested_at, reference_commit, now)
        if newest is not None
        else dict(ABSENT)
    )
    runtime["runs"] = runs
    return {"reference_commit": reference_commit, "coverage": coverage, "runtime": runtime}


__all__ = [
    "ABSENT",
    "LOW_SIGNAL_FIX",
    "evidence_status",
    "freshness_entry",
    "load_evidence_freshness",
    "low_signal",
    "run_share",
    "run_status",
    "split_words",
    "time_split",
    "unattributed_share",
]
