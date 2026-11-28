"""Ask once, then install an optional extra the user just chose to need.

The detection and the install itself live in :mod:`repowise.core.extras`; this
module is only the terminal side: one confirm, a spinner, and the outcome.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.markup import escape

from repowise.cli.ui.brand import OK, VALUE, WARN


def offer_extra(console: Console, extra: str, what: str, *, interactive: bool) -> bool:
    """True when *extra* is installed, asking to install it first when it is not.

    Without *interactive*, or when the command must not be run here (a source
    checkout, a system Python, no write access), the command is printed and
    nothing is installed.
    """
    from repowise.core import extras

    if extras.is_installed(extra):
        return True
    plan = extras.plan_install(extra)
    size = extras.EXTRAS[extra].size
    console.print(
        f"  {escape(what)} needs the [{VALUE}]{escape(f'repowise[{extra}]')}[/] extra ({size}), "
        "which is not installed."
    )
    if not interactive or not plan.runnable:
        why = f" ({plan.reason})" if plan.reason else ""
        console.print(f"  Install it with{escape(why)}: [bold]{escape(plan.command)}[/bold]")
        return False
    if not click.confirm(f"  Install it now with `{plan.command}`?", default=True):
        console.print(f"  [{WARN}]Not installed.[/] Later: [bold]{escape(plan.command)}[/bold]")
        return False
    try:
        with console.status(f"  Installing {extra}..."):
            extras.install(extra, plan=plan)
    except extras.ExtraInstallError as exc:
        console.print(f"  [{WARN}]Install failed:[/] {escape(str(exc))}")
        return False
    console.print(f"  [{OK}]✓[/] {extra} installed.")
    if plan.method == extras.UV_TOOL:
        # Added in place, so `uv tool upgrade` (which rebuilds from the
        # receipt) drops it; the receipt route keeps it.
        keep = f'uv tool install "repowise[{extra}]"'
        console.print(f"  [dim]To keep it across uv tool upgrades: {escape(keep)}[/dim]")
    return True
