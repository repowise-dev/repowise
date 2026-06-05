"""``repowise distill`` — run a command and emit a distilled rendering.

Executes the real command, captures its output, and prints the compact
errors-first rendering with an omission marker pointing at the stashed raw
output (``repowise expand <ref>`` round-trips it). The wrapped command's
exit code is always preserved, so this is a drop-in replacement in scripts
and agent tool calls alike.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import click

from repowise.cli.helpers import find_repowise_repo_root


@click.command(
    "distill",
    context_settings={"ignore_unknown_options": True},
    short_help="Run a command and print a compact, reversible rendering of its output.",
)
@click.argument("command", nargs=-1, required=True, type=click.UNPROCESSED)
def distill_command(command: tuple[str, ...]) -> None:
    """Run COMMAND and print a distilled rendering of its output.

    Examples:

        repowise distill git status

        repowise distill pytest tests/unit -x

        repowise distill npm run build

    Noise (pass parades, progress spam, hint boilerplate) is dropped; errors,
    failures, and summaries always survive. Dropped content is stored in
    .repowise/ and referenced by an inline ``[repowise#<ref>: ...]`` marker —
    restore it with ``repowise expand <ref>``. On any filter problem the raw
    output is printed unchanged. The command's exit code is preserved.
    """
    command_str = _render_command(command)
    # shell=True on purpose: the user's own command may be a shell builtin
    # or a .cmd shim (npm on Windows); we execute exactly what they typed.
    proc = subprocess.run(
        command_str,
        shell=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    output = proc.stdout
    if proc.stderr:
        output = output + ("\n" if output and not output.endswith("\n") else "") + proc.stderr

    text = _distill_or_raw(output, command_str, proc.returncode)
    _echo_safely(text)
    sys.exit(proc.returncode)


def _render_command(tokens: tuple[str, ...]) -> str:
    """Rejoin click's pre-split tokens into one shell command string."""
    if len(tokens) == 1:
        return tokens[0]
    if sys.platform == "win32":
        return subprocess.list2cmdline(tokens)
    import shlex

    return shlex.join(tokens)


def _distill_or_raw(output: str, command_str: str, exit_code: int) -> str:
    """Distill *output*, honoring the repo's ``distill:`` config block."""
    try:
        repo_root = find_repowise_repo_root()
        enabled, disabled, (ttl_days, max_mb) = _load_distill_config(repo_root)
        if not enabled:
            return output
        from repowise.core.distill import distill_output
        from repowise.core.distill.store import OmissionStore, default_store_path

        # Open the store here (rather than letting the engine) so the
        # configured TTL / size cap apply to this write's opportunistic prune.
        store = OmissionStore(
            default_store_path(repo_root or Path.cwd()), ttl_days=ttl_days, max_mb=max_mb
        )
        try:
            return distill_output(
                output,
                command=command_str,
                exit_code=exit_code,
                source="cli",
                store=store,
                disabled_filters=disabled,
            ).text
        finally:
            store.close()
    except Exception:
        # The wrapped command already ran; never let distillation lose it.
        return output


def _load_distill_config(
    repo_root: Path | None,
) -> tuple[bool, tuple[str, ...], tuple[float, float]]:
    from repowise.core.distill.config import omission_store_settings

    if repo_root is None:
        return True, (), omission_store_settings(None)
    from repowise.core.repo_config import load_repo_config

    cfg = load_repo_config(repo_root).get("distill") or {}
    if not isinstance(cfg, dict):
        return True, (), omission_store_settings(None)
    enabled = bool(cfg.get("enabled", True))
    commands_cfg = cfg.get("commands") or {}
    raw_disabled = commands_cfg.get("disabled_filters") if isinstance(commands_cfg, dict) else None
    disabled = tuple(str(name) for name in raw_disabled) if isinstance(raw_disabled, list) else ()
    return enabled, disabled, omission_store_settings(cfg)


def _echo_safely(text: str) -> None:
    """Echo *text* without dying on non-UTF-8 consoles (Windows cp1252)."""
    try:
        click.echo(text)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(text.encode("utf-8", errors="replace") + b"\n")
