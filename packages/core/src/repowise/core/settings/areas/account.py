"""This machine's choices: telemetry, tips, network use, saved keys."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "account"
_e = factory(AREA, scope="env")
_u = factory(AREA, scope="user")
_PLATFORM_FILE = "~/.repowise/platform.json"
_NO_SAVE = frozenset({"", "0", "false", "no"})

SETTINGS: tuple[Setting, ...] = (
    _u(
        "telemetry_enabled",
        "bool",
        True,
        "Send anonymous usage counts. DO_NOT_TRACK, REPOWISE_TELEMETRY_DISABLED "
        "and REPOWISE_OFFLINE turn it off whatever this says.",
        file=_PLATFORM_FILE,
        writer="repowise.cli.platform.settings:set_enabled",
    ),
    _u(
        "hints_enabled",
        "bool",
        True,
        "Show occasional repowise.dev tips in the CLI and web UI.",
        file=_PLATFORM_FILE,
        writer="repowise.cli.hints:set_enabled",
    ),
    _e("REPOWISE_OFFLINE", "bool", False, "Block repowise's own network calls: telemetry, update checks, plan status, "
        "agent sign-in checks and decision sync."),
    _e("REPOWISE_TELEMETRY_DISABLED", "bool", False, "Turn off anonymous usage telemetry."),
    _e("REPOWISE_TELEMETRY_DEBUG", "bool", False, "Print the telemetry payload instead of sending it."),
    _e(
        "REPOWISE_TELEMETRY_IN_TESTS",
        "bool",
        False,
        "Let telemetry send while running under pytest.",
        internal=True,
    ),
    _e("REPOWISE_NO_HINTS", "bool", False, "Hide repowise.dev tips in the CLI and web UI."),
    _e(
        "REPOWISE_NO_SAVE_KEY",
        "bool",
        False,
        "Do not copy an API key into the repo's .repowise/.env. For CI and shared machines.",
        env_false=_NO_SAVE,
    ),
    _e(
        "REPOWISE_CHANGELOG",
        "str",
        None,
        "A changelog file for 'what's new' to read instead of the bundled one.",
        internal=True,
    ),
)
