"""``repowise plugins``: list loaded plugins and the implementation chosen per seam; ``check`` runs the seam contracts."""

from __future__ import annotations

import importlib
from dataclasses import asdict

import click

from repowise.cli.output import emit_json, format_option

#: Modules that define a seam or register its built-in implementations. A seam
#: is only listed once its module is imported; a guard test keeps this complete.
BUILTIN_SEAM_MODULES = (
    "repowise.core.changes.outcomes",
    "repowise.core.changes.source",
    "repowise.core.events.sinks",
    "repowise.core.evidence.profile_formats",
    "repowise.core.evidence.sources",
    "repowise.core.ingestion.document_extractors",
    "repowise.core.inventory.advisories",
    "repowise.core.ownership.directory",
    "repowise.core.pipeline.index_persistence",
    "repowise.core.report.first_look.sections",
    "repowise.core.report.sections",
    "repowise.core.report.render",
    "repowise.core.suspects",
    "repowise.server.auth",
    "repowise.server.chat_session",
    "repowise.server.chat_views",
    "repowise.server.chat_datasets",
)


def _import_builtin_seams() -> None:
    for name in BUILTIN_SEAM_MODULES:
        try:
            module = importlib.import_module(name)
            if name == "repowise.server.chat_datasets":
                module.dataset_names()  # imports the built-in dataset families
        except ModuleNotFoundError as exc:
            # Only a missing third-party package (server extras not installed)
            # leaves a seam unlisted; a broken repowise import must surface.
            missing = exc.name or ""
            if missing == "repowise" or missing.startswith("repowise."):
                raise


def _applied_here() -> dict[tuple[str, str], str] | None:
    """Applied storage revisions in the store here, read without creating it; None without one."""
    from pathlib import Path

    from sqlalchemy.exc import SQLAlchemyError

    from repowise.cli.helpers import get_db_url_for_repo, run_async
    from repowise.core.persistence.database import create_engine, has_db_store
    from repowise.core.registry.storage_migrations import applied_revisions

    root = Path.cwd()
    if not has_db_store(root):
        return None

    async def read() -> dict[tuple[str, str], str]:
        engine = create_engine(get_db_url_for_repo(root))
        try:
            async with engine.connect() as conn:
                return await applied_revisions(conn)
        finally:
            await engine.dispose()

    try:
        return run_async(read())
    except (SQLAlchemyError, OSError):
        return None


def _plugin_rows(records: list) -> list[dict]:
    """Each record as a dict, with its storage revisions applied and pending here."""
    from repowise.core.registry.storage_migrations import storage_migrations

    revisions = storage_migrations.revisions()
    found = _applied_here() if revisions else None
    rows = []
    for record in records:
        row = asdict(record)
        applied, pending, changed = [], [], []
        for m in revisions:
            if storage_migrations.owner(m.namespace) is not record:
                continue
            key = f"{m.namespace}:{m.revision}"
            recorded = (found or {}).get((m.namespace, m.revision))
            if recorded is None:
                pending.append(key)
            elif recorded == m.checksum:
                applied.append(key)
            else:
                changed.append(key)
        row["storage"] = {"applied": applied, "pending": pending}
        loaded = row["status"] == "loaded"
        if loaded and changed:  # every open of this store raises
            row["status"] = "failed"
            row["error"] = f"storage revision {changed[0]} changed after it was applied here"
        elif loaded and pending and found is not None:  # with no store, nothing is unready
            row["status"] = "pending"
            row["error"] = f"storage revision {pending[0]} not applied to this store"
        rows.append(row)
    return rows


@click.group("plugins", invoke_without_command=True)
@format_option()
@click.pass_context
def plugins_command(ctx: click.Context, fmt: str) -> None:
    """List loaded plugins and the implementation each seam uses."""
    if ctx.invoked_subcommand is None:
        _list_plugins(fmt)


def _list_plugins(fmt: str) -> None:
    from rich.markup import escape

    from repowise.cli.helpers import console
    from repowise.core.registry.plugins import (
        ENTRY_POINT_GROUP,
        ENV_VAR,
        PLUGIN_API_VERSION,
        load_plugins,
        seams,
    )

    _import_builtin_seams()
    rows = _plugin_rows(load_plugins())
    from repowise.core.ingestion.document_extractors import document_extractor_claims

    seam_rows = {
        name: {"default": registry.default, "implementations": registry.names}
        for name, registry in sorted(seams().items())
    }
    extractor_claims = document_extractor_claims()
    if fmt == "json":
        emit_json(
            {
                "api_version": PLUGIN_API_VERSION,
                "plugins": rows,
                "seams": seam_rows,
                "document_extractors": extractor_claims,
            }
        )
        return

    console.print(f"plugin API version {PLUGIN_API_VERSION}")
    if not rows:
        console.print(
            f"No plugins loaded. Install one that declares a [bold]{ENTRY_POINT_GROUP}[/bold] "
            f"entry point, or list modules in [bold]{ENV_VAR}[/bold]."
        )
    for r in rows:
        _print_plugin_row(r)
    for name, row in seam_rows.items():
        others = ", ".join(row["implementations"])
        console.print(f"seam [bold]{escape(name)}[/bold]: {escape(str(row['default']))}  ({escape(others)})")
    for extension, name in sorted(extractor_claims.items()):
        console.print(
            f"document extractor [bold]{escape(extension)}[/bold]: {escape(name)}"
        )


def _print_plugin_row(r: dict) -> None:
    from rich.markup import escape

    from repowise.cli.helpers import console

    colors = {"loaded": "green", "failed": "red", "skipped": "yellow", "pending": "yellow"}
    color = colors.get(r["status"], "red")
    status = f"[{color}]{r['status']}[/{color}]"
    if r["error"]:
        status += f": {escape(r['error'])}"
    console.print(f"[bold]{escape(r['name'])}[/bold]  {r['source']}  {escape(r['target'])}  {status}")
    if r["seams"]:
        console.print(f"  registered: {escape(', '.join(r['seams']))}")
    if r["storage"]["applied"] or r["storage"]["pending"]:
        applied = ", ".join(r["storage"]["applied"]) or "none"
        pending = ", ".join(r["storage"]["pending"]) or "none"
        console.print(escape(f"  storage: applied {applied}; pending {pending}"))


@plugins_command.command("check")
@format_option()
def check_command(fmt: str) -> None:
    """Check every loaded plugin against the contract of each seam it registered.

    Exits 1 when a plugin failed to load or an implementation breaks a contract.
    """
    from rich.markup import escape

    from repowise.cli.helpers import console
    from repowise.core.plugin_testing import check_record
    from repowise.core.registry.plugins import PLUGIN_API_VERSION, load_plugins

    _import_builtin_seams()
    checks = [c for record in load_plugins() for c in check_record(record)]
    failed = any(c.status == "fail" for c in checks)
    if fmt == "json":
        emit_json(
            {
                "api_version": PLUGIN_API_VERSION,
                "ok": not failed,
                "checks": [asdict(c) for c in checks],
            }
        )
    else:
        if not checks:
            console.print("No plugins loaded, nothing to check.")
        colors = {"pass": "green", "fail": "red", "skipped": "dim"}
        for c in checks:
            color = colors[c.status]
            console.print(
                f"[{color}]{c.status}[/{color}]  {escape(c.plugin)}  {escape(c.kind)}:{escape(c.name)}"
            )
            for f in c.failures:
                console.print(f"  {escape(f.rule)}. Fix: {escape(f.fix)}")
    if failed:
        raise SystemExit(1)
