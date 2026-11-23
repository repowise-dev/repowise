"""What to run to collect evidence, per build unit: the plan ``evidence setup`` prints.

:func:`evidence_setup` reads a checkout and says, for each unit the coverage
stacks name (:mod:`repowise.core.test_runners.stacks`), the command that
writes a report repowise reads, what that command needs first, where the
report lands, and the exact ``repowise`` line that ingests it, then the
runtime targets worth profiling (:mod:`.runtime_setup`) and the JUnit XML
report its tests can write for test timings. A report already on
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
from .ci_snippet import ci_snippet
from .runtime_setup import (
    ARGS_PLACEHOLDER,
    CLASS_ORDER,
    EVIDENCE_STATUS_COMMAND,
    RuntimeInputs,
    evidence_ingest_line,
    is_offered,
    offered_targets,
    runtime_targets,
    timed_target,
)

#: Builds the index evidence joins to; no model calls, no API key.
INIT_COMMAND = "repowise init --yes --no-prose"
#: What a person runs; ``SETUP_COMMAND`` is the same plan as JSON for an agent.
EVIDENCE_SETUP = "repowise evidence setup"
SETUP_COMMAND = f"{EVIDENCE_SETUP} --format json"
#: The coverage part of the plan alone.
COVERAGE_SETUP = "repowise coverage setup"
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
    ci: str | None = None,
    branch: str | None = None,
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
    With *ci* (``github`` or ``gitlab``), ``ci`` holds the CI file that runs
    each unit's tests with coverage on pushes to *branch*
    (:func:`~.ci_snippet.ci_snippet`); the caller writes it.
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
        for stack, unit in zip(stacks, planned, strict=True):
            unit["test_timings"] = _test_timings(stack, head)
    covered = head is not None and coverage_commit == head
    done = _measured_at(runtime, head)
    next_call = _with_optional(
        _next_call(root, planned, indexed=indexed, covered=covered, done=done),
        _optional_entry_points(planned, done),
    )
    if next_call is not None:
        reason = None
    elif indexed and covered and not coverage_only and not _offers_runtime(planned):
        reason = (
            "Coverage at this commit is ingested. No unit has an entry point, benchmark "
            "or test suite repowise can profile, so there is nothing left to run."
        )
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
    plan = {
        "repo": str(root),
        "indexed": indexed,
        "commit": head,
        "units": planned,
        "next_call": next_call,
        "reason": reason,
    }
    if ci is not None:
        plan["ci"] = ci_snippet(stacks, ci, branch)
    return plan


def _test_timings(stack: Stack, head: str | None) -> dict[str, Any] | None:
    """How the unit's test run writes JUnit XML, and the line that ingests it; None when unknown.

    ``flag`` is what to add to the unit's own test command (None: the runner
    writes the report by default); ``env`` is what that command needs in its
    environment for the report (None: nothing). Test timings are never a step of
    ``next_call``: they rank tests, they never decide anything.
    """
    if not stack.junit_report:
        return None
    report = _joined(stack.root, stack.junit_report)
    workload = f"{stack.root}: {stack.runner} tests" if stack.root else f"{stack.runner} tests"
    return {
        "flag": stack.junit_flag or None,
        "env": stack.junit_vars() or None,
        "report": report,
        "ingest": evidence_ingest_line(report, workload, "unit_test", head) if head else None,
    }


def _offers_runtime(planned: Sequence[dict[str, Any]]) -> bool:
    """Whether any unit lists a runtime target with an ingest line (:func:`offered_targets`)."""
    return any(is_offered(t) for u in planned for t in u.get("runtime", ()))


def runtime_offer(
    repo_root: str | Path,
    *,
    units: BuildUnitIndex | None,
    runtime: RuntimeInputs,
    head: str | None,
) -> list[dict[str, Any]]:
    """:func:`~.runtime_setup.offered_targets` for the checkout at *repo_root*:
    the runtime targets its plan prints an ingest line for. Blocking reads."""
    root = Path(repo_root)
    stacks = detect_repo_stacks(root, units)
    return offered_targets(stacks, root, runtime, head, _go_mains(root, stacks))


async def load_runtime_offer(
    session: Any, repository_id: str, repo_root: str | Path | None, head: str | None
) -> list[dict[str, Any]] | None:
    """:func:`runtime_offer` from the stored build units and runtime inputs;
    None when *repo_root* is not a checkout on this machine."""
    import asyncio

    from ..persistence.crud.build_units import load_build_units
    from .runtime_setup import load_runtime_inputs

    root = Path(repo_root) if repo_root else None
    if root is None or not root.is_dir():
        return None
    units = await load_build_units(session, repository_id)
    runtime = await load_runtime_inputs(session, repository_id)
    return await asyncio.to_thread(runtime_offer, root, units=units, runtime=runtime, head=head)


def offered_classes(offer: Iterable[dict[str, Any]]) -> tuple[str, ...]:
    """The workload classes in *offer*, in :data:`~.runtime_setup.CLASS_ORDER`."""
    found = {t["class"] for t in offer}
    return tuple(c for c in CLASS_ORDER if c in found)


def _with_optional(
    next_call: dict[str, Any] | None, optional: dict[str, Any] | None
) -> dict[str, Any] | None:
    """*next_call* carrying *optional* under ``optional``.

    With no step left, the call is still there, with no steps, so an agent
    that stops at a null ``next_call`` never stops before the entry points.
    """
    if optional is None:
        return next_call
    if next_call is None:
        next_call = {"purpose": "Only entry points waiting for their arguments are left.", "steps": []}
    return {**next_call, "optional": optional}


def _optional_entry_points(
    planned: Sequence[dict[str, Any]], done: Collection[str]
) -> dict[str, Any] | None:
    """Entry points waiting for their arguments, or servers waiting for requests,
    not measured here, by workload.

    Never a step, so named here. An entry point counts as measured once any
    run of it is stored at this commit (``app`` or ``app build``), but not one
    stored under the unfilled name (``app <args>``). Only names,
    so the default answer stays small; each target's command and ingest line
    are in ``units[].runtime``.
    """
    workloads = [
        t["workload"] for u in planned for t in u.get("runtime", ())
        if t["class"] == "entry_point" and t["command"] and t["ingest"] and t["reason"]
        and not any(
            (d == t["workload"] or d.startswith(t["workload"] + " ")) and ARGS_PLACEHOLDER not in d
            for d in done
        )
    ]
    if not workloads:
        return None
    return {
        "purpose": f"Fill {ARGS_PLACEHOLDER} of these entry points (units[].runtime) with a "
        "representative run, or send a server representative requests while it runs, then run "
        "each command and its ingest line.",
        "workloads": workloads,
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
    "COVERAGE_SETUP",
    "COVERAGE_STATUS_COMMAND",
    "DOCTOR_COMMAND",
    "EVIDENCE_SETUP",
    "INIT_COMMAND",
    "SETUP_COMMAND",
    "UPDATE_COMMAND",
    "coverage_ingest_line",
    "evidence_setup",
    "load_runtime_offer",
    "offered_classes",
    "runtime_offer",
    "timed_targets",
]
