"""Central connectivity layer between the OSS CLI and the Repowise hosted platform.

All traffic to ``api.repowise.dev`` flows through here. Today the only consumer
is anonymous, opt-out :mod:`telemetry`; this package is structured so future
hosted features (login, account, cross-machine sync) add a client method and a
sibling module rather than scattering ``httpx`` calls across the CLI.

Layout:

* :mod:`client`   — :class:`PlatformClient`, the single HTTP seam (auth-ready).
* :mod:`store`    — ``~/.repowise/platform.json`` read/write.
* :mod:`identity` — anonymous install id + per-process session id.
* :mod:`settings` — telemetry consent + env-var precedence.
* :mod:`telemetry` — the extendable event system.
"""

from __future__ import annotations

from repowise.cli.platform.client import PLATFORM_BASE_URL, PlatformClient, default_client

__all__ = [
    "PLATFORM_BASE_URL",
    "PlatformClient",
    "default_client",
]
