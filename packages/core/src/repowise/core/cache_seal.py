"""HMAC seal for on-disk caches that live inside the indexed repo.

Caches under ``.repowise/`` are attacker-writable: a published repo can
``git add -f`` them despite the directory being gitignored. Loading those
files with bare ``pickle.load`` is CWE-502 (deserialization of untrusted
data) — the version / fingerprint checks run *after* unpickling, so a
crafted ``__reduce__`` payload already ran.

This module seals pickled bytes with a machine-local HMAC key that is
never stored next to the cache. Callers must ``unseal`` before
``pickle.loads``. Unsigned legacy files and forged payloads raise and
degrade to a cache miss / full recompute.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from functools import lru_cache
from pathlib import Path

__all__ = ["reset_key_cache_for_tests", "seal", "unseal"]

#: Format tag. Bump the trailing digit only when the envelope layout changes.
_MAGIC = b"RWCH1"
_MAC_LEN = 32  # sha256 digest


def _key_path() -> Path:
    override = os.environ.get("REPOWISE_CACHE_KEY_PATH")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "repowise" / "cache_hmac_key"


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    """Return the machine-local HMAC key, creating it on first use.

    Precedence:
    1. ``REPOWISE_CACHE_HMAC_KEY`` — hex-encoded 32-byte key (tests / CI).
    2. File at ``REPOWISE_CACHE_KEY_PATH`` or ``$XDG_CONFIG_HOME/repowise/cache_hmac_key``.
    """
    env_key = os.environ.get("REPOWISE_CACHE_HMAC_KEY")
    if env_key:
        try:
            raw = bytes.fromhex(env_key.strip())
        except ValueError as exc:
            raise ValueError(
                "REPOWISE_CACHE_HMAC_KEY must be a hex-encoded key"
            ) from exc
        if len(raw) < 16:
            raise ValueError("REPOWISE_CACHE_HMAC_KEY must be at least 16 bytes")
        return raw

    path = _key_path()
    try:
        data = path.read_bytes()
        if len(data) >= 16:
            return data
    except FileNotFoundError:
        pass
    except OSError:
        pass

    key = secrets.token_bytes(32)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 0o600 so other users on a shared machine cannot forge seals.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, key)
        finally:
            os.close(fd)
    except FileExistsError:
        # Another process created it first — re-read.
        return path.read_bytes()
    except OSError:
        # Unwritable home (ephemeral CI, read-only container): keep the
        # in-memory key for this process. Caches won't survive a restart,
        # which is the safe degradation.
        pass
    return key


def reset_key_cache_for_tests() -> None:
    """Drop the cached key so tests can swap env / key path mid-run."""
    _hmac_key.cache_clear()


def seal(payload: bytes) -> bytes:
    """Return ``MAGIC || HMAC-SHA256(key, payload) || payload``."""
    mac = hmac.new(_hmac_key(), payload, hashlib.sha256).digest()
    return _MAGIC + mac + payload


def unseal(blob: bytes) -> bytes:
    """Verify and return the sealed payload, or raise ``ValueError``.

    Raises on missing/unknown magic, truncated envelopes, or HMAC mismatch.
    Never returns bytes that have not been authenticated — callers may
    ``pickle.loads`` the result.
    """
    min_len = len(_MAGIC) + _MAC_LEN
    if len(blob) < min_len or not blob.startswith(_MAGIC):
        raise ValueError("unsigned or unsupported cache format")
    mac = blob[len(_MAGIC) : len(_MAGIC) + _MAC_LEN]
    payload = blob[len(_MAGIC) + _MAC_LEN :]
    expected = hmac.new(_hmac_key(), payload, hashlib.sha256).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("cache HMAC mismatch")
    return payload
