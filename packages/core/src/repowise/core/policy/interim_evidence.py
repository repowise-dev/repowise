"""The evidence ``repowise check`` evaluates, read from the existing analyzers.

Interim: this adapter is swapped for the Change Record evidence builder
(``repowise.core.changes.evidence``) once that lands; both emit the same
``ChangeEvidence`` shape, so the policy evaluator does not change.

Git alone gives the components a change touches (declared in the catalog) and
its provenance. An index adds derived components, bus factor, reaching tests
and the governing decisions the change crosses; without one those items are
``unknown`` and the rules that need them are not evaluated. Contract breaks
need a parse of both sides and are left to the evidence builder.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Commits of one change labelled for provenance; past it the label is unknown.
MAX_COMMITS = 500


def _item(
    kind: str, label: str, status: str, basis: str, value: Any, details: list, source: str
) -> dict[str, Any]:
    return {
        "kind": kind,
        "label": label,
        "status": status,
        "basis": basis,
        "value": value,
        "details": details,
        "source": source,
    }


def _unknown(kind: str, label: str, why: str) -> dict[str, Any]:
    return _item(kind, label, "unknown", "unresolved", None, [{"reason": why}], "repowise check")


async def gather_evidence(
    repo_root: Path | str,
    change: Any,
    *,
    session: Any = None,
    repo_id: str | None = None,
) -> dict[str, Any]:
    """``ChangeEvidence`` for *change* (a ``ChangeSet``); *session* is an open index, if any."""
    root = Path(repo_root)
    changed = sorted({*change.files, *change.deleted})
    indexed = session is not None and repo_id is not None
    items = [
        await _components(root, changed, session, repo_id) if indexed else _declared(root, changed),
        await asyncio.to_thread(_provenance, root, change),
    ]
    if indexed:
        items.append(await _reaching_tests(session, repo_id, root, change))
        items.append(await _decisions(session, repo_id, changed))
    else:
        items.append(_unknown("reaching_tests", "Reaching tests", "no index"))
        items.append(_unknown("decisions_crossed", "Decisions crossed", "no index"))
    items.append(
        _unknown("contracts", "Contracts", "contract comparison comes from the evidence builder")
    )
    return {
        "schema_version": 1,
        "change_id": change.head or change.label,
        "revspec": change.label,
        "generated_at": datetime.now(UTC).isoformat(),
        "items": items,
    }


def _component_row(comp: Any, files: list[str], bus_factor: int | None = None) -> dict[str, Any]:
    from repowise.core.components.catalog import owner_group_name

    return {
        "name": comp.name,
        "criticality": comp.criticality,
        "tags": list(comp.tags),
        "owner": owner_group_name(comp.owner) if comp.owner else None,
        "source": comp.source,
        "files": files,
        "bus_factor": bus_factor,
    }


def _declared(root: Path, changed: list[str]) -> dict[str, Any]:
    from repowise.core.components import read_catalog, resolve_components

    catalog = read_catalog(root)
    resolved = resolve_components(catalog.components, [], changed)
    rows = [_component_row(c, resolved.files[c.name]) for c in resolved.components if resolved.files[c.name]]
    return _item(
        "components_touched", "Components touched", "ok", "declared", len(rows), rows,
        ", ".join(catalog.sources) or "no catalog file",
    )


async def _components(root: Path, changed: list[str], session: Any, repo_id: str) -> dict[str, Any]:
    from repowise.core.components.store import (
        component_rollups,
        load_repo_components,
        workspace_services,
    )

    repo = await load_repo_components(session, repo_id, root, services=workspace_services(root))
    wanted = set(changed)
    touched = [
        c for c in repo.resolved.components if wanted & set(repo.resolved.files.get(c.name, ()))
    ]
    rollups = await component_rollups(
        session, repo_id, {c.name: repo.resolved.files[c.name] for c in touched}, repo.test_files
    )
    rows = []
    for c in touched:
        bus = (rollups.get(c.name) or {}).get("bus_factor") or {}
        value = bus.get("value") if bus.get("basis") == "measured" else None
        rows.append(_component_row(c, sorted(wanted & set(repo.resolved.files[c.name])), value))
    # Files no component claims do not count as touching a component.
    return _item(
        "components_touched", "Components touched", "ok", "measured", len(rows), rows,
        "index and " + (", ".join(repo.catalog.sources) or "derived components"),
    )


def _provenance(root: Path, change: Any) -> dict[str, Any]:
    from repowise.core.attribution import attribution_enabled
    from repowise.core.changes.store import change_provenance

    label = "Provenance"
    if not attribution_enabled(root):
        return _unknown("provenance", label, "attribution is off for this repository")
    if not (change.base and change.head):
        return _unknown("provenance", label, "uncommitted changes have no commits to label")
    commits = _labelled_commits(root, change.base, change.head)
    if commits is None:
        return _unknown("provenance", label, f"more than {MAX_COMMITS} commits, or git failed")
    labels = {c["sha"]: (c["label"], c["agent"]) for c in commits}
    state, agent, known = change_provenance(list(labels), labels)
    return _item(
        "provenance", label, "ok", "declared",
        {"label": state, "agent_commits": agent, "known_commits": known, "commits": len(commits)},
        commits, "public git markers: bot authors, trailers, footers, git-ai notes, Agent Trace",
    )


def _labelled_commits(root: Path, base: str, head: str) -> list[dict[str, Any]] | None:
    import subprocess

    from repowise.core.analysis.git_cli import _git
    from repowise.core.ingestion.git_commit_index import load_git_ai_note_agents
    from repowise.core.ingestion.git_indexer.agent_provenance import (
        AgentTraceIndex,
        classifier_from_repo_config,
        classify_commit,
    )
    from repowise.core.ingestion.git_indexer.records import (
        _LOG_FORMAT,
        _RECORD_SEP,
        _parse_commit_record,
    )

    try:
        raw = _git(
            ["log", f"-{MAX_COMMITS + 1}", "--numstat", f"--format={_LOG_FORMAT}", f"{base}..{head}"],
            str(root),
        )
    except (subprocess.SubprocessError, OSError):
        return None
    records = [r for r in (_parse_commit_record(c) for c in raw.split(_RECORD_SEP) if c.strip()) if r]
    if len(records) > MAX_COMMITS:
        return None
    try:
        from repowise.core.git_repo import Repo

        repo = Repo(root)
    except Exception:
        repo = None
    classifier = classifier_from_repo_config(root)
    notes = load_git_ai_note_agents(repo, MAX_COMMITS) if repo is not None else {}
    traces = AgentTraceIndex.load(repo) if repo is not None else None
    out = []
    for header, numstat in records:
        paths = {line.split("\t", 2)[2] for line in numstat if line.count("\t") >= 2}
        prov, _hit = classify_commit(classifier, header, paths, notes, traces)
        out.append(
            {
                "sha": header["sha"],
                "label": prov.attribution,
                "agent": prov.agent,
                "tier": prov.autonomy_tier,
                "channel": prov.channel,
            }
        )
    return out


async def _reaching_tests(session: Any, repo_id: str, root: Path, change: Any) -> dict[str, Any]:
    from repowise.core.analysis.test_collection import collect

    label = "Reaching tests"
    out = await collect(session, repo_id, root, change)
    if out.get("index_problem") or out.get("graph_error"):
        return _unknown("reaching_tests", label, str(out.get("index_problem") or out["graph_error"]))
    # A file nothing knows a test for is a known absence only when a current,
    # complete coverage map measured it; otherwise it is not known.
    measured = not (out.get("map_empty") or out.get("map_truncated")) and out.get("map_current")
    tests: dict[str, dict[str, str] | None] = {}
    for test_id, row in (out.get("covered") or {}).items():
        for path in row.get("source_files") or ():
            tests.setdefault(path, {})[test_id] = "measured"
    for row in out.get("inferred") or ():
        found = tests.setdefault(row["source_file"], {})
        found.setdefault(row["test_file"], "inferred")
    for path in out.get("unknown") or ():
        tests.setdefault(path, {} if measured else None)
    rows = [
        {
            "path": path,
            "tests": None if found is None else sorted(found),
            "basis": "unresolved"
            if found is None
            else ("measured" if not found or "measured" in found.values() else "inferred"),
        }
        for path, found in sorted(tests.items())
    ]
    if rows and all(r["tests"] is None for r in rows):
        return _unknown("reaching_tests", label, "no measured coverage and no test found")
    return _item(
        "reaching_tests", label, "ok", "measured" if measured else "inferred",
        sum(1 for r in rows if r["tests"] == []), rows, "coverage map and dependency graph",
    )


async def _decisions(session: Any, repo_id: str, changed: list[str]) -> dict[str, Any]:
    from repowise.core.persistence.crud.authority import governing_decisions_by_file

    by_file = await governing_decisions_by_file(session, repo_id)
    crossed: dict[str, dict[str, Any]] = {}
    for path in changed:
        for decision_id, title in by_file.get(path, ()):
            row = crossed.setdefault(decision_id, {"id": decision_id, "title": title, "paths": []})
            row["paths"].append(path)
    rows = list(crossed.values())
    return _item(
        "decisions_crossed", "Decisions crossed", "ok", "declared", len(rows), rows,
        "governing decisions that name a changed file",
    )
