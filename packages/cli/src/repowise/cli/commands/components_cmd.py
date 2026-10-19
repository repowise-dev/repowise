"""``repowise components`` - validate and list the repository's components.

``check`` reads ``.repowise/components.yaml`` and ``catalog-info.yaml`` and
validates them against the files git tracks; it needs no index and exits
non-zero on any error, so it fits a CI step. ``list`` shows declared and
derived components with their file counts, from the index when there is one.
``ownership`` compares declared owners (catalog, CODEOWNERS, teams.yaml) with
recent commit activity per component and lists the findings.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option


def _repo_root(path: str | None) -> Path:
    target = resolve_command_target(path=path, no_workspace_flag=True)
    assert target.repo_path is not None
    return Path(target.repo_path).resolve()


def _tracked(root: Path) -> list[str]:
    """Repo-relative POSIX paths git tracks, or every walked file outside git."""
    from repowise.core.analysis.decisions.source_files import tracked_files
    from repowise.core.fs_walk import walk_repo

    tracked = tracked_files(root)
    if tracked is not None:
        paths = (p.relative_to(root) for p in tracked if p.is_relative_to(root))
    else:
        paths = (
            (Path(d) / f).relative_to(root) for d, _dirs, names in walk_repo(root) for f in names
        )
    return sorted(p.as_posix() for p in paths)


@click.group("components")
def components_group() -> None:
    """Validate and list the components code facts roll up to."""


@components_group.command("check")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option()
def components_check(repo: str | None, fmt: str) -> None:
    """Validate .repowise/components.yaml and catalog-info.yaml; exit 1 on errors."""
    from repowise.core.components import check_catalog, read_catalog

    root = _repo_root(repo)
    catalog = read_catalog(root)
    issues = check_catalog(catalog, _tracked(root))
    errors = sum(1 for i in issues if i.severity == "error")
    if fmt == "json":
        emit_json(
            {
                "sources": catalog.sources,
                "components": len(catalog.components),
                "systems": len(catalog.systems),
                "groups": len(catalog.groups),
                "errors": errors,
                "issues": [i.to_dict() for i in issues],
            }
        )
    elif not catalog.sources:
        console.print(
            "No catalog file found (.repowise/components.yaml or catalog-info.yaml); "
            "components are derived from the index."
        )
    else:
        from rich.markup import escape

        console.print(
            f"Read {', '.join(catalog.sources)}: {len(catalog.components)} component(s), "
            f"{len(catalog.systems)} system(s), {len(catalog.groups)} group(s)."
        )
        for i in issues:
            colour = "red" if i.severity == "error" else "yellow"
            where = " ".join(x for x in (i.source, i.entity) if x)
            console.print(f"[{colour}]{i.severity}[/{colour}] {escape(where)}: {escape(i.message)}")
        if not issues:
            console.print("[green]No issues.[/green]")
    if errors:
        raise SystemExit(1)


async def _list(root: Path) -> dict[str, Any]:
    from repowise.core.components import read_catalog, resolve_components
    from repowise.core.components.resolve import derive_from_directories
    from repowise.core.components.store import load_repo_components, workspace_services

    async with repo_index_session(root) as opened:
        if opened is not None:
            session, repo_id = opened
            found = await load_repo_components(
                session, repo_id, root, services=workspace_services(root)
            )
            return {"indexed": True, "catalog": found.catalog, "resolved": found.resolved}
    files = _tracked(root)
    catalog = read_catalog(root)
    resolved = resolve_components(catalog.components, derive_from_directories(files), files)
    return {"indexed": False, "catalog": catalog, "resolved": resolved}


@components_group.command("list")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option()
def components_list(repo: str | None, fmt: str) -> None:
    """List declared and derived components with their file counts."""
    root = _repo_root(repo)
    data = run_async(_list(root))
    resolved = data["resolved"]
    rows = [
        {
            "name": c.name,
            "source": c.source,
            "files": len(resolved.files[c.name]),
            **{
                k: v
                for k, v in (
                    ("owner", c.owner),
                    ("system", c.system),
                    ("criticality", c.criticality),
                    ("tags", list(c.tags)),
                )
                if v
            },
        }
        for c in resolved.components
    ]
    if fmt == "json":
        emit_json(
            {
                "components": rows,
                "unassigned_files": len(resolved.unassigned),
                "catalog_errors": len(data["catalog"].errors),
            }
        )
        return
    from rich.markup import escape
    from rich.table import Table

    table = Table("Component", "Source", "Files", "Owner", "Criticality", "Tags")
    for r in rows:
        table.add_row(
            escape(r["name"]),
            r["source"],
            str(r["files"]),
            escape(r.get("owner") or ""),
            r.get("criticality") or "",
            escape(", ".join(r.get("tags") or [])),
        )
    console.print(table)
    if resolved.unassigned:
        console.print(f"[dim]{len(resolved.unassigned)} file(s) belong to no component.[/dim]")
    if not data["indexed"]:
        console.print("[dim]No index found: derived components are top-level directories.[/dim]")
    if data["catalog"].errors:
        console.print("[yellow]The catalog has errors; run `repowise components check`.[/yellow]")


async def _ownership(root: Path) -> dict[str, Any]:
    from repowise.core.components.ownership import Activity, assess_component
    from repowise.core.components.store import (
        component_ownership,
        component_rollups,
        load_repo_components,
        workspace_services,
    )
    from repowise.core.ownership.directory import team_directories

    async with repo_index_session(root) as opened:
        if opened is not None:
            session, repo_id = opened
            found = await load_repo_components(
                session, repo_id, root, services=workspace_services(root)
            )
            files = found.resolved.files
            rollups = await component_rollups(
                session, repo_id, {n: p for n, p in files.items() if p}, found.test_files
            )
            comps = [(c, files[c.name]) for c in found.resolved.components]
            blocks, issues = await component_ownership(
                session, repo_id, root, comps, {n: r["bus_factor"] for n, r in rollups.items()}
            )
            return {"indexed": True, "components": comps, "blocks": blocks, "issues": issues}
    data = await _list(root)
    declared = team_directories.get().load(root)
    resolved = data["resolved"]
    comps = [(c, resolved.files[c.name]) for c in resolved.components]
    blocks = {c.name: assess_component(c, p, declared, Activity()) for c, p in comps}
    return {"indexed": False, "components": comps, "blocks": blocks, "issues": declared.issues}


@components_group.command("ownership")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option()
def components_ownership(repo: str | None, fmt: str) -> None:
    """Compare declared owners with recent activity per component."""
    root = _repo_root(repo)
    data = run_async(_ownership(root))
    blocks = data["blocks"]
    if fmt == "json":
        out = {
            "indexed": data["indexed"],
            "components": [{"name": c.name, **blocks[c.name]} for c, _ in data["components"]],
        }
        if data["issues"]:
            out["directory_issues"] = data["issues"]
        emit_json(out)
        return
    from rich.markup import escape

    for issue in data["issues"]:
        console.print(f"[yellow]warning[/yellow] {escape(issue)}")

    findings = [(c.name, f) for c, _ in data["components"] for f in blocks[c.name]["findings"]]
    for name, f in findings:
        console.print(f"[yellow]{f['kind']}[/yellow] {escape(name)}: {escape(f['message'])}")
    if not findings:
        console.print("No ownership findings.")
    # What the check could see, so no findings is not read as all owners active.
    owned = sum(1 for b in blocks.values() if b.get("files_with_declared_owner"))
    unmatched = sum(
        1
        for b in blocks.values()
        if (b.get("declared_individuals") or {}).get("unknown")
        or any(t.get("activity") == "unknown" for t in b.get("declared_teams", ()))
    )
    line = f"{owned} of {len(blocks)} component(s) have a declared owner."
    if data["indexed"] and unmatched:
        line += (
            f" In {unmatched}, a declared owner matches no commit author, so that owner's"
            " activity is unknown, not inactive."
        )
    console.print(f"[dim]{line}[/dim]")
    if not data["indexed"]:
        console.print(
            "[dim]No index found: recent activity is unknown, "
            "so only declared facts are checked.[/dim]"
        )
