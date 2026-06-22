"""Telemetry consent resolution and env-var precedence.

Telemetry is **opt-out**: enabled by default, with a one-time loud notice and
trivially easy disable. This module is the single source of truth for whether
telemetry may be sent, resolving the stored consent against the environment.

Precedence (highest first):

1. ``DO_NOT_TRACK`` — the cross-tool `console do-not-track` standard. Any
   truthy value is a hard off and also suppresses the first-run notice.
2. ``REPOWISE_TELEMETRY_DISABLED`` — tool-specific hard off.
3. Stored consent (``telemetry_enabled`` in ``platform.json``) when the user
   has explicitly run ``repowise telemetry disable``/``enable``.
4. Default: enabled.

``REPOWISE_TELEMETRY_DEBUG`` does not change the enabled state — it makes the
emitter print the exact payload to stderr instead of sending it, so anyone can
verify what would leave their machine.
"""

from __future__ import annotations

import os

from repowise.cli.platform import store

_ENABLED_KEY = "telemetry_enabled"
_NOTICE_SHOWN_KEY = "telemetry_notice_shown"

_TRUTHY = {"1", "true", "yes", "on"}


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def do_not_track() -> bool:
    """Return whether the cross-tool ``DO_NOT_TRACK`` standard opts out."""
    return _env_truthy("DO_NOT_TRACK")


def debug_mode() -> bool:
    """Return whether ``REPOWISE_TELEMETRY_DEBUG`` is set (print, don't send)."""
    return _env_truthy("REPOWISE_TELEMETRY_DEBUG")


def is_enabled() -> bool:
    """Return whether telemetry may currently be sent."""
    if do_not_track():
        return False
    if _env_truthy("REPOWISE_TELEMETRY_DISABLED"):
        return False
    # Opt-out default: enabled unless the user explicitly stored a disable.
    stored = store.load().get(_ENABLED_KEY)
    return stored is not False


def set_enabled(enabled: bool) -> None:
    """Persist an explicit enable/disable verdict from the user."""
    store.update(**{_ENABLED_KEY: bool(enabled)})


def notice_shown() -> bool:
    return bool(store.load().get(_NOTICE_SHOWN_KEY))


def mark_notice_shown() -> None:
    store.update(**{_NOTICE_SHOWN_KEY: True})


def disabled_reason() -> str | None:
    """Return a short human reason telemetry is off, or ``None`` if on.

    Used by ``repowise telemetry status`` to explain *why* it is disabled.
    """
    if do_not_track():
        return "DO_NOT_TRACK is set"
    if _env_truthy("REPOWISE_TELEMETRY_DISABLED"):
        return "REPOWISE_TELEMETRY_DISABLED is set"
    if store.load().get(_ENABLED_KEY) is False:
        return "disabled via 'repowise telemetry disable'"
    return None
