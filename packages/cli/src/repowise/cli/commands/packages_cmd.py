"""``repowise packages`` - where third-party packages are used, from the index.

``where`` lists the files importing a package with their component, declared
owner teams and reachability. ``export --format cyclonedx`` writes that as a
CycloneDX 1.6 document (``evidence.occurrences``); it records usage and is not
an SBOM. ``import`` reads a Syft, SPDX, CycloneDX or OSV JSON file the user
supplies and joins its packages to usage by name. Nothing is fetched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option

_NO_INDEX = "No index found for this repository; run `repowise init` first."


def _repo_root(path: str | None) -> Path:
    target = resolve_command_target(path=path, no_workspace_flag=True)
    assert target.repo_path is not None
    return Path(target.repo_path).resolve()


async def _with_index(root: Path, fn: Any) -> Any:
    async with repo_index_session(root) as opened:
        if opened is None:
            raise click.ClickException(_NO_INDEX)
        session, repo_id = opened
        return await fn(session, repo_id)


@click.group("packages")
def packages_group() -> None:
    """Where third-party packages are used (import sites, components, owners)."""


def _owners(site: dict[str, Any]) -> str:
    """Declared teams by name; individual and unresolved owners only by count."""
    counts = site.get("owner_counts", {})
    parts = [
        *site["owners"],
        *(f"{n} {'individual(s)' if kind == 'individual' else kind}" for kind, n in counts.items()),
    ]
    return ", ".join(parts)


def _reach(site: dict[str, Any]) -> str:
    r = site["reachability"]
    return r["state"] if r["state"] != "unknown" else f"unknown ({r['basis']})"


@packages_group.command("where")
@click.argument("name")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option(choices=("text", "json"), default="text")
def packages_where(name: str, repo: str | None, fmt: str) -> None:
    """Files that import NAME (a package name or ecosystem:name)."""
    from repowise.core.inventory.usage import package_usage

    root = _repo_root(repo)
    doc = run_async(_with_index(root, lambda s, rid: package_usage(s, rid, root, name)))
    if fmt == "json":
        emit_json(doc)
        return
    from rich.markup import escape
    from rich.table import Table

    for decl in doc["declared"]:
        version = f" {decl['version']}" if decl["version"] else ""
        dev = " (dev)" if decl["dev"] else ""
        console.print(
            f"Declared {escape(decl['package_key'])}{escape(version)} "
            f"in {escape(decl['declared_in'])}{dev}"
        )
    if doc.get("note"):
        console.print(f"[dim]{escape(doc['note'])}[/dim]")
    if not doc["sites"]:
        return
    # The path is the answer, so it folds onto a second line rather than being
    # cut; the other columns give way first.
    inferred = any(s["mapping"] == "inferred" for s in doc["sites"])
    table = Table()
    table.add_column("File", overflow="fold", ratio=3)
    table.add_column("Imports", overflow="ellipsis", no_wrap=True, max_width=24)
    table.add_column("Component", overflow="fold", max_width=20)
    table.add_column("Owners", overflow="fold", max_width=24)
    table.add_column("Reachability", overflow="fold", max_width=24)
    if inferred:
        table.add_column("Mapping")
    for s in doc["sites"]:
        where = f"{s['path']}:{s['line']}" if "line" in s else s["path"]
        row = [
            escape(where),
            escape(", ".join(s["symbols"] or [m["name"] for m in s["modules"]])),
            escape(s.get("component", "")),
            escape(_owners(s)),
            _reach(s),
        ]
        table.add_row(*row, *([s["mapping"]] if inferred else []))
    console.print(table)
    summary = doc["summary"]
    reach = ", ".join(f"{n} {k}" for k, n in summary["reachability"].items() if n)
    console.print(
        f"{summary['files']} file(s) in {summary['components']} component(s): {reach}."
    )


@packages_group.command("export")
@format_option(choices=("cyclonedx",), default="cyclonedx", help="CycloneDX 1.6 JSON.")
@click.option(
    "--output",
    "-o",
    type=click.Path(dir_okay=False),
    default=None,
    help="Write to a file instead of stdout.",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
def packages_export(fmt: str, output: str | None, repo: str | None) -> None:
    """Declared packages and where they are imported, as CycloneDX 1.6 JSON (not an SBOM)."""
    import json

    from repowise.core.inventory.cyclonedx import cyclonedx_bom

    root = _repo_root(repo)
    bom = run_async(_with_index(root, lambda s, rid: cyclonedx_bom(s, rid, root, root.name)))
    text = json.dumps(bom, indent=2)
    if output:
        Path(output).write_text(text + "\n", encoding="utf-8")
        console.print(f"Wrote {len(bom['components'])} component(s) to {output}.")
    else:
        click.echo(text)


@packages_group.command("import")
@click.argument("file", type=click.Path(exists=True, dir_okay=False))
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option(choices=("text", "json"), default="text")
def packages_import(file: str, repo: str | None, fmt: str) -> None:
    """Join a Syft, SPDX, CycloneDX or OSV JSON FILE to where its packages are used."""
    from repowise.core.inventory.advisories import join_usage

    root = _repo_root(repo)
    try:
        result = run_async(_with_index(root, lambda s, rid: join_usage(s, rid, root, file)))
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    if fmt == "json":
        emit_json(result)
        return
    from rich.markup import escape
    from rich.table import Table

    console.print(
        f"Read {result['mentions']} package(s) from a {result['format']} file; "
        f"{len(result['matched'])} declared or imported here, {result['not_found']} not found."
    )
    if result["matched"]:
        table = Table("Package", "Advisories", "Files", "Reachable", "Unknown", "Components")
        for row in result["matched"]:
            reach = row["summary"]["reachability"]
            table.add_row(
                escape(f"{row['ecosystem'] or '?'}:{row['name']}"),
                escape(", ".join(a["id"] for a in row.get("advisories", []))),
                str(row["summary"]["files"]),
                str(reach["reachable"]),
                str(reach["unknown"]),
                escape(", ".join(row["components"])),
            )
        console.print(table)
    for row in result["not_found_with_advisories"]:
        ids = ", ".join(a["id"] for a in row["advisories"])
        console.print(
            f"[dim]{escape(row['name'])} ({escape(ids)}): not declared or imported here.[/dim]"
        )
    console.print(f"[dim]{escape(result['method'])}.[/dim]")
