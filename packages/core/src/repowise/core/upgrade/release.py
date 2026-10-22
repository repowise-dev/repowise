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
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .changelog import (
    ChangelogEntry,
    ChangelogHighlight,
    ChangelogSection,
    entries_between,
    parse_changelog,
)

PYPI_URL = "https://pypi.org/pypi/repowise/json"

#: The changelog on main, read only to name what a newer release brings.
CHANGELOG_URL = "https://raw.githubusercontent.com/repowise-dev/repowise/main/docs/CHANGELOG.md"

#: The changelog as released at a tag, read for the notes of releases newer than
#: the running one. Pinned to the tag so the result never changes once fetched.
TAGGED_CHANGELOG_URL = (
    "https://raw.githubusercontent.com/repowise-dev/repowise/v{version}/docs/CHANGELOG.md"
)

#: Default freshness window for the cached PyPI check (hours). Within it the last
#: fetched ``latest_version`` is reused so routine calls never hit the network.
DEFAULT_TTL_HOURS = 24.0

#: repowise's own changelog, bundled into the wheel as core package data so both
#: ``repowise whats-new`` and the web what's-new view work offline.
BUNDLED_CHANGELOG_PATH = Path(__file__).parent / "_data" / "CHANGELOG.md"

#: Per-release figures ("By the numbers"), keyed by version, written by
#: ``scripts/release_stats.py`` and bundled beside the changelog.
RELEASE_STATS_PATH = Path(__file__).parent / "_data" / "release_stats.json"

#: Shared cache file (also written by the CLI advisory) under the user-global dir.
_CACHE_PATH = Path.home() / ".repowise" / "update-check.json"

#: Highlight titles of the newer release. A file of its own because the CLI
#: rewrites ``update-check.json`` whole.
_HIGHLIGHTS_CACHE_PATH = Path.home() / ".repowise" / "release-highlights.json"

#: Parsed notes of the releases between the running and the latest version, for
#: the same reason a file of its own.
_NEWER_NOTES_CACHE_PATH = Path.home() / ".repowise" / "release-notes.json"


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
        if latest is not None:
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


# --- newer release highlights ---------------------------------------------


def fetch_release_highlights(version: str, timeout: float = 2.0) -> list[str] | None:
    """Highlight titles *version* lists in the changelog on main. Never raises.

    ``None`` on any network or parse failure, so the caller keeps its plain
    wording. Nothing is sent but the GET.
    """
    import httpx

    try:
        resp = httpx.get(CHANGELOG_URL, timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError:  # offline, timeout, non-2xx
        return None
    for entry in parse_changelog(resp.text):
        if entry.version == version:
            return [h.title for h in entry.highlights]
    return []


_EMPTY_TTL_HOURS = 0.25


def latest_highlights_cached(
    version: str,
    *,
    ttl_hours: float = DEFAULT_TTL_HOURS,
    timeout: float = 2.0,
) -> list[str]:
    """TTL-cached :func:`fetch_release_highlights`, ``[]`` when unknown.

    An empty or failed lookup is kept for :data:`_EMPTY_TTL_HOURS` only, so an
    offline machine does not retry on every request but a flaky first check
    does not hide the titles for a whole day.
    """
    try:
        cached = json.loads(_HIGHLIGHTS_CACHE_PATH.read_text(encoding="utf-8"))
        age = time.time() - float(cached["checked_at"])
        titles = list(cached.get("titles") or [])
        ttl = ttl_hours if titles else min(ttl_hours, _EMPTY_TTL_HOURS)
        if cached.get("version") == version and 0 <= age < ttl * 3600:
            return titles
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # no cache yet, or a malformed one: fetch
    titles = fetch_release_highlights(version, timeout=timeout)
    with contextlib.suppress(OSError):  # a read-only home only costs a refetch
        _HIGHLIGHTS_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _HIGHLIGHTS_CACHE_PATH.write_text(
            json.dumps({"checked_at": time.time(), "version": version, "titles": titles or []}),
            encoding="utf-8",
        )
    return titles or []


# --- newer release notes --------------------------------------------------


def fetch_newer_changelog(
    current_version: str, latest_version: str, timeout: float = 2.0
) -> list[ChangelogEntry] | None:
    """Entries newer than *current_version*, up to *latest_version*. Never raises.

    Read from the changelog at the ``v{latest_version}`` tag, because the
    installed wheel's bundled changelog was built before those releases existed.
    ``None`` on any network failure, so the caller falls back to the bundled one.
    """
    import httpx

    try:
        resp = httpx.get(TAGGED_CHANGELOG_URL.format(version=latest_version), timeout=timeout)
        resp.raise_for_status()
    except httpx.HTTPError:  # offline, timeout, non-2xx (a tag without the file)
        return None
    return entries_between(
        parse_changelog(resp.text), newer_than=current_version, up_to=latest_version
    )


def newer_changelog_cached(
    current_version: str,
    latest_version: str,
    *,
    timeout: float = 2.0,
) -> list[ChangelogEntry]:
    """Cached :func:`fetch_newer_changelog`, ``[]`` when unknown.

    A tag's changelog does not change, so a fetched result is kept until either
    version moves. A failed or empty lookup is kept for :data:`_EMPTY_TTL_HOURS`
    only, as for the highlights.
    """
    try:
        cached = json.loads(_NEWER_NOTES_CACHE_PATH.read_text(encoding="utf-8"))
        age = time.time() - float(cached["checked_at"])
        entries = [_entry_from_json(e) for e in cached.get("entries") or []]
        fresh = bool(entries) or 0 <= age < _EMPTY_TTL_HOURS * 3600
        if (
            cached.get("current") == current_version
            and cached.get("latest") == latest_version
            and fresh
        ):
            return entries
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # no cache yet, or a malformed one: fetch
    entries = fetch_newer_changelog(current_version, latest_version, timeout=timeout) or []
    with contextlib.suppress(OSError):  # a read-only home only costs a refetch
        _NEWER_NOTES_CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _NEWER_NOTES_CACHE_PATH.write_text(
            json.dumps(
                {
                    "checked_at": time.time(),
                    "current": current_version,
                    "latest": latest_version,
                    "entries": [asdict(e) for e in entries],
                }
            ),
            encoding="utf-8",
        )
    return entries


def _entry_from_json(raw: dict[str, Any]) -> ChangelogEntry:
    return ChangelogEntry(
        version=raw["version"],
        label=raw.get("label"),
        sections=tuple(
            ChangelogSection(name=s["name"], items=tuple(s.get("items") or ()))
            for s in raw.get("sections") or ()
        ),
        summary=raw.get("summary") or "",
        highlights=tuple(ChangelogHighlight(**h) for h in raw.get("highlights") or ()),
    )


# --- bundled changelog and release stats ----------------------------------

# Parsed files keyed by path, reused while the file's mtime is unchanged, so a
# request does not re-read and re-parse a 3,000-line changelog.
_FILE_CACHE: dict[Path, tuple[float, Any]] = {}


def _load_cached(path: Path, parse):
    mtime = path.stat().st_mtime
    hit = _FILE_CACHE.get(path)
    if hit and hit[0] == mtime:
        return hit[1]
    value = parse(path.read_text(encoding="utf-8"))
    _FILE_CACHE[path] = (mtime, value)
    return value


def load_bundled_changelog() -> list[ChangelogEntry]:
    """Parse repowise's bundled changelog, or ``[]`` if it cannot be read."""
    try:
        return list(_load_cached(BUNDLED_CHANGELOG_PATH, parse_changelog))
    except (OSError, UnicodeDecodeError):  # missing from an unusual install
        return []


def load_release_stats() -> dict[str, dict[str, Any]]:
    """Bundled "By the numbers" figures keyed by version, or ``{}``."""
    try:
        data = _load_cached(RELEASE_STATS_PATH, json.loads)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):  # missing, or not JSON
        return {}


__all__ = [
    "BUNDLED_CHANGELOG_PATH",
    "CHANGELOG_URL",
    "DEFAULT_TTL_HOURS",
    "PYPI_URL",
    "RELEASE_STATS_PATH",
    "TAGGED_CHANGELOG_URL",
    "ReleaseCheck",
    "check_latest_version_cached",
    "fetch_latest_version",
    "fetch_newer_changelog",
    "fetch_release_highlights",
    "is_newer_version",
    "latest_highlights_cached",
    "load_bundled_changelog",
    "load_release_stats",
    "newer_changelog_cached",
    "parse_release",
]
