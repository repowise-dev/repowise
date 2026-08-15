"""Repository dotenv values may supply provider keys, never process controls."""

from __future__ import annotations

import hashlib
import hmac
import os
import pickle

import pytest

from repowise.cli.ui.env_persistence import load_dotenv
from repowise.core.cache_seal import load_sealed_pickle, reset_key_cache_for_tests


def _write_repo_env(tmp_path, text: str):
    env_dir = tmp_path / ".repowise"
    env_dir.mkdir()
    (env_dir / ".env").write_text(text, encoding="utf-8")


def test_load_dotenv_imports_only_provider_api_keys(tmp_path, monkeypatch):
    names = (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "OLLAMA_BASE_URL",
        "REPOWISE_CACHE_HMAC_KEY",
        "REPOWISE_DB_URL",
        "LD_PRELOAD",
        "NODE_OPTIONS",
    )
    for name in names:
        monkeypatch.delenv(name, raising=False)

    _write_repo_env(
        tmp_path,
        "\n".join(
            (
                "OPENAI_API_KEY=sk-openai",
                'export GEMINI_API_KEY="sk-gemini"',
                "OLLAMA_BASE_URL=https://attacker.invalid",
                "REPOWISE_CACHE_HMAC_KEY=" + "cd" * 32,
                "REPOWISE_DB_URL=postgresql://attacker.invalid/db",
                "LD_PRELOAD=/tmp/evil.so",
                "NODE_OPTIONS=--require=/tmp/evil.js",
            )
        ),
    )

    load_dotenv(tmp_path)

    assert os.environ["OPENAI_API_KEY"] == "sk-openai"
    assert os.environ["GEMINI_API_KEY"] == "sk-gemini"
    for name in names[2:]:
        assert name not in os.environ


def test_load_dotenv_preserves_explicit_provider_key(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "from-launcher")
    _write_repo_env(tmp_path, "OPENAI_API_KEY=from-repository\n")

    load_dotenv(tmp_path)

    assert os.environ["OPENAI_API_KEY"] == "from-launcher"


def test_repo_dotenv_cannot_authenticate_a_malicious_pickle(tmp_path, monkeypatch):
    marker = tmp_path / "PWNED"

    class Evil:
        def __reduce__(self):
            return (marker.write_text, ("pwned",))

    attacker_key = bytes.fromhex("cd" * 32)
    payload = pickle.dumps(Evil())
    domain = "parse_cache.pkl"
    mac = hmac.new(attacker_key, domain.encode() + b"\x00" + payload, hashlib.sha256).digest()
    cache_path = tmp_path / domain
    cache_path.write_bytes(b"RWCH1" + mac + payload)

    monkeypatch.delenv("REPOWISE_CACHE_HMAC_KEY", raising=False)
    monkeypatch.setenv("REPOWISE_CACHE_KEY_PATH", str(tmp_path / "trusted" / "cache-key"))
    _write_repo_env(tmp_path, "REPOWISE_CACHE_HMAC_KEY=" + attacker_key.hex() + "\n")
    reset_key_cache_for_tests()
    try:
        load_dotenv(tmp_path)
        with pytest.raises(ValueError, match="HMAC"):
            load_sealed_pickle(cache_path, domain=domain)
    finally:
        reset_key_cache_for_tests()

    assert not marker.exists()
