"""Sum up the test selection records CI keeps beside each full test run.

``impacted_tests_shadow.py`` writes one ``impacted-shadow.json`` per run. This
reads a folder of them and reports how often the selection ran everything, the
test time it would have saved, which failing test files fell outside it, and
where the first failing file sat in the prioritized order.

    python scripts/impacted_tests_shadow_report.py records/
    python scripts/impacted_tests_shadow_report.py records/ --fetch   # gh download first

Records are read from ``<dir>/**/impacted-shadow.json``. A commit recorded by
several runs (a rerun) counts once, from its latest complete run. Only pull
request runs count unless ``--event`` names others (``push``, ``schedule``):
a red main branch fails the same tests on every push. A failing file outside
the selection that also failed on another change is marked as such: a test
failing on unrelated changes is flaky or already failing, not missed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import subprocess
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

ARTIFACT = "impacted-shadow"
#: Files left out of a selection the text report lists before summing up the rest.
MISS_ROWS = 20


def load(root: Path, events: Collection[str] = ("pull_request",)) -> list[dict[str, Any]]:
    """Every readable record of *events* under *root*, one per commit: its latest complete run."""
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(root.rglob(f"{ARTIFACT}.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(record, dict) or not record.get("complete") or not record.get("sha"):
            continue
        if record.get("event") not in events:
            continue
        kept = latest.get(record["sha"])
        if kept is None or _run_id(record) > _run_id(kept):
            latest[record["sha"]] = record
    return sorted(latest.values(), key=_run_id)


def _run_id(record: Mapping[str, Any]) -> int:
    """The record's run id as a number; 0 when it has none."""
    value = str(record.get("run_id") or "")
    return int(value) if value.isdigit() else 0


def _quantile(values: Sequence[float], q: float) -> float:
    """The *q* quantile of *values*, interpolated between the closest ranks."""
    ordered = sorted(values)
    at = q * (len(ordered) - 1)
    low = int(at)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (at - low)


def _elsewhere(records: Iterable[Mapping[str, Any]]) -> dict[str, set[Any]]:
    """``{test file: the pull requests (or pushes) it failed on}``."""
    out: dict[str, set[Any]] = {}
    for r in records:
        for path in r.get("failing_files") or ():
            out.setdefault(path, set()).add(r.get("pr") or r.get("sha"))
    return out


def _misses(
    failing: Iterable[Mapping[str, Any]], seen: Mapping[str, set[Any]]
) -> list[dict[str, Any]]:
    """Each failing file left out of a selection, with the other changes it failed on."""
    out = []
    for r in failing:
        own = r.get("pr") or r.get("sha")
        for path in r.get("missed") or ():
            others = sorted(map(str, seen.get(path, set()) - {own}))
            out.append(
                {"pr": r.get("pr"), "sha": r.get("sha"), "file": path, "also_failed_on": others}
            )
    return out


def _ordering(failing: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Where the first failing file sat in the prioritized order, over the failing runs."""
    ranked = [r for r in failing if r.get("first_failing_rank") and r.get("order_length")]
    out: dict[str, Any] = {
        "ordered_runs": len(ranked),
        # Failing runs whose failing files the prioritized order never lists:
        # left out of the rank statistics, so counted here instead.
        "unordered_runs": len(failing) - len(ranked),
        "first_failure_rank_median": None,
        "first_failure_position_median": None,
        "first_failure_position_p75": None,
        "order_length_median": None,
    }
    if not ranked:
        return out
    positions = [r["first_failing_rank"] / r["order_length"] for r in ranked]
    out.update(
        first_failure_rank_median=statistics.median(r["first_failing_rank"] for r in ranked),
        first_failure_position_median=statistics.median(positions),
        first_failure_position_p75=_quantile(positions, 0.75),
        order_length_median=statistics.median(r["order_length"] for r in ranked),
    )
    return out


def summarize(records: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """The report's numbers, from :func:`load`'s records."""
    selected = [r for r in records if r.get("run_all") is not None]
    failing = [r for r in selected if r.get("failing_files")]
    return {
        "runs": len(selected),
        "run_all": sum(bool(r["run_all"]) for r in selected),
        "total_seconds": round(sum(r.get("total_seconds") or 0 for r in selected), 1),
        "seconds_saved": round(sum(r.get("estimated_seconds_saved") or 0 for r in selected), 1),
        "failing_runs": len(failing),
        "failing_runs_selective": sum(not r["run_all"] for r in failing),
        "failing_runs_caught": sum(not r.get("missed") for r in failing),
        "misses": _misses(failing, _elsewhere(records)),
        **_ordering(failing),
    }


def _pct(part: float, whole: float) -> str:
    return f"{100 * part / whole:.1f}%" if whole else "n/a"


def render(s: Mapping[str, Any]) -> str:
    lines = [
        f"Runs: {s['runs']} (one per commit). Ran everything: {s['run_all']} "
        f"({_pct(s['run_all'], s['runs'])}).",
        f"Test time saved: {s['seconds_saved']:.0f} of {s['total_seconds']:.0f} s "
        f"({_pct(s['seconds_saved'], s['total_seconds'])}).",
        f"Runs with a failing test: {s['failing_runs']} ({s['failing_runs_selective']} selective); "
        f"every failing file selected in {s['failing_runs_caught']}.",
    ]
    for m in s["misses"][:MISS_ROWS]:
        others = len(m["also_failed_on"])
        also = (
            f"; it also failed on {others} other change(s), so flaky or already failing"
            if others
            else ""
        )
        where = f"PR {m['pr']}, " if m["pr"] else ""
        lines.append(f"  Outside the selection: {m['file']} ({where}{str(m['sha'])[:8]}){also}.")
    if len(s["misses"]) > MISS_ROWS:
        lines.append(f"  {len(s['misses']) - MISS_ROWS} more outside the selection (see --json).")
    if s["ordered_runs"]:
        lines.append(
            f"First failing file in the prioritized order, over {s['ordered_runs']} runs: median "
            f"rank {s['first_failure_rank_median']:g} of a median {s['order_length_median']:g} files; "
            f"median position {100 * s['first_failure_position_median']:.1f}%, "
            f"p75 {100 * s['first_failure_position_p75']:.1f}%."
        )
    if s["unordered_runs"]:
        lines.append(
            f"No failing file in the prioritized order: {s['unordered_runs']} of "
            f"{s['failing_runs']} runs with a failing test, left out of the ranks above."
        )
    return "\n".join(lines)


def fetch(root: Path, repo: str | None) -> int:
    """Download every unexpired record not under *root* yet; returns how many were fetched."""
    where = ["-R", repo] if repo else []
    api = f"repos/{repo}/actions/artifacts" if repo else "repos/{owner}/{repo}/actions/artifacts"
    # The ``{owner}/{repo}`` placeholder resolves only inside a GitHub checkout.
    listed = subprocess.run(
        ["gh", "api", "-X", "GET", api, "-f", f"name={ARTIFACT}", "-f", "per_page=100",
         "--paginate", "--jq", ".artifacts[] | select(.expired | not) | .workflow_run.id"],
        capture_output=True, text=True, check=True,
    ).stdout.split()  # fmt: skip
    fetched = 0
    for run_id in sorted(set(listed)):
        dest = root / run_id
        if (dest / f"{ARTIFACT}.json").is_file():
            continue
        done = subprocess.run(
            ["gh", "run", "download", run_id, *where, "-n", ARTIFACT, "-D", str(dest)],
            capture_output=True,
            text=True,
        )
        if done.returncode:
            print(f"Run {run_id}: {done.stderr.strip()}", file=sys.stderr)
        fetched += done.returncode == 0
    return fetched


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("dir", type=Path, help="folder holding the records")
    parser.add_argument(
        "--fetch", action="store_true", help="download missing records with gh first"
    )
    parser.add_argument("--repo", help="owner/name for --fetch; needed outside a GitHub checkout")
    parser.add_argument(
        "--event",
        action="append",
        help="a CI event whose runs count; repeat for several (default: pull_request)",
    )
    parser.add_argument("--json", action="store_true", help="print the numbers as JSON")
    args = parser.parse_args(argv)
    if args.fetch:
        args.dir.mkdir(parents=True, exist_ok=True)
        try:
            print(f"Fetched {fetch(args.dir, args.repo)} record(s).", file=sys.stderr)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Could not list the records with gh: {exc}", file=sys.stderr)
            return 1
    summary = summarize(load(args.dir, args.event or ("pull_request",)))
    print(json.dumps(summary, indent=2, sort_keys=True) if args.json else render(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
