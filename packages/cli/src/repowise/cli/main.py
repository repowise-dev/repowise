"""repowise CLI — codebase intelligence for developers and AI."""

from __future__ import annotations

import click

from repowise.cli import __version__
from repowise.cli._instrumented_group import InstrumentedGroup
from repowise.cli.commands.augment_cmd import augment_command
from repowise.cli.commands.claude_md_cmd import claude_md_command
from repowise.cli.commands.corrections_cmd import corrections_command
from repowise.cli.commands.costs_cmd import costs_command
from repowise.cli.commands.dead_code_cmd import dead_code_command
from repowise.cli.commands.decision_cmd import decision_group
from repowise.cli.commands.delete_cmd import delete_command
from repowise.cli.commands.distill_cmd import distill_command
from repowise.cli.commands.doctor_cmd import doctor_command
from repowise.cli.commands.expand_cmd import expand_command
from repowise.cli.commands.export_cmd import export_command
from repowise.cli.commands.health_cmd import health_command
from repowise.cli.commands.hook_cmd import hook_group
from repowise.cli.commands.init_cmd import init_command
from repowise.cli.commands.mcp_cmd import mcp_command
from repowise.cli.commands.reindex_cmd import reindex_command
from repowise.cli.commands.restyle_cmd import restyle_command, wiki_styles_command
from repowise.cli.commands.risk_cmd import risk_command
from repowise.cli.commands.saved_cmd import saved_command
from repowise.cli.commands.search_cmd import search_command
from repowise.cli.commands.serve_cmd import serve_command
from repowise.cli.commands.status_cmd import status_command
from repowise.cli.commands.telemetry_cmd import telemetry_command
from repowise.cli.commands.update_cmd import update_command
from repowise.cli.commands.watch_cmd import watch_command
from repowise.cli.commands.whats_new_cmd import whats_new_command
from repowise.cli.commands.workspace_cmd import workspace_group
from repowise.core.registry import cli_registry, register_command


@click.group(cls=InstrumentedGroup)
@click.version_option(version=__version__, prog_name="repowise")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """repowise -- codebase intelligence for developers and AI."""
    # Self-heal: migrate any legacy `repowise augment` Claude Code hooks
    # to the import-isolated `repowise-augment` console script. Cheap,
    # silent, idempotent — only writes when there is something to change.
    # Skipped when invoked as the augment subcommand itself (hook hot path) —
    # `augment_hook.main` handles that case.
    if ctx.invoked_subcommand != "augment":
        try:
            from repowise.cli.editor_integrations.claude_config import migrate_claude_code_hooks

            migrate_claude_code_hooks()
        except Exception:
            pass


# Register OSS commands through the shared registry so third-party
# packages can extend the CLI without monkey-patching the root group.
# Order is preserved by the registry, so `repowise --help` reads the same.
register_command(augment_command)
register_command(init_command)
register_command(delete_command)
register_command(claude_md_command)
register_command(costs_command)
register_command(update_command)
register_command(dead_code_command)
register_command(health_command)
register_command(risk_command)
register_command(decision_group)
register_command(search_command)
register_command(distill_command)
register_command(expand_command)
register_command(saved_command)
register_command(corrections_command)
register_command(export_command)
register_command(hook_group)
register_command(status_command)
register_command(doctor_command)
register_command(watch_command)
register_command(serve_command)
register_command(mcp_command)
register_command(reindex_command)
register_command(restyle_command)
register_command(wiki_styles_command)
register_command(whats_new_command)
register_command(telemetry_command)
register_command(workspace_group)

cli_registry.apply(cli)
