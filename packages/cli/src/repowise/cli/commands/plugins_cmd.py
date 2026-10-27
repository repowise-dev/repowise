"""``repowise plugins`` — list loaded plugins and the implementation chosen per seam."""

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
    "repowise.core.inventory.advisories",
    "repowise.core.ownership.directory",
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


@click.command("plugins")
@format_option()
def plugins_command(fmt: str) -> None:
    """List loaded plugins and the implementation each seam uses."""
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
    records = load_plugins()
    seam_rows = {
        name: {"default": registry.default, "implementations": registry.names}
        for name, registry in sorted(seams().items())
    }
    if fmt == "json":
        emit_json(
            {
                "api_version": PLUGIN_API_VERSION,
                "plugins": [asdict(r) for r in records],
                "seams": seam_rows,
            }
        )
        return

    console.print(f"plugin API version {PLUGIN_API_VERSION}")
    if not records:
        console.print(
            f"No plugins loaded. Install one that declares a [bold]{ENTRY_POINT_GROUP}[/bold] "
            f"entry point, or list modules in [bold]{ENV_VAR}[/bold]."
        )
    colors = {"loaded": "green", "failed": "red", "skipped": "yellow"}
    for r in records:
        color = colors.get(r.status, "red")
        status = f"[{color}]{r.status}[/{color}]"
        if r.error:
            status += f": {escape(r.error)}"
        console.print(f"[bold]{escape(r.name)}[/bold]  {r.source}  {escape(r.target)}  {status}")
        if r.seams:
            console.print(f"  registered: {escape(', '.join(r.seams))}")
    for name, row in seam_rows.items():
        others = ", ".join(row["implementations"])
        console.print(f"seam [bold]{escape(name)}[/bold]: {escape(str(row['default']))}  ({escape(others)})")
