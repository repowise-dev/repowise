"""GitLab REST API (v4): one request helper and merge-request notes.

Works the same on gitlab.com and a self-managed host: the caller passes the
API root, which GitLab CI exposes as ``CI_API_V4_URL``. The token goes in a
``PRIVATE-TOKEN`` header, so a project, group or personal access token works.
A CI job token does not: GitLab does not let it write merge-request notes.

Errors raise :class:`GitLabError` with a message that says what to check.
A busy or briefly failing GitLab (429, a 5xx, a dropped connection) is
retried with backoff first; see :meth:`GitLabClient._send`.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Iterator, Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

#: Largest page GitLab serves.
PER_PAGE = 100

#: Answers worth one more try: a busy or briefly failing GitLab.
_RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Methods safe to send twice. A POST that timed out or met a 5xx may have
#: landed, and sending it again would post a second note.
_IDEMPOTENT = frozenset({"GET", "HEAD", "PUT", "DELETE"})
#: Seconds before each retry; a ``Retry-After`` wins, clamped to the cap.
_BACKOFF = (1.0, 4.0)
_RETRY_AFTER_CAP = 30.0
#: Failures that clear up on their own: a timeout, a dropped or broken answer.
#: A proxy or URL scheme error would only repeat.
_TRANSIENT = (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)

_STATUS_HINTS = {
    401: "GitLab rejected the token (401): it is missing, expired or revoked.",
    403: (
        "The token may not do this (403): it needs the api scope and at least the "
        "Reporter role on the project."
    ),
    404: (
        "GitLab found nothing at {path} (404): check the API URL, project and merge "
        "request, and that the token can see the project."
    ),
}


class GitLabError(Exception):
    """A GitLab request failed; ``status`` is the HTTP status, ``None`` when unreachable."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitLabClient:
    """Requests against one GitLab API root (``https://gitlab.example.com/api/v4``).

    *transport* stands in for the network in tests.
    """

    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self._http = httpx.Client(
            headers={"PRIVATE-TOKEN": token}, timeout=timeout, transport=transport
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitLabClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
    ) -> Any:
        """The decoded JSON of ``<api_url>/<path>``; raises :class:`GitLabError`."""
        response = self._send(method, f"{self.api_url}/{path.lstrip('/')}", path, params, json)
        return _decode(response, path)

    def paginate(self, path: str, *, params: Mapping[str, Any] | None = None) -> Iterator[Any]:
        """Every item of a list endpoint, page by page, following GitLab's ``Link`` header."""
        url: str | None = f"{self.api_url}/{path.lstrip('/')}"
        query: Mapping[str, Any] | None = {"per_page": PER_PAGE, **(params or {})}
        while url:
            response = self._send("GET", url, path, query, None)
            page = _decode(response, path)
            if not isinstance(page, list):
                raise GitLabError(f"GitLab's answer to {path} was not a list.")
            yield from page
            url = response.links.get("next", {}).get("url")
            # The next link carries the whole query.
            query = None
            if url and not _same_origin(url, self.api_url):
                raise GitLabError(f"GitLab sent a next page on another host: {url}")

    def _send(
        self,
        method: str,
        url: str,
        path: str,
        params: Mapping[str, Any] | None,
        json: Any,
    ) -> httpx.Response:
        """One request, retried after a busy answer or a dropped connection.

        429 and a connection that never opened are retried for every method:
        GitLab did nothing. A 5xx, a timeout or a broken answer is retried only
        for idempotent methods: a POST may have landed.
        """
        idempotent = method.upper() in _IDEMPOTENT
        for wait in (*_BACKOFF, None):
            try:
                response = self._http.request(method, url, params=params, json=json)
            except httpx.HTTPError as exc:
                if wait is None or not _worth_retrying(idempotent, exc=exc):
                    raise GitLabError(f"Could not reach GitLab at {self.api_url}: {exc}") from exc
                time.sleep(wait)
                continue
            if wait is None or not _worth_retrying(idempotent, status=response.status_code):
                break
            time.sleep(_retry_wait(response, wait))
        if response.is_success:
            return response
        raise _error(response, method, path)


def _worth_retrying(idempotent: bool, *, status: int = 0, exc: Exception | None = None) -> bool:
    if exc is not None:
        if _is_tls_failure(exc):
            return False  # a bad certificate or handshake fails the same way every time
        never_sent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
        return never_sent or (idempotent and isinstance(exc, _TRANSIENT))
    return status == 429 or (idempotent and status in _RETRY_STATUSES)


def _is_tls_failure(exc: BaseException) -> bool:
    """httpx raises a TLS handshake or certificate failure as ConnectError; the
    ssl error sits in the exception chain."""
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, ssl.SSLError):
            return True
        seen = seen.__cause__ or seen.__context__
    return False


def _retry_wait(response: httpx.Response, backoff: float) -> float:
    """GitLab's ``Retry-After`` in seconds, clamped to the cap, else *backoff*.

    Parsed here, not with the LLM providers' ``parse_retry_after``: that one
    drops a value over 600 s, and a long ask should wait the cap, not 1 s.
    """
    header = response.headers.get("Retry-After", "")
    try:
        asked = float(header)
    except ValueError:
        try:  # the HTTP-date form
            asked = (parsedate_to_datetime(header) - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError):
            return backoff
    return min(asked, _RETRY_AFTER_CAP) if asked > 0 else backoff


def _error(response: httpx.Response, method: str, path: str) -> GitLabError:
    status = response.status_code
    if status in _STATUS_HINTS:
        return GitLabError(_STATUS_HINTS[status].format(path=path), status)
    return GitLabError(f"GitLab answered {status} to {method} {path}: {_reason(response)}", status)


def _decode(response: httpx.Response, path: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise GitLabError(
            f"GitLab's answer to {path} was not JSON: check that the API URL ends in /api/v4."
        ) from exc


def _same_origin(url: str, api_url: str) -> bool:
    a, b = urlsplit(url), urlsplit(api_url)
    return (a.scheme, a.netloc) == (b.scheme, b.netloc)


def _reason(response: httpx.Response) -> str:
    """GitLab's own message (``message`` or ``error``), else the start of the body."""
    try:
        doc = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(doc, dict):
        reason = doc.get("message") or doc.get("error_description") or doc.get("error")
        if reason:
            return str(reason)
    return str(doc)[:200]


def project_path(project: int | str) -> str:
    """``projects/<id>``: a numeric id, or a ``group/name`` path encoded as one segment."""
    return f"projects/{quote(str(project), safe='')}"


def _notes_path(project: int | str, mr_iid: int) -> str:
    return f"{project_path(project)}/merge_requests/{int(mr_iid)}/notes"


def find_note(
    client: GitLabClient, project: int | str, mr_iid: int, marker: str
) -> dict[str, Any] | None:
    """The token's own note on the merge request whose body starts with *marker*.

    Only the token's own: anyone can post a note that starts with the marker.
    """
    me = client.request("GET", "user")["id"]
    for note in client.paginate(
        _notes_path(project, mr_iid), params={"sort": "asc", "order_by": "created_at"}
    ):
        if (
            not note.get("system")
            and (note.get("author") or {}).get("id") == me
            and str(note.get("body") or "").startswith(marker)
        ):
            return note
    return None


def create_note(client: GitLabClient, project: int | str, mr_iid: int, body: str) -> dict[str, Any]:
    return client.request("POST", _notes_path(project, mr_iid), json={"body": body})


def update_note(
    client: GitLabClient, project: int | str, mr_iid: int, note_id: int, body: str
) -> dict[str, Any]:
    return client.request(
        "PUT", f"{_notes_path(project, mr_iid)}/{int(note_id)}", json={"body": body}
    )


def upsert_note(
    client: GitLabClient,
    project: int | str,
    mr_iid: int,
    body: str,
    *,
    existing: Mapping[str, Any] | None,
) -> tuple[dict[str, Any], bool]:
    """Edit *existing* (from :func:`find_note`) to *body*, or post a new note.

    Returns the note and whether it was created. A note deleted since it was
    found is replaced by a new one.
    """
    if existing is not None:
        try:
            return update_note(client, project, mr_iid, existing["id"], body), False
        except GitLabError as exc:
            if exc.status != 404:
                raise
    return create_note(client, project, mr_iid, body), True
