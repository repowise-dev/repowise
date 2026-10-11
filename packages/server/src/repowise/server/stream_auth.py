"""Short-lived, single-job-scoped tokens for the SSE progress stream.

An ``EventSource`` cannot set request headers, so a browser can't present the
bearer API key on ``GET /api/jobs/{id}/stream``. Instead the job-launching
endpoints mint a token bound to one job id with a short TTL and hand it back
with the job id; the stream route accepts it as a ``?token=`` query parameter
alongside the normal bearer path.

The token authorizes reading exactly one job's progress and nothing else, so if
it does leak into a proxy access log (the very reason the raw API key must never
go in the query string) the exposure is one job's page counts, and only until it
expires. The signature covers the job id, so a token minted for one job can't be
replayed against another.

Signing secret: the ``REPOWISE_API_KEY`` when set, so tokens verify across any
worker process that shares it; otherwise a per-process random secret (a
single-process local ``serve`` where auth is loopback-open anyway, so the token
path is not even reached).
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
import time
from hashlib import sha256

# Bucket size for the token's expiry. Long enough to survive a page reload
# mid-job and a slow first frame; short enough that a leaked token is stale
# soon. Bucketing (see mint_stream_token) means the real lifetime lands between
# one and two of these windows, i.e. one to two hours. A job that outlives it
# re-mints on the next authenticated ``GET /api/jobs/{id}``.
STREAM_TOKEN_TTL_SECONDS = 3600

# Stable for the lifetime of the process, used only when no API key is set.
_EPHEMERAL_SECRET = secrets.token_bytes(32)


def _signing_secret() -> bytes:
    key = os.environ.get("REPOWISE_API_KEY")
    if key:
        return key.encode("utf-8")
    return _EPHEMERAL_SECRET


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def mint_stream_token(job_id: str, ttl_seconds: int = STREAM_TOKEN_TTL_SECONDS) -> str:
    """Return a signed ``<payload>.<sig>`` token authorizing this job's stream.

    The expiry is quantized to a ``ttl_seconds`` bucket so repeated mints for the
    same job within a window return the *same* token. That stability matters:
    the web polls the job every few seconds and re-serializes it, and if the
    token string changed each poll the browser's EventSource URL would change and
    force a reconnect every poll. Bucketing gives every token between one and two
    full TTLs of life, so it never quantizes down to a near-dead token.
    """
    bucket = int(time.time()) // ttl_seconds
    exp = (bucket + 2) * ttl_seconds
    payload = f"{exp}:{job_id}"
    sig = hmac.new(_signing_secret(), payload.encode("utf-8"), sha256).digest()
    return f"{_b64(payload.encode('utf-8'))}.{_b64(sig)}"


def verify_stream_token(token: str | None, job_id: str) -> bool:
    """True iff ``token`` is a valid, unexpired token minted for ``job_id``."""
    if not token:
        return False
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64).decode("utf-8")
        sig = _unb64(sig_b64)
    except (ValueError, UnicodeDecodeError):
        return False

    expected = hmac.new(_signing_secret(), payload.encode("utf-8"), sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False

    exp_str, sep, embedded_job = payload.partition(":")
    if not sep or not embedded_job:
        return False
    # Constant-time id comparison so the token for job A never authorizes job B.
    if not hmac.compare_digest(embedded_job, job_id):
        return False
    try:
        exp = int(exp_str)
    except ValueError:
        return False
    return exp > int(time.time())
