"""``repowise packages`` - where third-party packages are used, from the index.

``where`` lists the files importing a package with their component, declared
owner teams and reachability. ``export --format cyclonedx`` writes that as a
CycloneDX 1.6 document (``evidence.occurrences``); it records usage and is not
an SBOM. ``import`` reads a Syft, SPDX, CycloneDX or OSV JSON file the user
supplies, joins its packages to usage by name and stores it. ``vulns`` lists
each stored advisory with how close production code comes to it, and
``check`` says whether the stored document lists an advisory for a package at
a version. Nothing is fetched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_command_target, run_async
from repowise.cli.output import emit_json, format_option

_NO_INDEX = "No index found for this repository; run `repowise init` first."
_NO_DOCUMENT = (
    "No advisory document imported; run `repowise packages import <file>` "
    "(OSV-Scanner JSON works best: its versions are already resolved)."
)


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
    """Join a Syft, SPDX, CycloneDX or OSV JSON FILE to where its packages are used.

    The document is stored as the repository's advisory data, replacing the last one.
    """
    from repowise.core.inventory.advisories import join_usage

    root = _repo_root(repo)
    try:
        result = run_async(_with_index(root, lambda s, rid: join_usage(s, rid, root, file, store=True)))
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
                escape(", ".join(_advisory_cell(a) for a in row.get("advisories", []))),
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
    clear = sum(len(row.get("not_affected", ())) for row in result["matched"])
    if clear:
        console.print(f"{clear} advisory match(es) left out: the pinned version is outside the range.")
    console.print(f"[dim]{escape(result['method'])}.[/dim]")


def _advisory_cell(a: dict[str, Any]) -> str:
    """``GHSA-x (high, fixed in 2.32.0)``; ``?`` marks one no version could decide."""
    notes = [n for n in (a.get("severity"), a.get("fix") and f"fixed in {a['fix']}") if n]
    text = f"{a['id']}{'?' if a.get('affected') is None else ''}"
    return f"{text} ({', '.join(notes)})" if notes else text


def _tier_choices() -> list[str]:
    from repowise.core.inventory.vuln_reach import TIERS

    return list(TIERS)


@packages_group.command("vulns")
@click.option(
    "--tier",
    "min_tier",
    type=click.Choice(_tier_choices()),
    default=None,
    help="List only advisories at this tier or stronger.",
)
@click.option(
    "--fail-on",
    type=click.Choice([*_tier_choices(), "never"]),
    default="package_called",
    show_default=True,
    help="Exit 1 when an advisory sits at this tier or stronger.",
)
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option(choices=("text", "json", "sarif"), default="text")
def packages_vulns(min_tier: str | None, fail_on: str, repo: str | None, fmt: str) -> None:
    """Each stored advisory, tiered by how close production code comes to it.

    Tiers, strongest first: reachable (production code calls an affected
    symbol), package_called (production code calls into the package),
    imported, indirect_unknown, test_or_dev_only, not_imported. Exits 2 when
    no advisory document was imported.
    """
    from repowise.core.inventory.advisory_tiers import tier_advisories
    from repowise.core.inventory.vuln_reach import TIERS

    root = _repo_root(repo)
    rows = run_async(_with_index(root, lambda s, rid: tier_advisories(s, rid, root)))
    if rows is None:
        click.echo(_NO_DOCUMENT, err=True)
        raise SystemExit(2)
    if min_tier is not None:
        rows = [r for r in rows if TIERS.index(r.tier.tier) <= TIERS.index(min_tier)]
    failing = fail_on != "never" and any(
        TIERS.index(r.tier.tier) <= TIERS.index(fail_on) for r in rows
    )
    if fmt == "json":
        emit_json({"advisories": [r.as_dict() for r in rows], "fail_on": fail_on, "failed": failing})
    elif fmt == "sarif":
        emit_json(_vulns_sarif(rows))
    else:
        _print_vulns(rows)
    if failing:
        raise SystemExit(1)


def _print_vulns(rows: list[Any]) -> None:
    from collections import Counter

    from rich.markup import escape
    from rich.table import Table

    if not rows:
        console.print("No stored advisory applies to the versions in use.")
        return
    table = Table("Tier", "Package", "Advisory", "Where")
    for r in rows:
        notes = [n for n in (r.advisory.severity, r.fix and f"fixed in {r.fix}") if n]
        advisory = f"{r.advisory.id}{'?' if r.affected is None else ''}"
        sites = r.sites()
        where = ", ".join(sites[:2]) + (f" (+{len(sites) - 2})" if len(sites) > 2 else "")
        table.add_row(
            r.tier.tier,
            escape(f"{r.ecosystem or '?'}:{r.package}"),
            escape(f"{advisory} ({', '.join(notes)})" if notes else advisory),
            escape(where),
        )
    console.print(table)
    counts = Counter(r.tier.tier for r in rows)
    console.print(
        f"{len(rows)} advisory(ies): " + ", ".join(f"{n} {t}" for t, n in counts.items()) + "."
    )
    if any(r.affected is None for r in rows):
        console.print("[dim]? = no exact version to check the range against.[/dim]")


_SARIF_LEVEL = {"reachable": "error", "package_called": "warning"}


def _vulns_sarif(rows: list[Any]) -> dict[str, Any]:
    """One result per advisory with an evidence site, located at its first site."""
    from repowise.cli import __version__
    from repowise.core.ci import sarif

    rules: dict[str, dict] = {}
    results = []
    for r in rows:
        if not r.tier.sites:
            continue
        rule_id = f"advisory/{r.tier.tier}"
        rules.setdefault(
            rule_id,
            sarif.rule(
                rule_id,
                r.tier.tier,
                f"Package advisory, tier {r.tier.tier}",
                "A package advisory and the closest production code comes to it.",
            ),
        )
        site = r.tier.sites[0]
        fix = f"; fixed in {r.fix}" if r.fix else ""
        results.append(
            sarif.result(
                rule_id,
                _SARIF_LEVEL.get(r.tier.tier, "note"),
                f"{r.advisory.id} in {r.package} ({r.tier.tier}){fix}",
                site.path,
                site.line,
                "repowiseAdvisory/v1",
                f"{r.ecosystem}:{r.package}:{r.advisory.id}",
                {"tier": r.tier.tier, "severity": r.advisory.severity},
            )
        )
    return sarif.run("repowise", __version__, list(rules.values()), results)


@packages_group.command("check")
@click.argument("target")
@click.option("--path", "repo", default=None, help="Repo path (defaults to cwd).")
@format_option(choices=("text", "json"), default="text")
def packages_check(target: str, repo: str | None, fmt: str) -> None:
    """Whether the stored advisory document lists an advisory for TARGET.

    TARGET is ``[ecosystem:]name[@version]``, e.g. ``pypi:requests@2.31.0``.
    Answers from the last imported document only; exits 1 when an advisory applies.
    """
    from repowise.core.inventory.advisories import load_advisories
    from repowise.core.inventory.advisory_tiers import check_package, split_version
    from repowise.core.inventory.usage import package_usage, parse_query

    root = _repo_root(repo)
    query, version = split_version(target)
    name, eco = parse_query(query)

    async def answer(session: Any, rid: str) -> tuple[Any, dict[str, Any]]:
        stored = await load_advisories(session, rid)
        usage = await package_usage(session, rid, root, query)
        return check_package(stored, eco, name, version), usage

    check, usage = run_async(_with_index(root, answer))
    doc = check.as_dict()
    doc["declared"] = [
        {k: d[k] for k in ("package_key", "version", "declared_in")} for d in usage["declared"]
    ]
    doc["files"] = usage["summary"]["files"]
    if fmt == "json":
        emit_json(doc)
    else:
        _print_check(doc)
    if check.verdict == "has_advisories":
        raise SystemExit(1)


def _print_check(doc: dict[str, Any]) -> None:
    from rich.markup import escape

    label = f"{doc.get('ecosystem') or '?'}:{doc['package']}" + (
        f"@{doc['version']}" if doc.get("version") else ""
    )
    console.print(f"{escape(label)}: {doc['verdict']}")
    for a in doc.get("advisories", []):
        console.print(f"  {escape(_advisory_cell(a))}")
    for d in doc["declared"]:
        version = f" {d['version']}" if d["version"] else ""
        console.print(f"Declared{escape(version)} in {escape(d['declared_in'])}")
    if doc["files"]:
        console.print(f"Imported in {doc['files']} file(s).")
    if doc["verdict"] == "unknown_no_source":
        console.print(
            f"[dim]No stored advisory document names it. {escape(_NO_DOCUMENT)}[/dim]"
        )
