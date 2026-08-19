"""Anonymous, opt-out telemetry for the core + server layers.

A small, self-contained emitter so ``repowise.server`` (which never imports
``repowise.cli``) can record events. It shares the on-disk and wire contract
with the richer CLI telemetry in ``repowise.cli.platform.telemetry``:

* the same ``~/.repowise/platform.json`` anonymous install id,
* the same ``DO_NOT_TRACK`` / ``REPOWISE_TELEMETRY_DISABLED`` consent env vars,
* the same ``POST https://api.repowise.dev/telemetry/events`` endpoint,

so CLI ``command_run`` events and server ``mcp_tool_call`` events land in one
table and group by the same install. The CLI substrate could later delegate to
this module; it is kept separate for now to avoid churning the shipped,
well-tested CLI path (upgrade path, not duplication of a subsystem).

Privacy contract (identical to the CLI's): never put source code, file paths,
repo or symbol names, query text, flag values, or anything user-identifiable
into an event's ``properties``. Coarse enums, bucketed counts, and booleans
only. Reads are best-effort and this module never raises into a caller.
"""

from __future__ import annotations

import atexit
import contextlib
import json
import os
import platform
import threading
import uuid
from pathlib import Path
from typing import Any

#: Production ingest endpoint (same as the CLI's PLATFORM_BASE_URL + path).
#: Intentionally not configurable from the shipped package.
_INGEST_URL = "https://api.repowise.dev/telemetry/events"

#: Best-effort: telemetry must never stall or break a tool call.
_TIMEOUT = 2.0

_TRUTHY = {"1", "true", "yes", "on"}

#: Common CI signals (kept in sync with the CLI's environment module).
_CI_ENV_VARS = (
    "CI",
    "GITHUB_ACTIONS",
    "GITLAB_CI",
    "BUILDKITE",
    "JENKINS_URL",
    "TEAMCITY_VERSION",
    "TF_BUILD",
)

#: Groups the events emitted by this process (e.g. one MCP server session).
_SESSION_ID = uuid.uuid4().hex

#: In-flight send threads, joined briefly at exit so short runs still deliver.
_pending: list[threading.Thread] = []

#: Cache the platform.json read so a burst of tool calls doesn't re-hit disk on
#: every event. The file (anon_id + consent) changes rarely; a short TTL keeps a
#: mid-session ``repowise telemetry disable`` effective within seconds while
#: keeping steady-state reads off disk. Only ever read on the background thread.
_STATE_TTL = 30.0
_state_cache: tuple[float, dict[str, Any]] | None = None


def _platform_json_path() -> Path:
    """The user-global ``~/.repowise/platform.json`` the CLI already maintains."""
    return Path.home() / ".repowise" / "platform.json"


def _load_state() -> dict[str, Any]:
    global _state_cache
    import time

    now = time.monotonic()
    if _state_cache is not None and (now - _state_cache[0]) < _STATE_TTL:
        return _state_cache[1]
    try:
        state = json.loads(_platform_json_path().read_text(encoding="utf-8"))
    except Exception:
        state = {}
    _state_cache = (now, state)
    return state


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def debug_mode() -> bool:
    """``REPOWISE_TELEMETRY_DEBUG`` prints the payload to stderr instead of sending."""
    return _env_truthy("REPOWISE_TELEMETRY_DEBUG")


def get_anonymous_id() -> str | None:
    """Return the persistent anonymous install id, or ``None`` if unset.

    Read-only: the CLI owns creation of ``anon_id`` (on first command), so a
    server that somehow runs before any CLI command simply sends ``None`` rather
    than minting a competing id that would fragment install counts.
    """
    anon = _load_state().get("anon_id")
    return anon if isinstance(anon, str) and anon else None


def _is_ci() -> bool:
    return any(os.environ.get(var) for var in _CI_ENV_VARS)


def _version() -> str:
    try:
        from repowise.core import __version__

        return __version__
    except Exception:
        return "unknown"


def _base_facts() -> dict[str, object]:
    info = platform.python_version_tuple()
    return {
        "os": platform.system().lower() or "unknown",
        "arch": platform.machine().lower() or "unknown",
        "python_version": f"{info[0]}.{info[1]}",  # major.minor only, no patch leak
        "is_ci": _is_ci(),
    }


def build_envelope(event: str, properties: dict[str, Any]) -> dict[str, object]:
    """Assemble the anonymous wire envelope (same shape as the CLI's)."""
    envelope: dict[str, object] = {
        "event": event,
        "anon_id": get_anonymous_id(),
        "session_id": _SESSION_ID,
        "cli_version": _version(),
        "properties": properties,
    }
    envelope.update(_base_facts())
    return envelope


def _post(envelope: dict[str, object]) -> None:
    with contextlib.suppress(Exception):
        import httpx

        httpx.post(_INGEST_URL, json=envelope, timeout=_TIMEOUT)


def _deliver(event: str, properties: dict[str, Any]) -> None:
    """Consent-check (incl. the disk-backed stored disable), build, and send.

    Runs on the background thread so no disk read or network call touches the
    caller's hot path.
    """
    with contextlib.suppress(Exception):
        if _load_state().get("telemetry_enabled") is False:
            return  # stored opt-out; env hard-offs were already handled inline
        _post(build_envelope(event, properties))


def record_event(event: str, properties: dict[str, Any] | None = None) -> None:
    """Record an anonymous event: respect consent, then debug-print or send.

    Performance: the caller's hot path does only two env-var reads and a thread
    hand-off. All disk I/O (anon_id + stored-consent) and the network POST run on
    a daemon thread, so a slow or unreachable backend never delays a tool call.
    Never raises.
    """
    try:
        # Cheap synchronous gate: env hard-offs need neither disk nor a thread.
        if _env_truthy("DO_NOT_TRACK") or _env_truthy("REPOWISE_TELEMETRY_DISABLED"):
            return
        props = dict(properties or {})

        if debug_mode():
            import sys

            print(
                "[repowise telemetry] would send:\n"
                + json.dumps(build_envelope(event, props), indent=2),
                file=sys.stderr,
            )
            return

        thread = threading.Thread(target=_deliver, args=(event, props), daemon=True)
        thread.start()
        _pending[:] = [t for t in _pending if t.is_alive()]
        _pending.append(thread)
    except Exception:
        # Telemetry must never break a tool call.
        return


@atexit.register
def _flush() -> None:
    """Give in-flight sends a brief moment to complete on process exit.

    Bounded to ~2s total so exit is never noticeably delayed even if the backend
    is hung. A hard-killed MCP server (watchdog) may skip this, which is fine:
    per-call sends are best-effort and each call already dispatched immediately.
    """
    import time

    remaining = 2.0
    for thread in _pending:
        if remaining <= 0:
            break
        start = time.monotonic()
        with contextlib.suppress(Exception):
            thread.join(timeout=remaining)
        remaining -= time.monotonic() - start
