"""``repowise plugins`` — list loaded plugins and the implementation chosen per seam."""

from __future__ import annotations

from dataclasses import asdict

import click

from repowise.cli.output import emit_json, format_option


@click.command("plugins")
@format_option()
def plugins_command(fmt: str) -> None:
    """List loaded plugins and the implementation each seam uses."""
    from rich.markup import escape

    from repowise.cli.helpers import console
    from repowise.core.registry.plugins import ENTRY_POINT_GROUP, ENV_VAR, load_plugins, seams

    records = load_plugins()
    seam_rows = {
        name: {"default": registry.default, "implementations": registry.names}
        for name, registry in sorted(seams().items())
    }
    if fmt == "json":
        emit_json({"plugins": [asdict(r) for r in records], "seams": seam_rows})
        return

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
