"""Links from the CLI to repowise.dev, tagged with where they were shown.

Every link carries ``src`` (which CLI moment it came from), so a signup that
starts from it is credited to that moment. The anonymous install id rides
along as ``aid`` only while telemetry is on; with telemetry off the site gets
``src`` alone.
"""

from __future__ import annotations

import contextlib
from urllib.parse import urlencode

#: The hosted site. Production only, matching the PlatformClient design rule.
SITE_URL = "https://repowise.dev"


def attribution_params(src: str) -> dict[str, str]:
    """``{"src": src}``, plus ``aid`` when telemetry is enabled."""
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
    """``https://repowise.dev/<path>?<params>&src=…[&aid=…][#fragment]``."""
    query = dict(params or {})
    query.update(attribution_params(src))
    url = f"{SITE_URL}/{path.lstrip('/')}?{urlencode(query)}"
    return f"{url}#{fragment}" if fragment else url
