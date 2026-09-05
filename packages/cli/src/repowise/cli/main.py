"""repowise CLI — codebase intelligence for developers and AI."""

from __future__ import annotations

# Before anything else can pull in numpy: a BLAS runtime sizes its private
# per-thread workspace to the host's core count at import, and on a 32-core
# machine that is ~750 MB committed and never returned, for work this pipeline
# does single-threaded in under a second. See the module for the measurements
# (issue #1394). This has to precede every other repowise import — one of them
# reaching numpy first is exactly the bug.
from repowise.core.blas_threads import limit_blas_threads

limit_blas_threads()

# ruff: noqa: E402 — the BLAS pin above is only effective before these run.
import click

from repowise.cli import __version__
from repowise.cli._instrumented_group import InstrumentedGroup
from repowise.core.registry import cli_registry, register_lazy_command


@click.group(cls=InstrumentedGroup)
@click.version_option(version=__version__, prog_name="repowise")
@click.pass_context
def cli(ctx: click.Context) -> None:
    """repowise -- codebase intelligence for developers and AI."""
    # Self-heal: repair agent hook entries whose shape a past repowise version
    # wrote and a later one changed. Silent, idempotent, and now stamped, so it
    # costs one small read per invocation rather than two JSON config parses.
    # Skipped when invoked as the augment subcommand itself (hook hot path) —
    # `augment_hook.main` handles that case. See `self_heal` for why the call
    # site stays here.
    #
    # Also skipped for `uninstall`, where repairing agent hook entries is the
    # opposite of what the user asked for, and where the migration stamp lands
    # in `~/.repowise/` — the directory that command may be about to delete.
    # Without this, a second `uninstall --all` found and removed a stamp its own
    # startup had just written, and the command never converged.
    if ctx.invoked_subcommand not in ("augment", "uninstall"):
        try:
            from repowise.cli.self_heal import run_editor_migrations

            run_editor_migrations()
        except Exception:
            pass


_COMMANDS_PKG = "repowise.cli.commands"

#: ``command name -> "module:attr"``, resolved one at a time by
#: :class:`InstrumentedGroup`. Registering objects here instead of strings
#: is what used to import all ~35 command modules (1.1s) to dispatch one,
#: on every invocation including every agent hook fire and MCP start.
#:
#: The name on the left must equal the Click command's own name — it is
#: what `--help`, dispatch and shell completion see before the module is
#: imported. `tests/unit/cli/test_lazy_commands.py` asserts every pair.
_OSS_COMMANDS: tuple[tuple[str, str], ...] = (
    ("augment", "augment_cmd:augment_command"),
    ("init", "init_cmd:init_command"),
    ("delete", "delete_cmd:delete_command"),
    ("generate-claude-md", "claude_md_cmd:claude_md_command"),
    ("costs", "costs_cmd:costs_command"),
    ("update", "update_cmd:update_command"),
    ("generate", "generate_cmd:generate_command"),
    ("dead-code", "dead_code_cmd:dead_code_command"),
    ("health", "health_cmd:health_command"),
    ("risk", "risk_cmd:risk_command"),
    ("overlap", "overlap_cmd:overlap_command"),
    ("decision", "decision_cmd:decision_group"),
    ("coverage", "coverage_cmd:coverage_group"),
    ("impacted-tests", "impacted_tests_cmd:impacted_tests_command"),
    ("search", "search_cmd:search_command"),
    ("ask", "ask_cmd:ask_command"),
    ("context", "context_cmd:context_command"),
    ("symbol", "symbol_cmd:symbol_command"),
    ("why", "why_cmd:why_command"),
    ("distill", "distill_cmd:distill_command"),
    ("expand", "expand_cmd:expand_command"),
    ("saved", "saved_cmd:saved_command"),
    ("security", "security_cmd:security_command"),
    ("corrections", "corrections_cmd:corrections_command"),
    ("export", "export_cmd:export_command"),
    ("hook", "hook_cmd:hook_group"),
    ("agents", "agents_cmd:agents_group"),
    ("uninstall", "uninstall_cmd:uninstall_command"),
    ("status", "status_cmd:status_command"),
    ("doctor", "doctor_cmd:doctor_command"),
    ("watch", "watch_cmd:watch_command"),
    ("serve", "serve_cmd:serve_command"),
    ("mcp", "mcp_cmd:mcp_command"),
    ("reindex", "reindex_cmd:reindex_command"),
    ("restyle", "restyle_cmd:restyle_command"),
    ("wiki-styles", "restyle_cmd:wiki_styles_command"),
    ("whats-new", "whats_new_cmd:whats_new_command"),
    ("telemetry", "telemetry_cmd:telemetry_command"),
    ("login", "login_cmd:login_command"),
    ("logout", "login_cmd:logout_command"),
    ("whoami", "login_cmd:whoami_command"),
    ("workspace", "workspace_cmd:workspace_group"),
)

# Register OSS commands through the shared registry so third-party
# packages can extend the CLI without monkey-patching the root group.
# Order is preserved by the registry, so `repowise --help` reads the same.
for _name, _target in _OSS_COMMANDS:
    register_lazy_command(_name, f"{_COMMANDS_PKG}.{_target}")

cli_registry.apply(cli)


# ``python -m repowise.cli.main`` is the documented fallback when the console
# script is shadowed, so it has to actually run. Without this it imports the
# module, defines ``cli``, calls nothing, and exits 0 with no output — a silent
# no-op at exactly the moment someone is already debugging their install.
if __name__ == "__main__":
    cli()
