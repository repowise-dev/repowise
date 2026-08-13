"""HMAC seal: refuse unsigned / forged / empty-key / cross-domain caches."""

from __future__ import annotations

import pickle

import pytest

from repowise.core.cache_seal import (
    dump_sealed_pickle,
    load_sealed_pickle,
    reset_key_cache_for_tests,
    seal,
    unseal,
)


@pytest.fixture(autouse=True)
def _isolate_hmac_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "ab" * 32)
    monkeypatch.delenv("REPOWISE_CACHE_KEY_PATH", raising=False)
    reset_key_cache_for_tests()
    yield
    reset_key_cache_for_tests()


def test_seal_roundtrip():
    payload = pickle.dumps({"version": 1, "files": {}})
    assert unseal(seal(payload, domain="parse_cache.pkl"), domain="parse_cache.pkl") == payload


def test_unseal_rejects_unsigned_pickle():
    raw = pickle.dumps({"version": 1})
    with pytest.raises(ValueError, match="unsigned"):
        unseal(raw, domain="parse_cache.pkl")


def test_unseal_rejects_tampered_payload():
    sealed = bytearray(seal(b"hello", domain="x"))
    sealed[-1] ^= 0xFF
    with pytest.raises(ValueError, match="HMAC"):
        unseal(bytes(sealed), domain="x")


def test_unseal_rejects_wrong_key(monkeypatch):
    sealed = seal(b"hello", domain="x")
    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "cd" * 32)
    reset_key_cache_for_tests()
    with pytest.raises(ValueError, match="HMAC"):
        unseal(sealed, domain="x")


def test_unseal_rejects_wrong_domain():
    sealed = seal(b"hello", domain="parse_cache.pkl")
    with pytest.raises(ValueError, match="HMAC"):
        unseal(sealed, domain="centrality_cache.pkl")


def test_evil_reduce_never_runs_through_unseal(tmp_path):
    marker = tmp_path / "PWNED"

    class Evil:
        def __reduce__(self):
            return (marker.write_text, ("pwned",))

    blob = pickle.dumps(Evil())
    with pytest.raises(ValueError, match="unsigned"):
        unseal(blob, domain="parse_cache.pkl")
    assert not marker.exists()


def test_empty_key_file_is_replaced_not_trusted(monkeypatch, tmp_path):
    """A 0-byte key file must not become the HMAC key (Raghav #1439 review)."""
    from repowise.core import cache_seal

    key_path = tmp_path / "cache_hmac_key"
    key_path.write_bytes(b"")  # crash mid-write leftover
    monkeypatch.delenv("REPOWISE_CACHE_HMAC_KEY", raising=False)
    monkeypatch.setenv("REPOWISE_CACHE_KEY_PATH", str(key_path))
    reset_key_cache_for_tests()

    key = cache_seal._hmac_key()
    assert len(key) >= 16
    assert key_path.read_bytes() == key

    # Empty-key forgeries must not verify under the replaced key.
    forged = (
        b"RWCH1"
        + __import__("hmac")
        .new(b"", b"\x00" + b"payload", __import__("hashlib").sha256)
        .digest()
        + b"payload"
    )
    with pytest.raises(ValueError, match="HMAC"):
        unseal(forged, domain="")


def test_short_key_file_is_replaced(monkeypatch, tmp_path):
    from repowise.core import cache_seal

    key_path = tmp_path / "cache_hmac_key"
    key_path.write_bytes(b"short")
    monkeypatch.delenv("REPOWISE_CACHE_HMAC_KEY", raising=False)
    monkeypatch.setenv("REPOWISE_CACHE_KEY_PATH", str(key_path))
    reset_key_cache_for_tests()

    key = cache_seal._hmac_key()
    assert len(key) >= 16
    assert key != b"short"


def test_dump_and_load_sealed_pickle_roundtrip(tmp_path):
    path = tmp_path / "parse_cache.pkl"
    dump_sealed_pickle(path, {"version": 1, "files": {}}, domain="parse_cache.pkl")
    assert load_sealed_pickle(path, domain="parse_cache.pkl") == {"version": 1, "files": {}}


def test_load_sealed_pickle_rejects_cross_domain(tmp_path):
    path = tmp_path / "parse_cache.pkl"
    dump_sealed_pickle(path, {"ok": True}, domain="parse_cache.pkl")
    with pytest.raises(ValueError, match="HMAC"):
        load_sealed_pickle(path, domain="centrality_cache.pkl")
