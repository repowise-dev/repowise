"""Central client for the Repowise hosted platform (``api.repowise.dev``).

This is the single seam between the OSS CLI and the hosted product. Today its
only consumer is anonymous telemetry; tomorrow it will carry authenticated
calls (login, account, cross-machine sync). Keeping *all* hosted connectivity
here means a new hosted feature adds a method, not another ad-hoc ``httpx``
call scattered across the CLI.

Design rules:

* **Production URL only.** There is no localhost override in the OSS package.
  Backend development points the private backend repo at a local server; the
  shipped CLI always talks to ``https://api.repowise.dev``.
* **Fail-silent.** The OSS CLI must work fully offline, so every method
  swallows network/parse errors and returns a sentinel instead of raising.
* **Auth-ready.** :meth:`PlatformClient._auth_headers` is the one place a future
  bearer token gets attached, so every platform call becomes authenticated at
  once when login lands.
"""

from __future__ import annotations

from typing import Any

#: Production base URL for the hosted platform. Intentionally not configurable
#: from the OSS package (see module docstring).
PLATFORM_BASE_URL = "https://api.repowise.dev"

#: Short default timeout — platform calls are best-effort and must never stall
#: a CLI command waiting on the network.
DEFAULT_TIMEOUT = 2.0


def _user_agent() -> str:
    try:
        from repowise.cli import __version__

        return f"repowise-cli/{__version__}"
    except Exception:
        return "repowise-cli"


class PlatformClient:
    """Best-effort HTTP client for the hosted platform.

    All methods return a sentinel on failure rather than raising; callers in
    the OSS CLI treat hosted connectivity as optional.
    """

    def __init__(
        self,
        base_url: str = PLATFORM_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # -- header assembly ---------------------------------------------------

    def _auth_headers(self) -> dict[str, str]:
        """Return auth headers for an authenticated platform call.

        Empty today: the OSS CLI makes only anonymous calls. When the pip
        package gains login this reads the stored bearer token from the
        credential store, and every platform call is authenticated uniformly
        without touching individual call sites.
        """
        return {}

    def _headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        headers = {
            "User-Agent": _user_agent(),
            "Content-Type": "application/json",
        }
        headers.update(self._auth_headers())
        if extra:
            headers.update(extra)
        return headers

    # -- verbs -------------------------------------------------------------

    def post(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        timeout: float | None = None,
    ) -> bool:
        """POST ``payload`` as JSON to ``path``. Returns success, never raises."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            import httpx

            resp = httpx.post(
                url,
                json=payload,
                headers=self._headers(),
                timeout=timeout or self.timeout,
            )
            resp.raise_for_status()
            return True
        except Exception:
            # Network, JSON, HTTP-status — all advisory. The CLI works offline.
            return False

    def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any] | None:
        """GET ``path`` and return parsed JSON, or ``None`` on any failure."""
        url = f"{self.base_url}/{path.lstrip('/')}"
        try:
            import httpx

            resp = httpx.get(
                url,
                params=params,
                headers=self._headers(),
                timeout=timeout or self.timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return None


#: Process-wide default client. Callers that don't need a custom base URL or
#: timeout share this instance.
default_client = PlatformClient()
