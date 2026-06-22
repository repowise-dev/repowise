"""The one-time opt-out notice and the user-facing consent controls.

Telemetry is opt-out, so the trust contract is a *loud, once* announcement: the
first time any command runs after telemetry ships, we print a short notice to
stderr explaining what is collected and how to turn it off, then never nag
again. The notice is suppressed when telemetry is already off (incl.
``DO_NOT_TRACK``), in CI, and when stderr is not a TTY (piped/redirected
output), so it never corrupts machine-readable streams.
"""

from __future__ import annotations

import sys

from repowise.cli.platform import settings
from repowise.cli.platform.telemetry import environment

TELEMETRY_DOC_URL = "https://repowise.dev/telemetry"

_NOTICE = (
    "Repowise collects completely anonymous usage telemetry (commands run, "
    "versions, OS, success/duration) to help prioritize what to build.\n"
    "It never sees your code, file paths, or repo names. Opt out anytime with "
    "'repowise telemetry disable' or DO_NOT_TRACK=1.\n"
    f"Details: {TELEMETRY_DOC_URL}"
)


def maybe_show_notice() -> None:
    """Print the one-time telemetry notice if it is due. Never raises."""
    try:
        if not settings.is_enabled():
            return  # already opted out — nothing to announce
        if settings.notice_shown():
            return
        if environment.is_ci():
            # Mark shown so an interactive run later doesn't re-announce, but
            # don't spam CI logs.
            settings.mark_notice_shown()
            return
        if not sys.stderr.isatty():
            return  # piped/redirected — don't corrupt the stream; try again later

        print(f"\n{_NOTICE}\n", file=sys.stderr)
        settings.mark_notice_shown()
    except Exception:
        return


def status_lines() -> list[str]:
    """Return human-readable status lines for ``repowise telemetry status``."""
    enabled = settings.is_enabled()
    lines = [f"Telemetry: {'enabled' if enabled else 'disabled'}"]
    reason = settings.disabled_reason()
    if reason:
        lines.append(f"  reason: {reason}")
    if settings.debug_mode():
        lines.append("  debug: REPOWISE_TELEMETRY_DEBUG set (payloads printed, not sent)")
    lines.append(f"  details: {TELEMETRY_DOC_URL}")
    return lines
