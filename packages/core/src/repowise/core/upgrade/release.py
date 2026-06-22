"""Release info shared by every presenter: version compare, PyPI freshness, and
repowise's own bundled changelog.

This is the neutral home (in ``repowise-core``) for logic both the CLI and the
server need. The server depends only on core, so anything it shares with the CLI
lives here rather than in ``repowise.cli``. The CLI re-uses these helpers too, so
the version comparator and PyPI fetch are defined once.

Everything is best-effort: network and cache failures return an empty/unknown
result rather than raising.
"""

from __future__ import annotations

import contextlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

from .changelog import ChangelogEntry, parse_changelog

PYPI_URL = "https://pypi.org/pypi/repowise/json"

#: Default freshness window for the cached PyPI check (hours). Within it the last
#: fetched ``latest_version`` is reused so routine calls never hit the network.
DEFAULT_TTL_HOURS = 24.0

#: repowise's own changelog, bundled into the wheel as core package data so both
#: ``repowise whats-new`` and the web what's-new view work offline.
BUNDLED_CHANGELOG_PATH = Path(__file__).parent / "_data" / "CHANGELOG.md"

#: Shared cache file (also written by the CLI advisory) under the user-global dir.
_CACHE_PATH = Path.home() / ".repowise" / "update-check.json"


# --- version comparison ---------------------------------------------------


def parse_release(version: str) -> tuple[int, ...] | None:
    """Parse the leading numeric release parts of *version* (``0.15.2`` -> ``(0,15,2)``).

    Pre-release/local suffixes (``-rc1``, ``+local``) are ignored. Returns
    ``None`` when no numeric component can be parsed.
    """
    parts: list[int] = []
    for chunk in version.strip().split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts) or None


def is_newer_version(latest: str, current: str) -> bool:
    """Return ``True`` if *latest* is a strictly newer release than *current*.

    Unparsable versions yield ``False`` (no update decision) rather than raising.
    """
    lat = parse_release(latest)
    cur = parse_release(current)
    if lat is None or cur is None:
        return False
    length = max(len(lat), len(cur))
    lat = lat + (0,) * (length - len(lat))
    cur = cur + (0,) * (length - len(cur))
    return lat > cur


# --- PyPI freshness -------------------------------------------------------


def fetch_latest_version(timeout: float = 2.0) -> tuple[str | None, str | None]:
    """Fetch repowise's latest published version from PyPI. Never raises.

    Returns ``(latest, error)``. ``latest`` is ``None`` on any failure (network,
    JSON, or a version we cannot compare), with ``error`` describing why.
    """
    try:
        import httpx

        resp = httpx.get(PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        fetched = resp.json()["info"]["version"]
        if parse_release(fetched) is None:
            return None, f"unparsable latest version: {fetched!r}"
        return fetched, None
    except Exception as exc:  # network, JSON, missing key - all advisory
        return None, str(exc) or exc.__class__.__name__


@dataclass(frozen=True)
class ReleaseCheck:
    """Outcome of a PyPI freshness check.

    ``update_available`` is ``None`` when ``latest`` could not be determined, so
    callers can tell "up to date" apart from "unknown".
    """

    current_version: str
    latest_version: str | None
    update_available: bool | None
    error: str | None = None


def check_latest_version_cached(
    current_version: str,
    *,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    timeout: float = 2.0,
) -> ReleaseCheck:
    """TTL-cached PyPI freshness check. Never raises.

    Reuses the last fetched ``latest_version`` from the shared
    ``~/.repowise/update-check.json`` while younger than *ttl_hours* (the same
    file the CLI advisory uses, so the two share a single network call). On a
    miss it fetches live and persists the result. All cache I/O is best-effort.
    """
    latest, error = _read_cached_latest(ttl_hours)
    if latest is _MISS:
        latest, error = fetch_latest_version(timeout=timeout)
        _write_cached_latest(latest, error)
    update_available = is_newer_version(latest, current_version) if latest else None
    return ReleaseCheck(
        current_version=current_version,
        latest_version=latest,
        update_available=update_available,
        error=error,
    )


#: Sentinel for "no fresh cache entry" (distinct from a cached ``latest=None``).
_MISS = object()


def _read_cached_latest(ttl_hours: float):
    try:
        cached = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        age = time.time() - float(cached["checked_at"])
        if 0 <= age < ttl_hours * 3600:
            return cached.get("latest_version"), cached.get("error")
    except Exception:
        pass
    return _MISS, None


def _write_cached_latest(latest: str | None, error: str | None) -> None:
    with contextlib.suppress(Exception):
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(
            json.dumps({"checked_at": time.time(), "latest_version": latest, "error": error}),
            encoding="utf-8",
        )


# --- bundled changelog ----------------------------------------------------


def load_bundled_changelog() -> list[ChangelogEntry]:
    """Parse repowise's bundled changelog, or ``[]`` if it cannot be read."""
    try:
        return parse_changelog(BUNDLED_CHANGELOG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


__all__ = [
    "BUNDLED_CHANGELOG_PATH",
    "DEFAULT_TTL_HOURS",
    "PYPI_URL",
    "ReleaseCheck",
    "check_latest_version_cached",
    "fetch_latest_version",
    "is_newer_version",
    "load_bundled_changelog",
    "parse_release",
]
