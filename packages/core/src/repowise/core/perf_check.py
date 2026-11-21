"""Gate a change on exact runtime evidence: the owner of ``repowise perf check``'s rule.

Only values that do not vary from run to run may fail a check. Today that is
allocations per operation from stored benchmark results (``go test
-benchmem``): the same code allocates the same, so any growth is the change.
Time never gates (:mod:`repowise.core.perf_measure` reports it, informational
only). Another exact value (statements per test from query counts) joins
through :data:`EXACT_METRICS` and the snapshot below.

A check compares two snapshots of the newest benchmark run of each workload,
whatever environment it ran in: one saved before the change
(``.repowise/perf/exact/<name>.json``, named the way ``perf measure`` names
its baselines, in a directory of its own so the two never overwrite each
other) and the one stored now. A workload whose newest run is still the saved
one, not ingested since, was not run again, so it is not judged. A benchmark
the baseline has and the run now lacks is listed as ``dropped``.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import TYPE_CHECKING, Any, Literal

from repowise.core.perf_measure import BaselineError, baseline_path

if TYPE_CHECKING:
    from pathlib import Path

#: Benchmark columns that are exact where the tool reports them.
EXACT_METRICS = ("allocs_per_op",)
#: Evidence formats whose results carry them: ``go test -bench -benchmem`` output.
EXACT_FORMATS = frozenset({"go-bench"})
SNAPSHOT_VERSION = 1

Verdict = Literal["passed", "regressed", "cannot_judge"]


def newest_runs(runs: Iterable[Any], workload: str | None = None) -> dict[str, Any]:
    """Workload -> its newest benchmark run (*runs* are stored runs, newest first)."""
    newest: dict[str, Any] = {}
    for run in runs:
        if run.kind == "benchmark" and (workload is None or run.workload_name == workload):
            newest.setdefault(run.workload_name, run)
    return newest


def snapshot(newest: Mapping[str, Any], rows: Iterable[Any]) -> dict[str, Any]:
    """Each workload's run in *newest* (:func:`newest_runs`) with its exact values.

    *rows* are those runs' benchmark rows. A workload with no exact value is
    left out: it has nothing a check could judge.
    """
    by_run = {run.id: name for name, run in newest.items()}
    values: dict[str, dict[str, dict[str, float]]] = {}
    for row in rows:
        name = by_run.get(row.run_id)
        exact = {m: getattr(row, m) for m in EXACT_METRICS if getattr(row, m) is not None}
        if name is not None and exact:
            values.setdefault(name, {})[row.benchmark_key] = exact
    return {
        "version": SNAPSHOT_VERSION,
        "workloads": {
            name: {
                "run_id": run.id,
                "commit_sha": run.commit_sha,
                "dirty": run.dirty,
                # A restamped run (the same output ingested again) keeps its id.
                "ingested_at": run.ingested_at.isoformat() if run.ingested_at else None,
                "benchmarks": dict(sorted(values[name].items())),
            }
            for name, run in sorted(newest.items())
            if name in values
        },
    }


def compare(base: Mapping[str, Any], head: Mapping[str, Any]) -> dict[str, Any]:
    """*head*'s exact values against *base*'s: any growth regresses, timing is never read.

    ``cannot_judge`` when no benchmark has an exact value on both sides of a
    workload that was run again; ``reason`` says why.
    """
    changes: list[dict[str, Any]] = []
    compared = 0
    not_rerun: list[str] = []
    missing: list[str] = []
    dropped: list[dict[str, str]] = []
    for name, before in base["workloads"].items():
        after = head["workloads"].get(name)
        if after is None:
            missing.append(name)
        elif (after["run_id"], after.get("ingested_at")) == (
            before["run_id"], before.get("ingested_at")
        ):
            not_rerun.append(name)
        else:
            compared += _compare_workload(name, before, after, changes, dropped)
    verdict: Verdict
    if any(c["change"] == "regressed" for c in changes):
        verdict = "regressed"
    elif compared:
        verdict = "passed"
    else:
        verdict = "cannot_judge"
    return {
        "verdict": verdict,
        "reason": _reason(base, not_rerun, missing) if verdict == "cannot_judge" else None,
        "metrics": list(EXACT_METRICS),
        "compared": compared,
        "regressed": [c for c in changes if c["change"] == "regressed"],
        "improved": [c for c in changes if c["change"] == "improved"],
        "not_rerun": not_rerun,
        "missing": missing,
        "dropped": dropped,
    }


def _compare_workload(
    name: str,
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    changes: list[dict[str, Any]],
    dropped: list[dict[str, str]],
) -> int:
    """Compare one workload's benchmarks into *changes* and *dropped*; the values compared."""
    compared = 0
    for key, old in before["benchmarks"].items():
        new = after["benchmarks"].get(key)
        if new is None:
            dropped.append({"workload": name, "benchmark": key})
            continue
        for metric in (m for m in EXACT_METRICS if m in old and m in new):
            compared += 1
            if new[metric] != old[metric]:
                changes.append({
                    "workload": name, "benchmark": key, "metric": metric,
                    "base": old[metric], "head": new[metric],
                    "change": "regressed" if new[metric] > old[metric] else "improved",
                })
    return compared


def _reason(base: Mapping[str, Any], not_rerun: list[str], missing: list[str]) -> str:
    if not base["workloads"]:
        return "the baseline holds no exact values (allocations per op)"
    if not_rerun and not missing:
        return "no workload of the baseline was run and ingested again since it was saved"
    if missing and not not_rerun:
        return "no workload of the baseline has a stored run now"
    return "no benchmark has an exact value on both sides"


def snapshot_path(repowise_dir: Path, name: str) -> Path:
    """``.repowise/perf/exact/<name>.json``: ``perf measure``'s naming, a directory of its own."""
    path = baseline_path(repowise_dir, name)
    return path.parent / "exact" / path.name


def save_snapshot(repowise_dir: Path, name: str, snap: Mapping[str, Any]) -> Path:
    path = snapshot_path(repowise_dir, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(snap, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def load_snapshot(repowise_dir: Path, name: str) -> dict[str, Any]:
    path = snapshot_path(repowise_dir, name)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineError(
            f"no exact baseline named {name!r}; save one first with "
            f"`repowise perf check --save {name}`"
        ) from None
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline {name!r} could not be read: {exc}") from None
    if not _well_formed(raw):
        raise BaselineError(
            f"baseline {name!r} is not an exact baseline of this version; save it again"
        )
    return raw


def _well_formed(raw: Any) -> bool:
    """Whether *raw* has the shape :func:`snapshot` writes, down to each exact value."""
    if not isinstance(raw, dict) or raw.get("version") != SNAPSHOT_VERSION:
        return False
    workloads = raw.get("workloads")
    return isinstance(workloads, dict) and all(
        isinstance(w, dict)
        and isinstance(w.get("run_id"), str)
        and isinstance(w.get("benchmarks"), dict)
        and all(
            isinstance(values, dict)
            and all(
                isinstance(v, int | float) and not isinstance(v, bool) for v in values.values()
            )
            for values in w["benchmarks"].values()
        )
        for w in workloads.values()
    )


__all__ = [
    "EXACT_FORMATS",
    "EXACT_METRICS",
    "compare",
    "load_snapshot",
    "newest_runs",
    "save_snapshot",
    "snapshot",
    "snapshot_path",
]
