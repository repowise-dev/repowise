"""What to run to collect evidence, per build unit: the plan ``evidence setup`` prints.

:func:`evidence_setup` reads a checkout and says, for each unit the coverage
stacks name (:mod:`repowise.core.test_runners.stacks`), the command that
writes a report repowise reads, what that command needs first, where the
report lands, and the exact ``repowise`` line that ingests it, then the
runtime targets worth profiling (:mod:`.runtime_setup`). A report already on
disk comes first. ``next_call`` is the sequence of steps an agent runs next,
each with the directory it runs in. Nothing is written.
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Collection, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..build_units.model import BuildUnitIndex
from ..test_runners.packages import quote_arg
from ..test_runners.stacks import Stack, detect_repo_stacks
from .runtime_setup import (
    CLASS_ORDER,
    EVIDENCE_STATUS_COMMAND,
    RuntimeInputs,
    runtime_targets,
    timed_target,
)

#: Builds the index evidence joins to; no model calls, no API key.
INIT_COMMAND = "repowise init --yes --no-prose"
SETUP_COMMAND = "repowise evidence setup --format json"
COVERAGE_STATUS_COMMAND = "repowise coverage status"
#: Says what is wrong with an index that exists but cannot be read.
DOCTOR_COMMAND = "repowise doctor"
#: Brings an index written by an older repowise up to date.
UPDATE_COMMAND = "repowise update"

# A report glob ``coverage add`` expands itself, so no shell may expand it first.
_GLOB = re.compile(r"[*?\[\]{}]")


def _report_arg(path: str) -> str:
    """*path* as one argument: a glob single-quoted (literal in sh and PowerShell alike)."""
    return f"'{path}'" if _GLOB.search(path) and "'" not in path else quote_arg(path)


def coverage_ingest_line(reports: Iterable[str]) -> str:
    """The ``coverage add`` line for *reports*, repo-relative, run from the repository root."""
    return "repowise coverage add " + " ".join(_report_arg(r) for r in reports)


def _joined(root: str, rel: str) -> str:
    return posixpath.join(root, rel) if root else rel


def _written_reports(stack: Stack) -> list[str]:
    """What the stack's command writes, repo-relative: the report, and ``.coverage`` with contexts."""
    reports = list(stack.report_paths()[:1])
    reports += [_joined(stack.root, r) for r in stack.push_reports.split()]
    return reports


def _found(repo_root: Path, stack: Stack, claimed: set[str]) -> list[str]:
    """Reports matching *stack*'s globs already on disk, minus ones a deeper unit claimed."""
    from ..analysis.health.coverage import discover_artifacts

    out: list[str] = []
    globs = [*stack.report_paths(), *(_joined(stack.root, r) for r in stack.push_reports.split())]
    for path in discover_artifacts(repo_root, globs=globs):
        try:
            rel = path.resolve().relative_to(repo_root.resolve()).as_posix()
        except ValueError:
            continue
        if rel not in claimed:
            claimed.add(rel)
            out.append(rel)
    return out


def _measures(repo_root: Path, found: list[str], head: str | None) -> bool:
    """Whether reports *found* on disk measure *head*, by the commit ``coverage add`` would stamp.

    A report written before the last commit is stamped with the commit it
    measured, so ingesting it again never brings coverage to HEAD: the unit's
    command has to run again instead.
    """
    from ..analysis.health.coverage import measurement_commit, oldest_report_time

    if head is None:
        return True
    when = oldest_report_time(repo_root / p for p in found)
    return measurement_commit(repo_root, when, head) == head


def _coverage_unit(stack: Stack, found: list[str], current: bool) -> dict[str, Any]:
    """One unit's coverage plan, from :meth:`Stack.to_dict` plus what is on disk.

    *current*: the reports *found* measure HEAD. Reports from an older commit
    are listed, and the ingest line names what the command writes instead.
    """
    shown = stack.to_dict()
    if found and (current or not shown["command"]):
        ingest: str | None = coverage_ingest_line(found)
    elif shown["command"]:
        ingest = coverage_ingest_line(_written_reports(stack))
    else:
        ingest = None
    return {
        "root": shown["root"],
        "stack": shown["stack"],
        "runner": shown["runner"],
        "package_manager": shown["package_manager"],
        "coverage": {
            "command": shown["command"],
            "prerequisites": shown["prerequisites"],
            "reports": shown["reports"],
            "found": found,
            "found_current": bool(found) and current,
            "ingest": ingest,
            "reason": shown["reason"],
            "notes": shown["notes"],
        },
    }


def _step(repo_root: Path, rel_dir: str, command: str) -> dict[str, str]:
    return {"cwd": str(repo_root / rel_dir) if rel_dir else str(repo_root), "run": command}


def _found_call(repo_root: Path, units: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Ingest every report on disk that measures HEAD, of every unit, in one call."""
    current = [r for u in units if u["coverage"]["found_current"] for r in u["coverage"]["found"]]
    if not current:
        return None
    return {
        "purpose": "Ingest the coverage reports already on disk.",
        "steps": [
            _step(repo_root, "", coverage_ingest_line(current)),
            _step(repo_root, "", COVERAGE_STATUS_COMMAND),
        ],
    }


def _coverage_call(repo_root: Path, units: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    """Run the first unit with a coverage command and ingest what it wrote."""
    for unit in units:
        cov = unit["coverage"]
        if cov["command"]:
            where = unit["root"] or "the repository root"
            return {
                "purpose": f"Run the {unit['stack']} tests in {where} with coverage, then ingest the report.",
                "steps": [
                    *(_step(repo_root, unit["root"], p) for p in cov["prerequisites"]),
                    _step(repo_root, unit["root"], cov["command"]),
                    _step(repo_root, "", cov["ingest"]),
                    _step(repo_root, "", COVERAGE_STATUS_COMMAND),
                ],
            }
    return None


def _runtime_call(
    repo_root: Path, units: Sequence[dict[str, Any]], done: Collection[str]
) -> dict[str, Any] | None:
    """Profile the best target not in *done* (entry point, then benchmark, then tests) and ingest it.

    A target with a ``reason`` (an entry point waiting for its arguments) is the agent's to finish, never a step.
    """
    targets = [
        t for u in units for t in u.get("runtime", ())
        if t["ingest"] and t["reason"] is None and t["workload"] not in done
    ]
    if not targets:
        return None
    best = min(targets, key=lambda t: CLASS_ORDER.index(t["class"]))
    cwd = {"cwd": best["cwd"]}
    return {
        "purpose": f"Profile {best['workload']} ({best['class']}), then ingest the profile."
        + (f" {best['note']}" if best.get("note") else ""),
        "steps": [
            *({**cwd, "run": p} for p in best["prerequisites"]),
            {**cwd, "run": best["command"]},
            _step(repo_root, "", best["ingest"]),
            _step(repo_root, "", EVIDENCE_STATUS_COMMAND),
        ],
    }


def evidence_setup(
    repo_root: str | Path,
    *,
    indexed: bool,
    units: BuildUnitIndex | None = None,
    head: str | None = None,
    coverage_commit: str | None = None,
    runtime: RuntimeInputs | None = None,
    coverage_only: bool = False,
    index_error: str | None = None,
    index_fix: str = DOCTOR_COMMAND,
) -> dict[str, Any]:
    """The setup plan for the checkout at *repo_root*, JSON-ready.

    *units* is the stored build-unit index (``None`` or empty without an
    index); *indexed* says whether the repository has one, since every ingest
    line needs it. *head* is the commit the checkout is at, when git can say.
    *coverage_commit* is the commit the newest stored coverage measured.
    *runtime* is what the index knows about entry points and benchmarks; each
    unit lists its runtime targets unless *coverage_only*. ``next_call``
    ingests a coverage report on disk first, then profiles the best runtime
    target, then runs a unit's tests with coverage. What is already stored
    for *head* (coverage, a workload's run) is not asked for again, so an
    agent that reruns setup after each step comes to an end.
    *index_error* is why an index that exists could not be read: the plan
    then sends the agent to *index_fix* (``repowise doctor`` unless the
    error names another), never to a fresh ``init``.
    """
    root = Path(repo_root)
    stacks = detect_repo_stacks(root, units)
    claimed: set[str] = set()
    # Deepest unit first, so a nested unit keeps its own report from the root's globs.
    found = {
        id(s): _found(root, s, claimed)
        for s in sorted(stacks, key=lambda s: -(s.root.count("/") + bool(s.root)))
    }
    planned = [_coverage_unit(s, found[id(s)], _measures(root, found[id(s)], head)) for s in stacks]
    if not coverage_only:
        _add_runtime(root, stacks, planned, runtime, head)
    covered = head is not None and coverage_commit == head
    next_call = _next_call(root, planned, indexed=indexed, covered=covered, done=_measured_at(runtime, head))
    if next_call is not None:
        reason = None
    elif indexed and covered:
        reason = "Evidence at this commit is ingested; nothing left to run."
    else:
        reason = "No unit has a command repowise is sure of; see each unit's reason."
    if index_error is not None:
        next_call = {
            "purpose": "The index exists but could not be read; find out why.",
            "steps": [_step(root, "", index_fix)],
        }
        reason = f"The index could not be read: {index_error}"
    return {
        "repo": str(root),
        "indexed": indexed,
        "commit": head,
        "units": planned,
        "next_call": next_call,
        "reason": reason,
    }


def _add_runtime(
    root: Path,
    stacks: Sequence[Stack],
    planned: Sequence[dict[str, Any]],
    runtime: RuntimeInputs | None,
    head: str | None,
) -> None:
    """Give each planned unit its runtime targets; none without runtime inputs (no index)."""
    mains = _go_mains(root, stacks) if runtime is not None else []
    roots = {s.root for s in stacks}
    for stack, unit in zip(stacks, planned, strict=True):
        unit["runtime"] = (
            runtime_targets(stack, root, runtime, head, mains, roots) if runtime is not None else []
        )


def _measured_at(runtime: RuntimeInputs | None, head: str | None) -> set[str]:
    """Workloads with a stored run at *head*."""
    if runtime is None or head is None:
        return set()
    return {workload for workload, commit in runtime.measured if commit == head}


def _next_call(
    root: Path, planned: Sequence[dict[str, Any]], *, indexed: bool, covered: bool, done: set[str]
) -> dict[str, Any] | None:
    """Index first; then a report on disk, the best profile, the tests with coverage.

    *covered*: coverage at this commit is stored. *done*: workloads measured here.
    """
    if not indexed:
        return {
            "purpose": "Index the repository (evidence joins to the index), then ask again.",
            "steps": [_step(root, "", INIT_COMMAND), _step(root, "", SETUP_COMMAND)],
        }
    return (
        (None if covered else _found_call(root, planned))
        or _runtime_call(root, planned, done)
        or (None if covered else _coverage_call(root, planned))
    )


def timed_targets(
    repo_root: str | Path,
    paths: Iterable[str],
    *,
    units: BuildUnitIndex | None,
    runtime: RuntimeInputs,
) -> dict[str, dict[str, Any]]:
    """For each repo-relative path in *paths*, the workload ``perf measure``
    times for a change there (:func:`~.runtime_setup.timed_target`); a path
    with none is left out. One stack detection for all of them. Blocking reads.
    """
    root = Path(repo_root)
    stacks = detect_repo_stacks(root, units)
    mains = _go_mains(root, stacks)
    out: dict[str, dict[str, Any]] = {}
    # Files of one language in one directory share a unit, so its targets are read once.
    seen: dict[tuple[str, str], dict[str, Any] | None] = {}
    for path in paths:
        key = (posixpath.splitext(path)[1], posixpath.dirname(path))
        if key not in seen:
            seen[key] = timed_target(stacks, root, runtime, path, mains)
        target = seen[key]
        if target:
            out[path] = target
    return out


def _go_mains(root: Path, stacks: Sequence[Stack]) -> list[str]:
    """Go ``package main`` directories, read only when a unit is Go."""
    if not any(s.language == "go" for s in stacks):
        return []
    from ..analysis.published_packages import BuildFacts, go_main_packages

    return go_main_packages(BuildFacts(root))


__all__ = [
    "COVERAGE_STATUS_COMMAND",
    "DOCTOR_COMMAND",
    "INIT_COMMAND",
    "SETUP_COMMAND",
    "UPDATE_COMMAND",
    "coverage_ingest_line",
    "evidence_setup",
    "timed_targets",
]
