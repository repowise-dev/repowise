"""``repowise telemetry`` — inspect and control anonymous usage telemetry."""

from __future__ import annotations

import click

from repowise.cli.helpers import console


@click.group(name="telemetry")
def telemetry_group() -> None:
    """Inspect and control anonymous, opt-out usage telemetry."""


@telemetry_group.command(name="status")
def telemetry_status() -> None:
    """Show whether telemetry is enabled and why."""
    from repowise.cli.platform import telemetry

    for line in telemetry.status_lines():
        console.print(line)


@telemetry_group.command(name="enable")
def telemetry_enable() -> None:
    """Enable anonymous usage telemetry."""
    from repowise.cli.platform import settings

    settings.set_enabled(True)
    console.print("[green]✓[/green] Telemetry enabled. Thank you for helping improve Repowise.")


@telemetry_group.command(name="disable")
def telemetry_disable() -> None:
    """Disable all usage telemetry."""
    from repowise.cli.platform import settings

    settings.set_enabled(False)
    console.print("[green]✓[/green] Telemetry disabled. No usage data will be sent.")


telemetry_command = telemetry_group
