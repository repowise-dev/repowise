"""``repowise config``: find, explain and change any repowise setting.

Every subcommand reads the one settings registry (``repowise.core.settings``),
so a setting an area declares is listed, explained and settable here with no
edit to this file. ``set`` and ``reset`` validate through the registry and
write through the area's own writer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import click
from rich.markup import escape
from rich.table import Table

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.settings import AREAS, Resolved, Setting, Source
from repowise.core.settings.views import (
    APPLIES,
    change_report,
    path_flag,
    requirement,
    resolve_many,
    view,
)
from repowise.core.settings.views import changed as _changed

_PATH_HELP = "A path inside the repository (defaults to the current directory)."

@click.group(name="config")
def config_group() -> None:
    """Find, explain and change repowise settings.

    `list` shows every setting with its value and where it came from,
    `explain <key>` says what one does, `set <key> <value>` changes it and
    prints the undo command, `reset <key>` goes back to the default.
    """


# ---------------------------------------------------------------------------
# reading
# ---------------------------------------------------------------------------


def _repo(path: str | None, fmt: str) -> Path:
    from repowise.cli.helpers import resolve_command_target

    target = resolve_command_target(path=path)
    if fmt == "json":
        return target.single_repo_path()
    return target.narrow(notice_console(fmt), command="config", repo_option=False)


def _setting(key: str, fmt: str) -> Setting:
    from difflib import get_close_matches

    from repowise.core.settings import all_settings, get

    try:
        return get(key)
    except KeyError:
        near = get_close_matches(key, [s.key for s in all_settings()], n=3)
        hint = f" Did you mean {', '.join(near)}?" if near else ""
        emit_refusal(
            "unknown_setting",
            f"No setting named {key}.{hint}",
            fmt,
            remedy="`repowise config list` shows every setting.",
        )
        raise AssertionError("unreachable") from None


def _resolve_all(root: Path, settings: tuple[Setting, ...], fmt: str) -> list[Resolved]:
    """Each setting's value, reading each file once. A broken file is refused."""
    from repowise.core.repo_config import RepoConfigError

    try:
        return resolve_many(root, settings)
    except RepoConfigError as exc:
        emit_refusal(
            "config_unreadable", str(exc), fmt, remedy="Fix the file, then run this again."
        )
        raise AssertionError("unreachable") from None


def _text(value: Any) -> str:
    if value is None:
        return "unset"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        return ", ".join(str(v) for v in value) if value else "(none)"
    if isinstance(value, dict):
        return json.dumps(value) if value else "(none)"
    return str(value)


def _source(r: Resolved) -> str:
    return r.source.value.replace("_", " ")


def _note(s: Setting, r: Resolved) -> str:
    if r.problem:
        return f"[yellow]{escape(r.problem)}[/yellow]"
    if r.source is Source.ENV and s.env is not None:
        return f"[cyan]from {s.env.name}[/cyan]"
    if _changed(s, r):
        return f"[dim]default {escape(_text(s.default))}[/dim]"
    return ""


@config_group.command(name="list")
@click.option("--area", type=click.Choice(list(AREAS)), default=None, help="Only this area.")
@click.option("--changed", "only_changed", is_flag=True, help="Only settings not at their default.")
@click.option("--all", "include_internal", is_flag=True, help="Include debug and test settings.")
@click.option("--path", default=None, help=_PATH_HELP)
@format_option()
def config_list(
    area: str | None, only_changed: bool, include_internal: bool, path: str | None, fmt: str
) -> None:
    """List settings by area, with their values and where each came from."""
    from repowise.core.settings import all_settings, settings_in

    root = _repo(path, fmt)
    pool = settings_in(area) if area else all_settings()
    settings = tuple(s for s in pool if include_internal or not s.internal)
    rows = [
        (s, r)
        for s, r in zip(settings, _resolve_all(root, settings, fmt), strict=True)
        if not only_changed or _changed(s, r)
    ]
    if fmt == "json":
        emit_json({"repo": str(root), "settings": [view(s, r) for s, r in rows]})
        return
    _print_list(rows)


def _print_list(rows: list[tuple[Setting, Resolved]]) -> None:
    if not rows:
        console.print("[dim]Every setting here is at its default.[/dim]")
        return
    for area in AREAS:
        in_area = [(s, r) for s, r in rows if s.area == area]
        if not in_area:
            continue
        table = Table(box=None, pad_edge=False, show_header=False)
        for _ in range(3):
            table.add_column()
        for s, r in in_area:
            key = f"[bold]{s.key}[/bold]" if _changed(s, r) else s.key
            table.add_row(f"  {key}", escape(_text(r.value)), _note(s, r))
        console.print(f"\n[bold]{area}[/bold]")
        console.print(table)
    changed = sum(_changed(s, r) for s, r in rows)
    console.print(
        f"\n[dim]{changed} changed from the default. "
        "`repowise config explain <key>` says what one does.[/dim]"
    )


@config_group.command(name="get")
@click.argument("key")
@click.option("--path", default=None, help=_PATH_HELP)
@format_option()
def config_get(key: str, path: str | None, fmt: str) -> None:
    """Print one setting's value and where it came from."""
    setting = _setting(key, fmt)
    root = _repo(path, fmt)
    (resolved,) = _resolve_all(root, (setting,), fmt)
    if fmt == "json":
        emit_json({"repo": str(root), **view(setting, resolved)})
        return
    line = f"{setting.key} = {escape(_text(resolved.value))}  [dim]({_source(resolved)})[/dim]"
    console.print(f"{line}  {_note(setting, resolved)}".rstrip())


def _explain_extra(s: Setting, root: Path, path: str | None) -> dict[str, Any]:
    extra: dict[str, Any] = {"applies": APPLIES[s.reload], "accepts": requirement(s)}
    if s.scope == "env":
        extra["set_with"] = f"{s.key}=<value> in the environment"
    else:
        extra["set_with"] = f"repowise config set {s.key} <value>{path_flag(path)}"
        extra["reset_with"] = f"repowise config reset {s.key}{path_flag(path)}"
    if s.key == "wiki_style":
        from repowise.core.generation.styles import list_styles

        extra["styles"] = [spec.name for spec in list_styles(root)]
    return extra


def _explain_rows(s: Setting, r: Resolved, extra: dict[str, Any]) -> list[tuple[str, str]]:
    rows = [
        ("Value", f"{_text(r.value)} ({_source(r)})"),
        ("Default", _text(s.default)),
        ("Accepts", extra["accepts"]),
        ("Where", f"{s.path} ({s.key})" if s.path else "the environment only"),
    ]
    if s.env is not None and s.scope != "env":
        wins = "wins over the file" if s.env.wins else "used when the file does not say"
        rows.append(("Env", f"{s.env.name} ({wins})"))
    rows.append(("Applies", extra["applies"]))
    if "styles" in extra:
        rows.append(("Styles", ", ".join(extra["styles"])))
    rows.append(("Change", extra["set_with"]))
    if "reset_with" in extra:
        rows.append(("Reset", extra["reset_with"]))
    return rows


@config_group.command(name="explain")
@click.argument("key")
@click.option("--path", default=None, help=_PATH_HELP)
@format_option()
def config_explain(key: str, path: str | None, fmt: str) -> None:
    """Say what one setting does, what it accepts, and how to change it."""
    setting = _setting(key, fmt)
    root = _repo(path, fmt)
    (resolved,) = _resolve_all(root, (setting,), fmt)
    extra = _explain_extra(setting, root, path)
    if fmt == "json":
        emit_json({"repo": str(root), **view(setting, resolved), **extra})
        return
    console.print(f"\n[bold]{setting.key}[/bold]\n  {escape(setting.description)}")
    if resolved.problem:
        console.print(f"  [yellow]{escape(resolved.problem)}[/yellow]")
    table = Table(box=None, pad_edge=False, show_header=False)
    table.add_column(style="dim")
    table.add_column()
    for label, value in _explain_rows(setting, resolved, extra):
        table.add_row(f"  {label}", escape(value))
    console.print(table)
    console.print("")


# ---------------------------------------------------------------------------
# writing
# ---------------------------------------------------------------------------


def _write(action: str, key: str, value: str | None, path: str | None, fmt: str) -> None:
    from repowise.core.repo_config import RepoConfigError
    from repowise.core.settings.writers import EnvOnlyError, parse_text, reset_value, set_value

    setting = _setting(key, fmt)
    root = _repo(path, fmt)
    try:
        if action == "set":
            change = set_value(root, key, parse_text(setting, value or ""))
        else:
            change = reset_value(root, key)
    except EnvOnlyError as exc:
        emit_refusal("env_only", str(exc), fmt)
    except RepoConfigError as exc:
        emit_refusal(
            "config_unreadable",
            f"{exc}. Nothing was saved, so the file keeps what it has.",
            fmt,
            remedy="Fix the file, then run this again.",
        )
    except ValueError as exc:
        emit_refusal("invalid_value", str(exc), fmt, accepts=requirement(setting))
    _report(action, change, root, path, fmt)


def _report(action: str, change: Any, root: Path, path: str | None, fmt: str) -> None:
    report = change_report(action, change, path)
    if fmt == "json":
        emit_json({"repo": str(root), **report})
        return
    moved = [m for m in (change.main, *change.also) if m.moved]
    _print_report(change, moved, report["effect"], report["env_override"], report["undo"])


def _print_report(
    change: Any, moved: list[Any], effect: str, override: str | None, undo: list[str]
) -> None:
    if not change.changed:
        console.print(
            f"[dim]{change.setting.key} is already {escape(_text(change.after.value))}; "
            "nothing changed.[/dim]"
        )
    for m in moved:
        before, after = m.saved_before.value, m.saved_after.value
        if m.before.value != m.after.value or not override:
            before, after = m.before.value, m.after.value
        console.print(
            f"[green]✓[/green] {m.setting.key}: {escape(_text(before))} -> {escape(_text(after))}"
        )
    if change.changed:
        console.print(f"  [dim]{escape(effect)}[/dim]")
    if override:
        console.print(
            f"  [yellow]Saved, but {override} is set in this environment and wins over "
            "the file until it is unset.[/yellow]"
        )
    for line in undo:
        console.print(f"  Undo: [bold]{escape(line)}[/bold]")


@config_group.command(name="set")
@click.argument("key")
@click.argument("value")
@click.option("--path", default=None, help=_PATH_HELP)
@format_option()
def config_set(key: str, value: str, path: str | None, fmt: str) -> None:
    """Change one setting; prints what changed, its effect, and the undo command.

    VALUE is read as the setting's type: true or false for a switch, a
    number, a,b or [a, b] for a list (use [a, b] when an item holds a comma).
    """
    _write("set", key, value, path, fmt)


@config_group.command(name="reset")
@click.argument("key")
@click.option("--path", default=None, help=_PATH_HELP)
@format_option()
def config_reset(key: str, path: str | None, fmt: str) -> None:
    """Put one setting back to its default."""
    _write("reset", key, None, path, fmt)


@config_group.command(name="hints")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_hints(state: str) -> None:
    """Turn the occasional repowise.dev tip on or off."""
    from repowise.cli import hints

    hints.set_enabled(state == "on")
    if state == "on":
        console.print("[green]✓[/green] repowise.dev tips are on (at most one a week each).")
    else:
        console.print("[green]✓[/green] repowise.dev tips are off.")


config_command = config_group
