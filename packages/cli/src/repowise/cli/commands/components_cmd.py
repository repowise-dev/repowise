"""``repowise components`` - validate and list the repository's components.

``check`` reads ``.repowise/components.yaml`` and ``catalog-info.yaml`` and
validates them against the files git tracks; it needs no index and exits
non-zero on any error, so it fits a CI step. ``list`` shows declared and
derived components with their file counts, from the index when there is one.
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
