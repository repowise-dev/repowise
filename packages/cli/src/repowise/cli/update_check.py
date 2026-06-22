"""Best-effort check for whether the installed ``repowise`` CLI is stale.

This is intentionally conservative:

* Current version comes from :data:`repowise.cli.__version__`.
* Latest version is fetched from the PyPI JSON endpoint via the already-bundled
  ``httpx`` with a short timeout.
* An install-method-appropriate upgrade *command* is suggested, but never run.

Network failures are swallowed — callers (e.g. ``repowise doctor``) must keep
working when PyPI is unreachable. This module never raises and never invokes a
package manager.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PYPI_URL = "https://pypi.org/pypi/repowise/json"

#: Default freshness window for the cached PyPI check. Within this window the
#: last fetched ``latest_version`` is reused so routine commands never hit the
#: network. 24h keeps the advisory current without nagging PyPI on every run.
DEFAULT_TTL_HOURS = 24.0


@dataclass(frozen=True)
class UpdateCheck:
    """Result of a CLI update check.

    ``update_available`` is ``None`` when the latest version could not be
    determined (network error, parse failure) so callers can distinguish
    "up to date" from "unknown".
    """

    current_version: str
    latest_version: str | None
    resolved_executable: str | None
    running_executable: str
    python: str
    update_available: bool | None
    suggested_command: str
    install_hint: str
    error: str | None = None


def _parse_release(version: str) -> tuple[int, ...] | None:
    """Parse the leading numeric release parts of a version string.

    Returns a tuple of ints for the dotted numeric prefix (so ``0.15.2`` ->
    ``(0, 15, 2)``). Pre-release/local suffixes such as ``-rc1`` or ``+local``
    are ignored. Returns ``None`` when no numeric component can be parsed.
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
    """Return ``True`` if ``latest`` is a strictly newer release than ``current``.

    Uses a simple numeric-release comparison. If either version cannot be
    parsed, returns ``False`` (no update decision) rather than raising.
    """
    lat = _parse_release(latest)
    cur = _parse_release(current)
    if lat is None or cur is None:
        return False
    # Pad to equal length so 0.15 compares correctly against 0.15.2.
    length = max(len(lat), len(cur))
    lat = lat + (0,) * (length - len(lat))
    cur = cur + (0,) * (length - len(cur))
    return lat > cur


def suggest_update_command(executable: str | None, python: str) -> tuple[str, str]:
    """Suggest an upgrade command and human-readable install hint.

    Heuristic, based on the resolved executable path. Returns
    ``(command, hint)``. When the install method is unknown, falls back to a
    safe ``<python> -m pip install -U repowise``.
    """
    path = (executable or "").replace("\\", "/").lower()

    if "pipx" in path:
        return ("pipx upgrade repowise", "pipx")
    # uv tool installs live under a uv data dir, e.g.
    # ~/.local/share/uv/tools/... or a uv-managed bin shim.
    if "/uv/" in path or "uv/tools" in path or "/uv/tools/" in path:
        return ("uv tool upgrade repowise", "uv tool")
    return (f"{python} -m pip install -U repowise", "pip")


def _editable_checkout() -> Path | None:
    """Return the repo root when ``repowise`` is running from a source checkout.

    Detected by the package not living under ``site-packages``/``dist-packages``
    while a parent directory carries both ``pyproject.toml`` and ``.git``.
    """
    try:
        from repowise.cli import __file__ as cli_file
    except Exception:
        return None
    pkg = Path(cli_file).resolve()
    if "site-packages" in pkg.parts or "dist-packages" in pkg.parts:
        return None
    for parent in pkg.parents:
        if (parent / "pyproject.toml").exists() and (parent / ".git").exists():
            return parent
    return None


def get_cli_update_check(timeout: float = 2.0) -> UpdateCheck:
    """Check whether the installed ``repowise`` CLI is out of date.

    Always returns an :class:`UpdateCheck`; never raises. On network/parse
    failure, ``latest_version`` is ``None``, ``update_available`` is ``None``,
    and ``error`` carries a short description.
    """
    from repowise.cli import __version__

    current = __version__
    resolved = shutil.which("repowise")
    running = sys.argv[0] if sys.argv else ""
    python = sys.executable or "python"

    checkout = _editable_checkout()
    if checkout is not None:
        suggested = f"cd {checkout} && git pull && {python} -m pip install -e ."
        hint = "editable"
    else:
        suggested, hint = suggest_update_command(resolved or running, python)

    latest: str | None = None
    error: str | None = None
    try:
        import httpx

        resp = httpx.get(PYPI_URL, timeout=timeout)
        resp.raise_for_status()
        fetched = resp.json()["info"]["version"]
        # Only accept a version we can actually compare; otherwise leave
        # latest=None so callers report "unknown" rather than a false "latest".
        if _parse_release(fetched) is None:
            error = f"unparsable latest version: {fetched!r}"
        else:
            latest = fetched
    except Exception as exc:  # network, JSON, missing key — all advisory
        error = str(exc) or exc.__class__.__name__

    if latest is None:
        update_available: bool | None = None
    else:
        update_available = is_newer_version(latest, current)

    return UpdateCheck(
        current_version=current,
        latest_version=latest,
        resolved_executable=resolved,
        running_executable=running,
        python=python,
        update_available=update_available,
        suggested_command=suggested,
        install_hint=hint,
        error=error,
    )


def _cache_path() -> Path:
    from repowise.cli.helpers import user_global_dir

    return user_global_dir() / "update-check.json"


def _build_from_cached_latest(latest: str | None, error: str | None) -> UpdateCheck:
    """Assemble an :class:`UpdateCheck` from a cached ``latest`` + local facts.

    Only ``latest_version`` requires the network; everything else (current
    version, resolved executable, suggested command) is cheap and local, so a
    cache hit recomputes them freshly rather than persisting stale paths.
    """
    from repowise.cli import __version__

    current = __version__
    resolved = shutil.which("repowise")
    running = sys.argv[0] if sys.argv else ""
    python = sys.executable or "python"
    checkout = _editable_checkout()
    if checkout is not None:
        suggested = f"cd {checkout} && git pull && {python} -m pip install -e ."
        hint = "editable"
    else:
        suggested, hint = suggest_update_command(resolved or running, python)
    update_available = is_newer_version(latest, current) if latest else None
    return UpdateCheck(
        current_version=current,
        latest_version=latest,
        resolved_executable=resolved,
        running_executable=running,
        python=python,
        update_available=update_available,
        suggested_command=suggested,
        install_hint=hint,
        error=error,
    )


def get_cli_update_check_cached(
    ttl_hours: float = DEFAULT_TTL_HOURS, timeout: float = 2.0
) -> UpdateCheck:
    """TTL-cached variant of :func:`get_cli_update_check`. Never raises.

    Reuses the last fetched ``latest_version`` from ``~/.repowise/update-check.json``
    while it is younger than *ttl_hours*, so routine commands stay offline. On a
    miss it performs the live check and persists the result. Cache I/O is
    best-effort: any read/write failure degrades to a live check.
    """
    path = _cache_path()
    try:
        cached = json.loads(path.read_text(encoding="utf-8"))
        age = time.time() - float(cached["checked_at"])
        if 0 <= age < ttl_hours * 3600:
            return _build_from_cached_latest(cached.get("latest_version"), cached.get("error"))
    except Exception:
        pass  # missing / corrupt / stale -> fall through to a live check

    result = get_cli_update_check(timeout=timeout)
    with contextlib.suppress(Exception):  # caching is opportunistic
        path.write_text(
            json.dumps(
                {
                    "checked_at": time.time(),
                    "latest_version": result.latest_version,
                    "error": result.error,
                }
            ),
            encoding="utf-8",
        )
    return result
