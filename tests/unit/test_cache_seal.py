"""HMAC seal: refuse unsigned / forged on-disk caches before unpickling."""

from __future__ import annotations

import pickle

import pytest

from repowise.core.cache_seal import reset_key_cache_for_tests, seal, unseal


@pytest.fixture(autouse=True)
def _isolate_hmac_key(monkeypatch, tmp_path):
    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "ab" * 32)
    monkeypatch.delenv("REPOWISE_CACHE_KEY_PATH", raising=False)
    reset_key_cache_for_tests()
    yield
    reset_key_cache_for_tests()


def test_seal_roundtrip():
    payload = pickle.dumps({"version": 1, "files": {}})
    assert unseal(seal(payload)) == payload


def test_unseal_rejects_unsigned_pickle():
    raw = pickle.dumps({"version": 1})
    with pytest.raises(ValueError, match="unsigned"):
        unseal(raw)


def test_unseal_rejects_tampered_payload():
    sealed = bytearray(seal(b"hello"))
    sealed[-1] ^= 0xFF
    with pytest.raises(ValueError, match="HMAC"):
        unseal(bytes(sealed))


def test_unseal_rejects_wrong_key(monkeypatch):
    sealed = seal(b"hello")
    monkeypatch.setenv("REPOWISE_CACHE_HMAC_KEY", "cd" * 32)
    reset_key_cache_for_tests()
    with pytest.raises(ValueError, match="HMAC"):
        unseal(sealed)


def test_evil_reduce_never_runs_through_unseal(tmp_path):
    marker = tmp_path / "PWNED"

    class Evil:
        def __reduce__(self):
            return (marker.write_text, ("pwned",))

    # Attacker-shaped bytes: bare pickle with a reduce gadget.
    blob = pickle.dumps(Evil())
    with pytest.raises(ValueError, match="unsigned"):
        unseal(blob)
    assert not marker.exists()
