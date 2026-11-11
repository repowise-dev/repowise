"""``repowise config``: find, explain and change any repowise setting.

Every subcommand reads the one settings registry (``repowise.core.settings``),
so a setting an area declares is listed, explained and settable here with no
edit to this file. ``set`` and ``reset`` validate through the registry and
write through the area's own writer.
"""

from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Any

import click
from rich.markup import escape
from rich.table import Table

from repowise.cli.helpers import console
from repowise.cli.output import emit_json, emit_refusal, format_option, notice_console
from repowise.core.settings import AREAS, Resolved, Setting, Source

_PATH_HELP = "A path inside the repository (defaults to the current directory)."

#: When a change takes effect, in the words ``set`` and ``explain`` print.
_APPLIES = {
    "immediate": "Takes effect the next time it is read.",
    "update": "Takes effect on the next `repowise update`.",
    "reindex": "Takes effect after `repowise reindex`.",
    "restart": "Takes effect when the server or MCP process next starts.",
}


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
    from repowise.core.settings.writers import current, read_config

    files: dict[str | None, dict[str, Any]] = {}
    out = []
    try:
        for s in settings:
            if s.path not in files:
                files[s.path] = read_config(root, s)
            out.append(current(root, s, config=files[s.path]))
    except RepoConfigError as exc:
        emit_refusal(
            "config_unreadable", str(exc), fmt, remedy="Fix the file, then run this again."
        )
    return out


def _changed(s: Setting, r: Resolved) -> bool:
    """Set somewhere, to something other than the default."""
    return r.source is not Source.DEFAULT and r.value != s.default


def _state(s: Setting, r: Resolved) -> dict[str, Any]:
    """Where *s* stands: value, source, default, changed, problem, locked."""
    state = r.to_dict()
    state["default"] = s.to_dict()["default"]
    state["changed"] = _changed(s, r)
    return state


def _view(s: Setting, r: Resolved) -> dict[str, Any]:
    """The JSON object ``list``, ``get`` and ``explain`` give for one setting."""
    return {**s.to_dict(), **_state(s, r)}


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
        emit_json({"repo": str(root), "settings": [_view(s, r) for s, r in rows]})
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
        emit_json({"repo": str(root), **_view(setting, resolved)})
        return
    line = f"{setting.key} = {escape(_text(resolved.value))}  [dim]({_source(resolved)})[/dim]"
    console.print(f"{line}  {_note(setting, resolved)}".rstrip())


def _bounds(s: Setting) -> str:
    low = None
    if s.minimum is not None:
        low = f"more than {s.minimum:g}" if s.exclusive_minimum else f"at least {s.minimum:g}"
    high = None if s.maximum is None else f"at most {s.maximum:g}"
    return " and ".join(b for b in (low, high) if b)


def _requirement(s: Setting) -> str:
    """What a value of *s* must be, in plain words."""
    if s.choices:
        lead = "one of" if s.type == "str" else "any of"
        return f"{lead} {', '.join(s.choices)}"
    if s.type in ("int", "float"):
        kind = "a whole number" if s.type == "int" else "a number"
        bounds = _bounds(s)
        return f"{kind}, {bounds}" if bounds else kind
    return {
        "bool": "true or false",
        "list": "a list: a,b or [a, b]",
        "mapping": "a mapping: {key: value}",
    }.get(s.type, "text")


def _path_flag(path: str | None) -> str:
    return f" --path {shlex.quote(path)}" if path else ""


def _explain_extra(s: Setting, root: Path, path: str | None) -> dict[str, Any]:
    extra: dict[str, Any] = {"applies": _APPLIES[s.reload], "accepts": _requirement(s)}
    if s.scope == "env":
        extra["set_with"] = f"{s.key}=<value> in the environment"
    else:
        extra["set_with"] = f"repowise config set {s.key} <value>{_path_flag(path)}"
        extra["reset_with"] = f"repowise config reset {s.key}{_path_flag(path)}"
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
        emit_json({"repo": str(root), **_view(setting, resolved), **extra})
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


def _cli_value(value: Any) -> str:
    """*value* as ``repowise config set`` reads it back."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, tuple)):
        simple = bool(value) and all(
            isinstance(v, str) and v and "," not in v and v.strip() == v for v in value
        )
        return shlex.quote(",".join(value) if simple else json.dumps(list(value)))
    if isinstance(value, dict):
        return shlex.quote(json.dumps(value))
    return shlex.quote(str(value))


def _undo(m: Any, path: str | None, *, main: bool) -> str | None:
    """The command that puts the file back where it was, or ``None`` when none is needed.

    Read from what the file spelled, not the effective value: an environment
    variable that decides the value says nothing about what the file held. A
    setting that moved only because another key did (the switches under a
    mode) and was never in the file needs no command of its own.
    """
    from repowise.core.settings.resolve import MISSING

    key = m.setting.key
    if m.written_before is not MISSING:
        return f"repowise config set {key} {_cli_value(m.written_before)}{_path_flag(path)}"
    if main or m.written_after is not MISSING:
        return f"repowise config reset {key}{_path_flag(path)}"
    return None


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
        emit_refusal("invalid_value", str(exc), fmt, accepts=_requirement(setting))
    _report(action, change, root, path, fmt)


def _report(action: str, change: Any, root: Path, path: str | None, fmt: str) -> None:
    s = change.setting
    moved = [m for m in (change.main, *change.also) if m.moved]
    undo = [
        line
        for m in moved
        if (line := _undo(m, path, main=m is change.main)) is not None
    ]
    effect = f"{s.description} {_APPLIES[s.reload]}"
    override = s.env.name if change.after.source is Source.ENV and s.env else None
    if fmt == "json":
        emit_json(
            {
                "repo": str(root),
                "action": action,
                "key": s.key,
                "changed": change.changed,
                "before": _state(s, change.before),
                "after": _state(s, change.after),
                "also": [
                    {
                        "key": m.setting.key,
                        "before": _state(m.setting, m.before),
                        "after": _state(m.setting, m.after),
                    }
                    for m in change.also
                ],
                "effect": effect,
                "undo": undo,
                "env_override": override,
            }
        )
        return
    _print_report(change, moved, effect, override, undo)


def _written(value: Any) -> Any:
    from repowise.core.settings.resolve import MISSING

    return None if value is MISSING else value


def _print_report(
    change: Any, moved: list[Any], effect: str, override: str | None, undo: list[str]
) -> None:
    if not change.changed:
        console.print(
            f"[dim]{change.setting.key} is already {escape(_text(change.after.value))}; "
            "nothing changed.[/dim]"
        )
    for m in moved:
        before, after = m.before.value, m.after.value
        if override and before == after:  # the file moved under the variable
            before, after = _written(m.written_before), _written(m.written_after)
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
