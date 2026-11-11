"""This machine's choices: telemetry, tips, network use, saved keys."""

from __future__ import annotations

from repowise.core.settings.areas._common import factory
from repowise.core.settings.model import Setting

AREA = "account"
_s = factory(AREA)
_PLATFORM_FILE = "~/.repowise/platform.json"
_NO_SAVE = frozenset({"", "0", "false", "no"})

SETTINGS: tuple[Setting, ...] = (
    _s(
        "telemetry_enabled",
        "bool",
        True,
        "Send anonymous usage counts. DO_NOT_TRACK, REPOWISE_TELEMETRY_DISABLED "
        "and REPOWISE_OFFLINE turn it off whatever this says.",
        scope="user",
        file=_PLATFORM_FILE,
        writer="repowise.cli.platform.settings:set_enabled",
    ),
    _s(
        "hints_enabled",
        "bool",
        True,
        "Show occasional repowise.dev tips in the CLI and web UI.",
        scope="user",
        file=_PLATFORM_FILE,
        writer="repowise.cli.hints:set_enabled",
    ),
    _s("REPOWISE_OFFLINE", "bool", False, "Block repowise's own network calls: telemetry, publish, sign-in checks."),
    _s("REPOWISE_TELEMETRY_DISABLED", "bool", False, "Turn off anonymous usage telemetry."),
    _s("REPOWISE_TELEMETRY_DEBUG", "bool", False, "Print the telemetry payload instead of sending it."),
    _s(
        "REPOWISE_TELEMETRY_IN_TESTS",
        "bool",
        False,
        "Let telemetry send while running under pytest.",
        internal=True,
    ),
    _s("REPOWISE_NO_HINTS", "bool", False, "Hide repowise.dev tips in the CLI and web UI."),
    _s(
        "REPOWISE_NO_SAVE_KEY",
        "bool",
        False,
        "Do not copy an API key into the repo's .repowise/.env. For CI and shared machines.",
        env_false=_NO_SAVE,
    ),
    _s(
        "REPOWISE_CHANGELOG",
        "str",
        None,
        "A changelog file for 'what's new' to read instead of the bundled one.",
        internal=True,
    ),
)
