"""HMAC seal: refuse unsigned / forged / empty-key / cross-domain caches."""

from __future__ import annotations

import os
import pickle

import pytest

import repowise.core.cache_seal as cache_seal
from repowise.core.cache_seal import (
    dump_sealed_pickle,
    load_sealed_pickle,
    reset_key_cache_for_tests,
    seal,
    sweep_orphan_temps,
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


def _aged(path, *, old):
    path.write_bytes(b"x")
    if old:
        os.utime(path, (1, 1))
    return path


def test_sweep_removes_old_orphans_and_keeps_fresh_and_unrelated(tmp_path):
    index = tmp_path / ".repowise"
    (index / "mcp_cache").mkdir(parents=True)
    old = [
        _aged(index / "centrality_cache.pkl9c5db8b4228022b4.pkltmp", old=True),
        _aged(index / "duplication_cache.pkl7917cfu5.pkltmp", old=True),
        _aged(index / "parse_cache.pkl0123456789abcdef.tmp", old=True),
        _aged(index / "mcp_cache" / "repo1.adjacencydeadbeef.pkltmp", old=True),
    ]
    kept = [
        _aged(index / "parse_cache.pklfeedfacefeedface.pkltmp", old=False),
        _aged(index / "parse_cache.pkl", old=True),
        _aged(index / "notes.tmp", old=True),
        _aged(index / "state.json.tmp", old=True),
        _aged(index / "manual.pkltmp", old=True),
        _aged(index / "notes.pkl.backup.tmp", old=True),
    ]
    deep = index / "lancedb" / "pages.lance"
    deep.mkdir(parents=True)
    kept.append(_aged(deep / "x.pkltmp", old=True))

    assert sweep_orphan_temps(index) == len(old)

    assert not any(p.exists() for p in old)
    assert all(p.exists() for p in kept)


def test_sweep_never_follows_a_link_out_of_the_directory(tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    victim = _aged(outside / "parse_cache.pkl0123456789abcdef.pkltmp", old=True)
    index = tmp_path / ".repowise"
    index.mkdir()
    link = index / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            pytest.skip("cannot create a directory link here")
        import _winapi

        _winapi.CreateJunction(str(outside), str(link))

    assert sweep_orphan_temps(index) == 0
    assert victim.exists()


def test_sweep_skips_a_file_it_cannot_delete(tmp_path, monkeypatch):
    locked = _aged(tmp_path / "a.pkl1111111111111111.pkltmp", old=True)
    other = _aged(tmp_path / "b.pkl2222222222222222.pkltmp", old=True)
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        if os.fspath(path) == str(locked):
            raise PermissionError("in use by another process")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(cache_seal.os, "unlink", unlink)

    assert sweep_orphan_temps(tmp_path) == 1
    assert locked.exists() and not other.exists()


def test_sweep_of_a_missing_directory_is_a_no_op(tmp_path):
    assert sweep_orphan_temps(tmp_path / "absent") == 0


def test_every_temp_a_sealed_write_makes_is_one_the_sweep_matches(tmp_path, monkeypatch):
    names = []
    real = cache_seal.make_temp_file

    def recording(directory, **kwargs):
        fd, name = real(directory, **kwargs)
        names.append(os.path.basename(name))
        return fd, name

    monkeypatch.setattr(cache_seal, "make_temp_file", recording)
    dump_sealed_pickle(tmp_path / "repo1.adjacency", {"a": 1}, domain="d")

    assert len(names) == 2
    assert all(cache_seal._is_orphan_temp_name(n) for n in names)
