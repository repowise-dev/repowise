"""Derive C4 L1 actors from a repository's curated entry points.

The L1 System Context answers "who or what drives this system?" A lone
hardcoded "User" is no information. The curated entry-point paths already
encode how the system is entered: a ``cli/main.py`` is run by a person at a
terminal, a ``server/app.py`` is called by an API client, a ``worker``/``cron``
module is woken by a scheduler. This module maps those path conventions to a
small, deduplicated set of actors so the diagram reflects reality, while
falling back to a generic user when nothing is determinable.

Pure functions — operate on the path strings, no DB — so they unit-test in
isolation.
"""

from __future__ import annotations

from dataclasses import dataclass

# Actor kind -> (display name, what it does to the system). Order here is the
# stable render order when several kinds are present.
_ACTOR_META: dict[str, tuple[str, str]] = {
    "cli": ("CLI user", "Runs commands from a terminal"),
    "api": ("API client", "Calls the HTTP API"),
    "scheduler": ("Scheduled job", "Triggers background runs on a schedule"),
    "developer": ("Developer / CI", "Runs scripts and tooling"),
    "user": ("User", "Uses the system"),
}
_KIND_ORDER: tuple[str, ...] = ("cli", "api", "scheduler", "developer", "user")

# How each actor kind relates to the system on the L1 arrow.
_ACTOR_VERB: dict[str, str] = {
    "cli": "runs",
    "api": "calls",
    "scheduler": "triggers",
    "developer": "runs",
    "user": "uses",
}


@dataclass(frozen=True)
class Actor:
    """A derived L1 actor: a stable id, a display name, the system-edge verb."""

    id: str
    kind: str
    name: str
    description: str
    verb: str


def _segments(path: str) -> tuple[list[str], str]:
    """Return (lowercased path segments, lowercased basename)."""
    parts = path.replace("\\", "/").lower().split("/")
    return parts, parts[-1] if parts else ""


def classify_entry_point(path: str) -> str | None:
    """Classify one entry-point path into an actor kind, or ``None``.

    Convention-based and framework-agnostic: matches on conventional directory
    and file names rather than any specific tool. Checked most-specific first.
    """
    segs, base = _segments(path)
    segset = set(segs)

    # Scheduled/background entry: a worker or cron-style module.
    if segset & {"worker", "workers", "cron", "scheduler", "beat", "tasks", "celery"}:
        return "scheduler"

    # CLI entry: a cli package or a conventional command launcher.
    if "cli" in segset or base in {"__main__.py", "manage.py"}:
        return "cli"

    # HTTP/API entry: a server app or web framework launcher.
    if segset & {"server", "api", "routers", "asgi", "wsgi"}:
        return "api"
    if base in {"app.py", "asgi.py", "wsgi.py", "server.py"}:
        return "api"

    # Developer / CI tooling: scripts and standalone runners.
    if segset & {"scripts", "tools", "tooling"} or base == "run.py":
        return "developer"

    return None


def derive_actors(entry_points: list[str]) -> list[Actor]:
    """Derive the deduplicated, ordered actor set for the L1 view.

    Each distinct actor kind appears once. When no entry point is classifiable
    (or the list is empty) a single generic ``user`` actor is returned so L1 is
    never empty.
    """
    kinds: list[str] = []
    for path in entry_points:
        kind = classify_entry_point(path)
        if kind is not None and kind not in kinds:
            kinds.append(kind)

    if not kinds:
        kinds = ["user"]

    ordered = [k for k in _KIND_ORDER if k in kinds]
    actors: list[Actor] = []
    for kind in ordered:
        name, description = _ACTOR_META[kind]
        actors.append(
            Actor(
                id=f"person:{kind}",
                kind=kind,
                name=name,
                description=description,
                verb=_ACTOR_VERB[kind],
            )
        )
    return actors
