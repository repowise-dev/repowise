"""HMAC seal for on-disk caches that live inside the indexed repo.

Caches under ``.repowise/`` are attacker-writable: a published repo can
``git add -f`` them despite the directory being gitignored. Loading those
files with bare ``pickle.load`` is CWE-502 (deserialization of untrusted
data) — the version / fingerprint checks run *after* unpickling, so a
crafted ``__reduce__`` payload already ran.

This module seals pickled bytes with a machine-local HMAC key that is
never stored next to the cache. The MAC also binds a *domain* string
(typically the cache filename) so a sealed file cannot be renamed or
copied across cache kinds / repos on the same machine. Callers must
verify before unpickling. Unsigned legacy files and forged payloads raise
and degrade to a cache miss / full recompute.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import os
import pickle
import secrets
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any

__all__ = [
    "dump_sealed_pickle",
    "load_sealed_pickle",
    "reset_key_cache_for_tests",
    "seal",
    "unseal",
]

#: Format tag. Bump the trailing digit only when the envelope layout changes.
_MAGIC = b"RWCH1"
_MAC_LEN = 32  # sha256 digest
_MIN_KEY_LEN = 16
_STREAM_CHUNK = 1024 * 1024


def _key_path() -> Path:
    override = os.environ.get("REPOWISE_CACHE_KEY_PATH")
    if override:
        return Path(override)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "repowise" / "cache_hmac_key"


def _read_valid_key(path: Path) -> bytes | None:
    """Return the key file contents iff they are long enough, else ``None``.

    Empty or truncated files (e.g. a crash mid-write under a prior non-atomic
    path) must not be trusted — an empty HMAC key is a publicly known key.
    """
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError:
        return None
    if len(data) < _MIN_KEY_LEN:
        return None
    return data


def _write_key_atomic(path: Path, key: bytes) -> None:
    """Persist *key* via temp file + ``os.replace`` so a crash cannot leave 0 bytes.

    Mode ``0o600`` is best-effort: respected on POSIX, effectively ignored on
    Windows (ACLs are separate). The HMAC key is still not stored next to the
    cache either way.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".cache_hmac_key.", suffix=".tmp"
    )
    try:
        os.write(fd, key)
        os.close(fd)
        fd = -1
        with contextlib.suppress(OSError):
            os.chmod(tmp_name, 0o600)
        os.replace(tmp_name, path)
    except BaseException:
        if fd >= 0:
            with contextlib.suppress(OSError):
                os.close(fd)
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


@lru_cache(maxsize=1)
def _hmac_key() -> bytes:
    """Return the machine-local HMAC key, creating it on first use.

    Precedence:
    1. ``REPOWISE_CACHE_HMAC_KEY`` — hex-encoded key (tests / CI), min 16 bytes.
    2. File at ``REPOWISE_CACHE_KEY_PATH`` or ``$XDG_CONFIG_HOME/repowise/cache_hmac_key``.

    Short / empty key files are ignored and replaced; the in-memory key is used
    if the config dir is unwritable.
    """
    env_key = os.environ.get("REPOWISE_CACHE_HMAC_KEY")
    if env_key:
        try:
            raw = bytes.fromhex(env_key.strip())
        except ValueError as exc:
            raise ValueError("REPOWISE_CACHE_HMAC_KEY must be a hex-encoded key") from exc
        if len(raw) < _MIN_KEY_LEN:
            raise ValueError("REPOWISE_CACHE_HMAC_KEY must be at least 16 bytes")
        return raw

    path = _key_path()
    existing = _read_valid_key(path)
    if existing is not None:
        return existing

    key = secrets.token_bytes(32)
    try:
        # Atomic replace so a crash cannot leave a 0-byte key file. If the
        # file is missing or truncated we (re)write; a race with another
        # first-time writer may overwrite once, then we re-read whatever
        # landed so we never return b"".
        _write_key_atomic(path, key)
    except OSError:
        # Unwritable home (ephemeral CI, read-only container): keep the
        # in-memory key for this process. Caches won't survive a restart,
        # which is the safe degradation.
        return key
    existing = _read_valid_key(path)
    return existing if existing is not None else key


def reset_key_cache_for_tests() -> None:
    """Drop the cached key so tests can swap env / key path mid-run."""
    _hmac_key.cache_clear()


def _domain_prefix(domain: str) -> bytes:
    return domain.encode("utf-8") + b"\x00"


def seal(payload: bytes, *, domain: str = "") -> bytes:
    """Return ``MAGIC || HMAC-SHA256(key, domain||NUL||payload) || payload``."""
    mac = hmac.new(_hmac_key(), _domain_prefix(domain) + payload, hashlib.sha256).digest()
    return _MAGIC + mac + payload


def unseal(blob: bytes, *, domain: str = "") -> bytes:
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
    expected = hmac.new(
        _hmac_key(), _domain_prefix(domain) + payload, hashlib.sha256
    ).digest()
    if not hmac.compare_digest(mac, expected):
        raise ValueError("cache HMAC mismatch")
    return payload


def load_sealed_pickle(path: Path, *, domain: str) -> Any:
    """Stream-verify a sealed pickle file, then ``pickle.load`` without an extra copy.

    Reads magic + MAC, HMAC-updates over the payload in chunks, compares, seeks
    back to the payload offset, and unpickles from the file handle — so peak
    RAM stays close to the object graph alone (no full ``read_bytes()`` twin).
    """
    header = len(_MAGIC) + _MAC_LEN
    with path.open("rb") as fh:
        magic = fh.read(len(_MAGIC))
        if magic != _MAGIC:
            raise ValueError("unsigned or unsupported cache format")
        mac = fh.read(_MAC_LEN)
        if len(mac) != _MAC_LEN:
            raise ValueError("unsigned or unsupported cache format")
        h = hmac.new(_hmac_key(), _domain_prefix(domain), hashlib.sha256)
        while True:
            chunk = fh.read(_STREAM_CHUNK)
            if not chunk:
                break
            h.update(chunk)
        if not hmac.compare_digest(mac, h.digest()):
            raise ValueError("cache HMAC mismatch")
        fh.seek(header)
        return pickle.load(fh)


def dump_sealed_pickle(path: Path, obj: Any, *, domain: str) -> None:
    """Atomically write *obj* as a domain-bound sealed pickle.

    Pickles to a temp file first, streams the HMAC over it, then writes
    ``MAGIC||MAC||payload`` via a second temp + ``os.replace`` — avoiding a
    second full in-memory copy of the pickled bytes beside the object graph.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd_pkl, tmp_pkl = tempfile.mkstemp(
        dir=str(path.parent), prefix=path.name, suffix=".pkltmp"
    )
    try:
        with os.fdopen(fd_pkl, "wb") as fh:
            fd_pkl = -1
            pickle.dump(obj, fh, protocol=pickle.HIGHEST_PROTOCOL)

        h = hmac.new(_hmac_key(), _domain_prefix(domain), hashlib.sha256)
        with open(tmp_pkl, "rb") as src:
            while True:
                chunk = src.read(_STREAM_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        mac = h.digest()

        fd_out, tmp_out = tempfile.mkstemp(
            dir=str(path.parent), prefix=path.name, suffix=".tmp"
        )
        try:
            with os.fdopen(fd_out, "wb") as out, open(tmp_pkl, "rb") as src:
                fd_out = -1
                out.write(_MAGIC)
                out.write(mac)
                while True:
                    chunk = src.read(_STREAM_CHUNK)
                    if not chunk:
                        break
                    out.write(chunk)
            os.replace(tmp_out, path)
        except BaseException:
            if fd_out >= 0:
                with contextlib.suppress(OSError):
                    os.close(fd_out)
            with contextlib.suppress(OSError):
                os.unlink(tmp_out)
            raise
    finally:
        if fd_pkl >= 0:
            with contextlib.suppress(OSError):
                os.close(fd_pkl)
        with contextlib.suppress(OSError):
            os.unlink(tmp_pkl)
