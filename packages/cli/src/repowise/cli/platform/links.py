"""Links from the CLI to repowise.dev, tagged with where they were shown.

Every link carries ``src`` (which CLI moment it came from), so a signup that
starts from it is credited to that moment. Only the sign-in URL also carries
the anonymous install id (``aid``, telemetry on only); a link someone merely
opens never does.
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlencode

#: The hosted site. Production only, matching the PlatformClient design rule.
SITE_URL = "https://repowise.dev"


def attribution_params(src: str) -> dict[str, str]:
    """``{"src": src}``, plus ``aid`` when telemetry is enabled. Sign-in only."""
    params = {"src": src}
    with contextlib.suppress(Exception):
        from repowise.cli.platform import identity, settings

        if settings.is_enabled():
            params["aid"] = identity.get_anonymous_id()
    return params


def site_link(
    path: str,
    src: str,
    *,
    fragment: str | None = None,
    params: dict[str, str] | None = None,
) -> str:
    """``https://repowise.dev/<path>?<params>&src=…[#fragment]``."""
    query = dict(params or {})
    query["src"] = src
    url = f"{SITE_URL}/{path.lstrip('/')}?{urlencode(query)}"
    return f"{url}#{fragment}" if fragment else url
