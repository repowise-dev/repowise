"""One JSON request against a forge API, with the issue-source failure rules.

No retries: a sync runs again on the next ``repowise update``, so a busy or
rate-limited host raises :class:`SourceUnavailable` naming when to try again,
and a refused token raises :class:`SourceRefused`. Messages are written here
and never quote the request, so the token cannot leak into one.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ..sources import SourceRefused, SourceUnavailable

#: Statuses that mean the host is busy or briefly down.
_BUSY = frozenset({429, 500, 502, 503, 504})


class ForgeHttp:
    """Requests against one API endpoint with one token header.

    *transport* stands in for the network in tests. Redirects are not
    followed, so the token never goes to a host it was not meant for.
    """

    def __init__(
        self,
        label: str,
        headers: Mapping[str, str],
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.label = label
        #: The last answer's headers, for a rate limit the body reports (GraphQL).
        self.last_headers: Mapping[str, str] = {}
        self._http = httpx.Client(headers=dict(headers), timeout=timeout, transport=transport)

    def close(self) -> None:
        self._http.close()

    def send(
        self,
        method: str,
        url: str,
        *,
        params: Any = None,
        json: Any = None,
    ) -> Any:
        """The decoded JSON answer, or None for a 404."""
        try:
            response = self._http.request(method, url, params=params, json=json)
        except httpx.HTTPError as exc:
            raise SourceUnavailable(
                f"{self.label} did not answer ({type(exc).__name__}); sync again later."
            ) from None
        self.last_headers = response.headers
        if response.status_code == 404:
            return None
        if not response.is_success:
            raise self._failure(response)
        try:
            return response.json()
        except ValueError:
            raise SourceRefused(
                f"{self.label}'s answer was not JSON: check that the API URL is the API "
                "endpoint, not the web address."
            ) from None

    def _failure(self, response: httpx.Response) -> Exception:
        status = response.status_code
        retry_at = retry_point(response.headers)
        if retry_at is not None or status in _BUSY:
            when = f"after {retry_at:%Y-%m-%d %H:%M} UTC" if retry_at else "later"
            what = "rate limit is used up" if retry_at else f"answered {status}"
            return SourceUnavailable(f"{self.label}'s {what}; sync again {when}.")
        if status == 401:
            return SourceRefused(
                f"{self.label} rejected the token (401): it is missing, expired or revoked."
            )
        if status == 403:
            return SourceRefused(
                f"{self.label} refused the request (403): the token needs read access to "
                "this repository's pull or merge requests and issues."
            )
        return SourceRefused(f"{self.label} answered {status}.")


def retry_point(headers: Mapping[str, str], now: datetime | None = None) -> datetime | None:
    """When a rate-limited host says to come back, or None when it is not rate limiting.

    ``Retry-After`` (seconds) wins; else a used-up quota's reset time, which
    GitHub sends as ``x-ratelimit-reset`` and GitLab as ``RateLimit-Reset``
    (both epoch seconds).
    """
    now = now or datetime.now(UTC)
    after = headers.get("retry-after", "").strip()
    if after.isdigit():
        return now + timedelta(seconds=int(after))
    for remaining, reset in (
        ("x-ratelimit-remaining", "x-ratelimit-reset"),
        ("ratelimit-remaining", "ratelimit-reset"),
    ):
        if headers.get(remaining, "").strip() == "0":
            stamp = headers.get(reset, "").strip()
            if stamp.isdigit():
                return datetime.fromtimestamp(int(stamp), UTC)
    return None
