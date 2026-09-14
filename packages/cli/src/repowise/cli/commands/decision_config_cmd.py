"""``repowise decision config`` / ``source`` / ``llm`` — capture controls.

Every command here reads and writes the one resolved policy in
``repowise.core.analysis.decisions.policy``, so the CLI cannot disagree with
what the index pipeline and the server report.
"""

from __future__ import annotations

from pathlib import Path

import click
from rich.table import Table

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, format_option, notice_console
from repowise.core.analysis.decisions.policy import (
    DISCOVERY_BOUNDS,
    PRESET_NAMES,
    SOURCE_SPECS,
    DecisionPolicy,
)
from repowise.core.analysis.decisions.policy_store import load_policy, write_policy


def _load(repo_path: Path):
    """Resolve the policy, turning an unparseable config into a clean error.

    A malformed ``decisions:`` block is only a warning, but a malformed *file*
    never reaches the resolver and would surface as a traceback.
    """
    from repowise.core.repo_config import RepoConfigError

    try:
        return load_policy(repo_path)
    except RepoConfigError as exc:
        raise click.ClickException(str(exc)) from exc

#: Sources a user can switch. ``cli`` is manual entry, an authority route with
#: nothing to capture, so offering it here would imply it could be turned off.
_TOGGLABLE: tuple[str, ...] = tuple(s.key for s in SOURCE_SPECS if s.togglable)

_STATUS_STYLE = {
    "enabled": "green",
    "always_on": "green",
    "deterministic_only": "yellow",
    "skipped_no_provider": "yellow",
    "disabled": "dim",
}


def _provider_available(repo_path: Path) -> bool:
    """Whether an LLM provider resolves for this repo.

    Reported, not enforced: the model on with no key configured is a healthy
    ``skipped_no_provider``, not a failure.
    """
    from repowise.core.providers.llm.registry import provider_available_for_repo

    return provider_available_for_repo(repo_path)


def _emit(repo_path: Path, resolution, fmt: str) -> None:
    """Render a resolved policy, plus any warnings, in the requested format."""
    policy = resolution.policy
    provider_available = _provider_available(repo_path)
    notices = notice_console(fmt)

    if fmt == "json":
        emit_json(
            {
                "repo": str(repo_path),
                "policy": policy.to_dict(provider_available=provider_available),
                "provider_available": provider_available,
                "warnings": list(resolution.warnings),
                "legacy_keys": list(resolution.legacy_keys),
            }
        )
        return

    header = "on" if policy.enabled else "off"
    llm = "on" if policy.llm else "off"
    console.print(
        f"\n  Decision capture [bold]{header}[/bold]  ·  "
        f"LLM extraction [bold]{llm}[/bold]  ·  preset [bold]{policy.preset_name()}[/bold]"
    )
    if not provider_available:
        console.print("  [dim]No LLM provider configured; model stages are skipped.[/dim]")

    table = Table(box=None, pad_edge=False, show_edge=False)
    table.add_column("Source", style="bold")
    table.add_column("Status")
    table.add_column("LLM")
    table.add_column("Why")
    for rt in policy.runtime(provider_available=provider_available):
        style = _STATUS_STYLE.get(rt.status, "")
        llm_cell = "-" if not rt.supports_llm else ("yes" if rt.llm_enabled else "no")
        table.add_row(
            rt.key,
            f"[{style}]{rt.status}[/{style}]" if style else rt.status,
            llm_cell,
            f"[dim]{rt.reason}[/dim]",
        )
    console.print(table)
    budget = policy.discovery
    console.print(
        f"  [dim]Discovery budget: up to {budget.max_sessions} session(s), "
        f"{budget.max_input_tokens:,} input tokens per update.[/dim]"
    )
    console.print("")

    for warning in resolution.warnings:
        notices.print(f"[yellow]{warning}[/yellow]")
    if resolution.legacy_keys:
        notices.print(
            "[dim]Legacy keys still honoured: "
            f"{', '.join(resolution.legacy_keys)}. Any write here replaces them.[/dim]"
        )


def _apply(repo_path: Path, policy: DecisionPolicy, fmt: str, dry_run: bool) -> None:
    """Persist *policy*, or show the diff it would make under ``--dry-run``."""
    from repowise.core.analysis.decisions.policy_store import PolicyConflictError

    before = _load(repo_path).policy
    if dry_run:
        changes = _diff(before, policy)
        if fmt == "json":
            emit_json(
                {
                    "repo": str(repo_path),
                    "dry_run": True,
                    "changes": changes,
                    "policy": policy.to_dict(provider_available=_provider_available(repo_path)),
                }
            )
            return
        if not changes:
            console.print("[dim]No change.[/dim]")
            return
        console.print("\n  Would change:")
        for change in changes:
            console.print(f"    {change['key']}: {change['from']} -> {change['to']}")
        console.print("")
        return

    try:
        resolution = write_policy(repo_path, policy)
    except PolicyConflictError as exc:
        raise click.ClickException(str(exc)) from exc
    _emit(repo_path, resolution, fmt)


def _diff(before: DecisionPolicy, after: DecisionPolicy) -> list[dict[str, str]]:
    changes: list[dict[str, str]] = []
    if before.enabled != after.enabled:
        changes.append({"key": "enabled", "from": str(before.enabled), "to": str(after.enabled)})
    if before.llm != after.llm:
        changes.append({"key": "llm", "from": str(before.llm), "to": str(after.llm)})
    for key in _TOGGLABLE:
        old, new = before.sources.get(key), after.sources.get(key)
        if old == new:
            continue
        changes.append(
            {
                "key": f"sources.{key}",
                "from": f"enabled={old.enabled if old else '?'} llm={old.llm if old else '?'}",
                "to": f"enabled={new.enabled if new else '?'} llm={new.llm if new else '?'}",
            }
        )
    for key in DISCOVERY_BOUNDS:
        old_value = getattr(before.discovery, key)
        new_value = getattr(after.discovery, key)
        if old_value != new_value:
            changes.append(
                {"key": f"discovery.{key}", "from": str(old_value), "to": str(new_value)}
            )
    return changes


# ---------------------------------------------------------------------------
# decision config
# ---------------------------------------------------------------------------


@click.group("config")
def config_group() -> None:
    """Inspect and set decision capture policy."""


@config_group.command("show")
@click.argument("path", required=False, default=None)
@format_option()
def config_show(path: str | None, fmt: str) -> None:
    """Show the resolved capture policy for this repository."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    _emit(repo_path, _load(repo_path), fmt)


@config_group.command("preset")
@click.argument("name", type=click.Choice(list(PRESET_NAMES)))
@click.argument("path", required=False, default=None)
@click.option("--dry-run", is_flag=True, default=False, help="Show the change; write nothing.")
@format_option()
def config_preset(name: str, path: str | None, dry_run: bool, fmt: str) -> None:
    """Apply a named preset: default, off, local_only, balanced, full."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo
    from repowise.core.analysis.decisions.policy import preset_policy

    repo_path = _resolve_decision_repo(path, fmt)
    _apply(repo_path, preset_policy(name), fmt, dry_run)


# ---------------------------------------------------------------------------
# decision source
# ---------------------------------------------------------------------------


@config_group.command("discovery")
@click.argument("path", required=False, default=None)
@click.option(
    "--max-sessions",
    type=int,
    default=None,
    help="Session deltas one broad discovery call may read.",
)
@click.option(
    "--max-input-tokens",
    type=int,
    default=None,
    help="Input-token ceiling for one broad discovery call.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show the change; write nothing.")
@format_option()
def config_discovery(
    path: str | None,
    max_sessions: int | None,
    max_input_tokens: int | None,
    dry_run: bool,
    fmt: str,
) -> None:
    """Set the per-update budget for broad session discovery."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    fields = {
        key: value
        for key, value in (
            ("max_sessions", max_sessions),
            ("max_input_tokens", max_input_tokens),
        )
        if value is not None
    }
    if not fields:
        _emit(repo_path, _load(repo_path), fmt)
        return
    try:
        policy = _load(repo_path).policy.with_discovery(**fields)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    _apply(repo_path, policy, fmt, dry_run)


@click.group("source")
def source_group() -> None:
    """List and switch individual capture sources."""


@source_group.command("list")
@click.argument("path", required=False, default=None)
@format_option()
def source_list(path: str | None, fmt: str) -> None:
    """List every capture source with its capabilities and current state."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    _emit(repo_path, _load(repo_path), fmt)


@source_group.command("set")
@click.argument("source", type=click.Choice(list(_TOGGLABLE)))
@click.argument("path", required=False, default=None)
@click.option("--on/--off", "enabled", default=None, help="Enable or disable this source.")
@click.option(
    "--llm/--no-llm",
    "llm",
    default=None,
    help="Allow or forbid this source's model stage.",
)
@click.option("--dry-run", is_flag=True, default=False, help="Show the change; write nothing.")
@format_option()
def source_set(
    source: str,
    path: str | None,
    enabled: bool | None,
    llm: bool | None,
    dry_run: bool,
    fmt: str,
) -> None:
    """Switch one capture source, or its model stage, on or off."""
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    if enabled is None and llm is None:
        raise click.ClickException("Pass --on/--off or --llm/--no-llm.")

    repo_path = _resolve_decision_repo(path, fmt)
    current = _load(repo_path).policy
    _apply(repo_path, current.with_source(source, enabled=enabled, llm=llm), fmt, dry_run)


# ---------------------------------------------------------------------------
# decision llm
# ---------------------------------------------------------------------------


@click.command("llm")
@click.argument("path", required=False, default=None)
@click.option("--on/--off", "enabled", default=None, required=True, help="Master LLM switch.")
@click.option("--dry-run", is_flag=True, default=False, help="Show the change; write nothing.")
@format_option()
def llm_command(path: str | None, enabled: bool | None, dry_run: bool, fmt: str) -> None:
    """Turn all decision-extraction model calls on or off.

    Off is a complete mode, not a degraded one: deterministic capture,
    transcript ingestion and manual decisions all keep working, and already
    accepted decisions keep governing.
    """
    from repowise.cli.commands.decision_cmd import _resolve_decision_repo

    repo_path = _resolve_decision_repo(path, fmt)
    current = _load(repo_path).policy
    _apply(repo_path, current.with_llm(bool(enabled)), fmt, dry_run)
