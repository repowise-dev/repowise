"""Presenter for release news in the CLI.

Locates the changelog (bundled with the wheel, or the source ``docs/CHANGELOG.md``
in a checkout), parses it via :mod:`repowise.core.upgrade.changelog`, and renders
"what's new" panels and the PyPI update advisory. Also tracks the last release
the user has seen so the post-upgrade panel and ``repowise whats-new`` only show
genuinely new entries.

Everything here is best-effort: a missing changelog or unreadable marker degrades
to a quiet fallback (a link to the GitHub releases page), never an error.
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from repowise.core.upgrade.changelog import (
    ChangelogEntry,
    entries_between,
    parse_changelog,
)
from repowise.core.upgrade.release import BUNDLED_CHANGELOG_PATH

from .helpers import user_global_dir

RELEASES_URL = "https://github.com/repowise-dev/repowise/releases"

_BUNDLED_CHANGELOG = BUNDLED_CHANGELOG_PATH
_LAST_SEEN_FILENAME = "last-seen-version"


# --- changelog location + parsing ----------------------------------------


def find_changelog_path() -> Path | None:
    """Locate *repowise's* changelog.

    Order: ``REPOWISE_CHANGELOG`` override, then the source ``docs/CHANGELOG.md``
    reachable from this package (a dev/editable checkout, freshest), then the
    copy bundled into the installed wheel.

    Deliberately does NOT search the current working directory: an installed
    user running ``repowise`` inside their own project must never have that
    project's ``docs/CHANGELOG.md`` mistaken for repowise's release notes.
    """
    override = os.environ.get("REPOWISE_CHANGELOG")
    if override and Path(override).is_file():
        return Path(override)
    # Walk up from this module; in a source/editable checkout a parent holds
    # docs/CHANGELOG.md. In an installed wheel the package lives under
    # site-packages with no such parent, so this finds nothing and we fall
    # through to the bundled copy.
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "docs" / "CHANGELOG.md"
        if candidate.is_file():
            return candidate
    if _BUNDLED_CHANGELOG.is_file():
        return _BUNDLED_CHANGELOG
    return None


def load_changelog_entries() -> list[ChangelogEntry]:
    """Parsed changelog entries (newest first), or ``[]`` if none can be found."""
    path = find_changelog_path()
    if path is None:
        return []
    try:
        return parse_changelog(path.read_text(encoding="utf-8"))
    except Exception:
        return []


# --- last-seen-version tracking ------------------------------------------


def read_last_seen_version() -> str | None:
    try:
        text = (user_global_dir() / _LAST_SEEN_FILENAME).read_text(encoding="utf-8").strip()
        return text or None
    except Exception:
        return None


def write_last_seen_version(version: str) -> None:
    with contextlib.suppress(Exception):  # best-effort marker
        (user_global_dir() / _LAST_SEEN_FILENAME).write_text(version, encoding="utf-8")


# --- rendering ------------------------------------------------------------


def _format_entry(entry: ChangelogEntry, *, max_items: int) -> str:
    lines: list[str] = []
    shown = 0
    truncated = False
    for section in entry.sections:
        if shown >= max_items:
            truncated = True
            break
        if not section.items:
            continue
        lines.append(f"[bold]{section.name}[/bold]")
        for item in section.items:
            if shown >= max_items:
                truncated = True
                break
            lines.append(f"  - {_strip_markdown(item)}")
            shown += 1
    if truncated:
        lines.append("  [dim]...[/dim]")
    return "\n".join(lines)


def _strip_markdown(text: str) -> str:
    # Light touch: drop bold markers so rich doesn't render literal ** and keep
    # the line readable in a terminal. Leaves the prose otherwise intact.
    return text.replace("**", "")


def render_whats_new(
    console: Console,
    entries: list[ChangelogEntry],
    *,
    since_version: str | None,
    up_to_version: str | None = None,
    max_versions: int = 5,
    max_items_per_version: int = 8,
    title: str = "What's new",
) -> bool:
    """Render release entries newer than *since_version* (up to *up_to_version*).

    Returns ``True`` if anything was rendered. Falls back to a quiet GitHub link
    when no changelog is available.
    """
    if not entries:
        console.print(f"[dim]Release notes: {RELEASES_URL}[/dim]")
        return False

    selected = entries_between(entries, newer_than=since_version, up_to=up_to_version)
    if not selected:
        return False

    blocks: list[str] = []
    for entry in selected[:max_versions]:
        label = f" [dim]({entry.label})[/dim]" if entry.label else ""
        body = _format_entry(entry, max_items=max_items_per_version)
        blocks.append(f"[bold cyan]v{entry.version}[/bold cyan]{label}\n{body}")

    extra = len(selected) - max_versions
    if extra > 0:
        blocks.append(f"[dim]...and {extra} earlier release(s). Full notes: {RELEASES_URL}[/dim]")

    console.print(Panel("\n\n".join(blocks), title=title, border_style="cyan", expand=False))
    return True


def render_update_advisory(console: Console, check) -> bool:
    """Print a one-line, non-blocking advisory when a newer release exists.

    *check* is an :class:`~repowise.cli.update_check.UpdateCheck`. Returns
    ``True`` if an advisory was printed (i.e. an update is available).
    """
    if not check.update_available or not check.latest_version:
        return False
    console.print(
        f"[yellow]repowise {check.latest_version} is available[/yellow] "
        f"[dim](you have {check.current_version}).[/dim] "
        f"Upgrade: [bold]{check.suggested_command}[/bold]"
    )
    return True


__all__ = [
    "RELEASES_URL",
    "find_changelog_path",
    "load_changelog_entries",
    "read_last_seen_version",
    "render_update_advisory",
    "render_whats_new",
    "write_last_seen_version",
]
