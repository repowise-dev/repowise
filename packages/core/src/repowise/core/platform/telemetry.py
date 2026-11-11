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

#: Attribute an exception carries to report the other failures it was raised
#: alongside. anyio reports a task group's death as one wrapper holding several
#: leaves; whoever unwraps it stamps the leaf class names here, and whoever
#: classifies the invocation reads them off. Named once, in the package both
#: sides already import, so a rename cannot silently unhook the two.
GROUP_LEAF_TYPES_ATTR = "repowise_group_leaf_types"

_TRUTHY = {"1", "true", "yes", "on"}


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


def network_disabled() -> bool:
    """True when ``REPOWISE_OFFLINE``, ``DO_NOT_TRACK`` or ``REPOWISE_TELEMETRY_DISABLED`` is set."""
    return any(
        _env_truthy(n) for n in ("REPOWISE_OFFLINE", "DO_NOT_TRACK", "REPOWISE_TELEMETRY_DISABLED")
    )


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
    from repowise.core.ci.base import CI_ENV_VARS

    return any(os.environ.get(var) for var in CI_ENV_VARS)


#: Opt back in to delivery under a test runner. Only telemetry's own tests set it.
TESTS_OPT_IN_ENV = "REPOWISE_TELEMETRY_IN_TESTS"


def pytest_in_env() -> bool:
    """Whether a pytest session is in progress, judged from the environment.

    The env survives a subprocess boundary, so a real ``repowise`` command a
    test spawns sees it too. ``PYTEST_CURRENT_TEST`` covers a running test;
    ``PYTEST_VERSION`` (pytest 8.1+) also covers collection and teardown.
    """
    return bool(os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get("PYTEST_VERSION"))


def under_test() -> bool:
    """Whether delivery is suppressed because a test runner is driving this process.

    Test fixtures relocate the home that holds ``anon_id``, so every test that
    reaches the emitter would otherwise register a brand new install. Applies to
    any project's suite that exercises repowise, not only this one.
    """
    if _env_truthy(TESTS_OPT_IN_ENV):
        return False
    import sys

    return pytest_in_env() or "pytest" in sys.modules


#: Host labels, checked in order. Agents come before editors because an agent
#: running inside an editor terminal sees both; Cursor before VS Code because
#: Cursor's terminal also sets ``TERM_PROGRAM=vscode``.
_HOST_ENV_VARS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("claude_code", ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT")),
    (
        "codex",
        (
            "CODEX_SANDBOX",
            "CODEX_SANDBOX_NETWORK_DISABLED",
            "CODEX_MANAGED_BY_NPM",
            "CODEX_MANAGED_BY_BUN",
        ),
    ),
    ("cursor", ("CURSOR_TRACE_ID", "CURSOR_AGENT")),
    ("vscode", ("VSCODE_PID", "VSCODE_IPC_HOOK_CLI")),
)

#: Every value :func:`detect_client` can return.
CLIENT_LABELS = frozenset({label for label, _ in _HOST_ENV_VARS} | {"ci", "terminal"})


def detect_client() -> str:
    """Name the host that launched this process, from its environment alone.

    One fixed label, never an env value: ``claude_code``, ``codex``,
    ``cursor``, ``vscode``, ``ci``, else ``terminal``.
    """
    for label, names in _HOST_ENV_VARS:
        if any(os.environ.get(name) for name in names):
            return label
    if os.environ.get("TERM_PROGRAM", "").lower() == "vscode":
        return "vscode"
    return "ci" if _is_ci() else "terminal"


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
        "properties": {"client": detect_client(), **properties},
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
        if network_disabled():
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
        if under_test():
            # After the debug print, so a test can still see what would leave.
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
