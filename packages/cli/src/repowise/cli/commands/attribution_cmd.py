"""``repowise attribution``: inspect and clear AI attribution for a repository.

``status`` shows what is switched on (effective value beside the repo and user
values), for which agents, and how much of the indexed history has a known
source. ``purge`` deletes the attribution the local index derived. Capture is
not wired yet, so there is no sidecar, cursor or Repowise-written note to
remove; third-party git-ai notes are never touched.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import click

from repowise.cli.helpers import console, repo_index_session, resolve_repo_path, run_async
from repowise.cli.output import emit_json, format_option


def _settings(repo_path: Path) -> Any:
    from repowise.cli.agent_targets.registry import list_target_ids
    from repowise.core.attribution import load_attribution_settings

    return load_attribution_settings(repo_path, known_agents=list_target_ids())


async def _label_counts(repo_path: Path) -> dict[str, int] | None:
    """Indexed commits by attribution label, or ``None`` without an index."""
    from sqlalchemy import func, select

    from repowise.core.attribution import commit_label
    from repowise.core.persistence.models import GitCommit

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return None
        session, repo_id = opened
        rows = await session.execute(
            select(GitCommit.attribution, GitCommit.agent_name, func.count())
            .where(GitCommit.repository_id == repo_id)
            .group_by(GitCommit.attribution, GitCommit.agent_name)
        )
        counts = {"agent": 0, "human_attested": 0, "unknown": 0}
        for attribution, agent_name, n in rows.all():
            counts[commit_label(attribution, agent_name)] += int(n)
        return counts


_DEFAULTS: dict[str, Any] = {"enabled": True, "capture": "off", "agents": ()}


def _fmt(value: Any) -> str:
    if isinstance(value, (tuple, list)):
        return ", ".join(value) or "none"
    return str(value).lower() if isinstance(value, bool) else str(value)


def _explanations(settings: Any) -> list[str]:
    """Plain-words reasons wherever a value a config file sets is not the effective one."""
    reasons: list[str] = []
    layers = (
        ("repo", settings.repo, "user", settings.user),
        ("user", settings.user, "repo", settings.repo),
    )
    for key in ("enabled", "capture", "agents"):
        effective = getattr(settings, key)
        for name, layer, other_name, other in layers:
            value = getattr(layer, key)
            if value is None or (tuple(value) if key == "agents" else value) == effective:
                continue
            other_value = getattr(other, key)
            if key != "enabled" and not settings.enabled:
                why = "attribution is switched off"
            elif key != "enabled" and settings.do_not_track:
                why = "DO_NOT_TRACK is set"
            elif key == "agents" and settings.capture == "off":
                why = "capture is off, so no agent is captured"
            elif other_value is None:
                why = (
                    f"the {other_name} config does not set it, so the default"
                    f" ({_fmt(_DEFAULTS[key])}) applies there and the stricter value wins"
                )
            else:
                why = (
                    f"the {other_name} config sets {_fmt(other_value)}"
                    " and the stricter value wins"
                )
            reasons.append(
                f"{key}: the {name} config sets {_fmt(value)}, but {_fmt(effective)} applies"
                f" because {why}."
            )
    return reasons


def _layer(layer: Any) -> dict[str, Any]:
    return {
        "enabled": layer.enabled,
        "capture": layer.capture,
        "agents": list(layer.agents) if layer.agents is not None else None,
    }


@click.group(name="attribution")
def attribution_group() -> None:
    """Inspect and clear AI attribution for this repository."""


@attribution_group.command(name="status")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@format_option(choices=("table", "json"))
def attribution_status(path: str | None, fmt: str) -> None:
    """Show what is on, for which agents, and the provenance coverage."""
    from repowise.core.attribution import attribution_share

    repo_path = resolve_repo_path(path)
    settings = _settings(repo_path)
    counts = run_async(_label_counts(repo_path)) if settings.enabled else None
    share = (
        attribution_share(counts["agent"], counts["human_attested"], sum(counts.values()))
        if counts is not None
        else None
    )
    if fmt == "json":
        emit_json(
            {
                "effective": {
                    "enabled": settings.enabled,
                    "capture": settings.capture,
                    "agents": list(settings.agents),
                },
                "repo": _layer(settings.repo),
                "user": _layer(settings.user),
                "do_not_track": settings.do_not_track,
                "errors": list(settings.errors),
                "explanations": _explanations(settings),
                "commits": counts,
                "coverage": share.coverage if share else None,
                "agent_share_of_covered": share.agent_share if share else None,
                "suppressed_reason": share.suppressed_reason if share else None,
            }
        )
        return

    def _show(value: Any) -> str:
        return "[dim]unset[/dim]" if value is None else _fmt(value)

    console.print("[bold]AI attribution[/bold]")
    for key in ("enabled", "capture", "agents"):
        console.print(
            f"  {key:<8} {_show(getattr(settings, key))}"
            f"  (repo: {_show(getattr(settings.repo, key))},"
            f" user: {_show(getattr(settings.user, key))})"
        )
    if settings.do_not_track:
        console.print("  DO_NOT_TRACK is set: capture stays off.")
    for error in settings.errors:
        console.print(f"  [yellow]![/yellow] {error} (treated as the strictest value)")
    for reason in _explanations(settings):
        console.print(f"  [dim]{reason}[/dim]")
    if not settings.enabled:
        console.print("  Nothing about AI is stored or shown for this repository.")
    elif counts is None:
        console.print("  No index yet. Run `repowise init` to read public attribution markers.")
    else:
        console.print(
            f"  commits  {counts['agent']} agent, {counts['human_attested']} human-attested,"
            f" {counts['unknown']} unknown"
        )
        if share is not None:
            console.print(f"  share    {share.summary()}")


async def _purge(repo_path: Path) -> bool:
    from repowise.core.persistence.crud import clear_attribution

    async with repo_index_session(repo_path) as opened:
        if opened is None:
            return False
        session, repo_id = opened
        await clear_attribution(session, repo_id)
        return True


@attribution_group.command(name="purge")
@click.argument("path", required=False, type=click.Path(exists=True, file_okay=False))
@click.option("--yes", "-y", is_flag=True, help="Skip the confirmation prompt.")
def attribution_purge(path: str | None, yes: bool) -> None:
    """Delete the AI attribution derived into the local index.

    Clears every commit label and file rollup. Git history, trailers and notes
    in the repository are left as they are.
    """
    repo_path = resolve_repo_path(path)
    if not yes and not click.confirm(
        "Delete all AI attribution derived into this repository's index?", default=False
    ):
        console.print("Nothing deleted.")
        return
    if not run_async(_purge(repo_path)):
        console.print("No index found; nothing to delete.")
        return
    console.print("[green]✓[/green] AI attribution deleted from the local index.")
    if _settings(repo_path).enabled:
        console.print(
            "  Attribution is still enabled: `repowise update` labels new commits, and a"
            " full re-index labels past ones again. Set `attribution.enabled: false` in"
            " .repowise/config.yaml to keep it off."
        )

