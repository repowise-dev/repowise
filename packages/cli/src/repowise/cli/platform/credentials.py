"""Hosted-account credential storage (``~/.repowise/credentials.json``).

Holds the tokens ``repowise login`` obtains, kept separate from
``platform.json`` (anonymous state) so secrets live in exactly one file with
owner-only permissions. Two token kinds:

* ``oauth`` — short-lived access token + rotating refresh token from the
  hosted authorization server (the browser sign-in path).
* ``api_key`` — a long-lived ``rw_live_…`` key the user pasted (the
  headless/SSH fallback). No expiry, no refresh.

Reads tolerate a missing or corrupt file (returning ``None``); writes create
the file with ``0600`` (best effort on Windows, where the user-profile ACL is
the effective guard). Refresh rotation is serialized across processes via a
sibling lock file — see :func:`refresh_lock`.
"""

from __future__ import annotations

import contextlib
import json
import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

CREDENTIALS_FILENAME = "credentials.json"
_LOCK_FILENAME = "credentials.lock"

#: Refresh this many seconds before the recorded expiry to absorb clock skew
#: and request latency.
EXPIRY_SKEW_SECONDS = 60

#: A lock file older than this is a crashed process, not a live refresh.
_LOCK_STALE_SECONDS = 30


def _path() -> Path:
    from repowise.cli.helpers import user_global_dir

    return user_global_dir() / CREDENTIALS_FILENAME


def load() -> dict[str, Any] | None:
    """Return stored credentials, or ``None`` if absent/corrupt."""
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, dict) and data.get("access_token") else None


def save(creds: dict[str, Any]) -> None:
    """Persist credentials with owner-only permissions.

    Written via ``os.open`` with mode ``0600`` so the file is never observable
    with wider permissions, then ``chmod`` for the pre-existing-file case.
    """
    path = _path()
    payload = json.dumps(creds, indent=2)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, payload.encode("utf-8"))
    finally:
        os.close(fd)
    with contextlib.suppress(OSError):
        os.chmod(path, 0o600)


def delete() -> None:
    with contextlib.suppress(OSError):
        _path().unlink()


def is_access_expired(creds: dict[str, Any]) -> bool:
    """Whether the access token needs a refresh before use.

    API-key credentials never expire. OAuth credentials with no recorded
    expiry are treated as expired (refresh resolves the truth).
    """
    if creds.get("token_kind") == "api_key":
        return False
    expires_at = creds.get("access_expires_at")
    if not isinstance(expires_at, (int, float)):
        return True
    return time.time() >= expires_at - EXPIRY_SKEW_SECONDS


def mark_stale() -> None:
    """Flag credentials as no longer refreshable (revoked/expired server-side).

    Keeps the account snapshot so ``whoami`` can explain the state, but
    :func:`load`-ing callers should treat ``stale`` as signed out.
    """
    creds = load()
    if creds is not None:
        creds["stale"] = True
        with contextlib.suppress(OSError):
            save(creds)


@contextlib.contextmanager
def refresh_lock(timeout: float = 10.0) -> Iterator[bool]:
    """Cross-process single-flight lock for token refresh.

    Two parallel CLI invocations must not both rotate the same refresh token
    (the server revokes the presented token on rotation, so the loser would
    sign the machine out). Yields ``True`` when the lock was acquired;
    ``False`` on timeout — the caller should then re-read credentials, since
    the holder probably refreshed them already.
    """
    lock_path = _path().with_name(_LOCK_FILENAME)
    deadline = time.time() + timeout
    acquired = False
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.close(fd)
            acquired = True
            break
        except FileExistsError:
            with contextlib.suppress(OSError):
                if time.time() - lock_path.stat().st_mtime > _LOCK_STALE_SECONDS:
                    lock_path.unlink()
                    continue
            if time.time() >= deadline:
                break
            time.sleep(0.1)
        except OSError:
            # Unwritable global dir — proceed unlocked rather than fail.
            break
    try:
        yield acquired
    finally:
        if acquired:
            with contextlib.suppress(OSError):
                lock_path.unlink()
