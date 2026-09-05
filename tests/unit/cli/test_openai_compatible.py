"""Focused tests for custom OpenAI-compatible gateway setup primitives."""

from __future__ import annotations

import os
from io import StringIO
from typing import Any

import pytest
from rich.console import Console

from repowise.cli.helpers import NO_SAVE_KEY_ENV
from repowise.cli.ui import openai_compatible


def _console() -> tuple[Console, StringIO]:
    output = StringIO()
    return Console(file=output, force_terminal=False, width=100), output


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("localhost:20128/v1/", "http://localhost:20128/v1"),
        ("https://gateway.example/openai/v1", "https://gateway.example/openai/v1"),
        (" HTTP://LOCALHOST:20128/v1/ ", "http://LOCALHOST:20128/v1"),
    ],
)
def test_normalize_base_url_accepts_supported_endpoints(raw: str, expected: str) -> None:
    assert openai_compatible.normalize_base_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "ftp://localhost/v1",
        "http://",
        "http://localhost:not-a-port/v1",
        "https://user:secret@example.com/v1",
        "http://x/v1?q=1",
        "http://x/v1#fragment",
    ],
)
def test_normalize_base_url_rejects_unsafe_or_invalid_values(raw: str) -> None:
    with pytest.raises(ValueError):
        openai_compatible.normalize_base_url(raw)


def test_prompt_setup_retries_invalid_endpoint_and_empty_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_API_KEY", "")
    answers = iter(["ftp://router/v1", "localhost:20128/v1", "", "router-secret"])
    monkeypatch.setattr(openai_compatible.click, "prompt", lambda *_a, **_k: next(answers))
    console, output = _console()

    base_url, api_key = openai_compatible.prompt_setup(
        console,
        official_base_url="https://api.openai.com/v1",
    )

    assert base_url == "http://localhost:20128/v1"
    assert api_key == "router-secret"
    assert os.environ["OPENAI_BASE_URL"] == base_url
    assert os.environ["OPENAI_API_KEY"] == api_key
    assert "must use http:// or https://" in output.getvalue()
    assert "API key is required" in output.getvalue()
    assert "router-secret" not in output.getvalue()


def test_prompt_setup_can_reuse_existing_key(monkeypatch: Any) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:20128/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "existing-secret")
    answers = iter(["http://localhost:20128/v1", ""])
    monkeypatch.setattr(openai_compatible.click, "prompt", lambda *_a, **_k: next(answers))
    console, _ = _console()

    _, api_key = openai_compatible.prompt_setup(
        console,
        official_base_url="https://api.openai.com/v1",
    )

    assert api_key == "existing-secret"


def test_persist_setup_saves_endpoint_and_key_with_consent(monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setenv(NO_SAVE_KEY_ENV, "")
    monkeypatch.setattr(openai_compatible.click, "confirm", lambda *_a, **_k: True)
    console, _ = _console()

    openai_compatible.persist_setup(
        console,
        tmp_path,
        base_url="http://localhost:20128/v1",
        api_key="router-secret",
        save_key=True,
    )

    env_file = tmp_path / ".repowise" / ".env"
    contents = env_file.read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=http://localhost:20128/v1" in contents
    assert "OPENAI_API_KEY=router-secret" in contents
    assert env_file.stat().st_mode & 0o777 == 0o600


def test_persist_setup_no_save_key_keeps_secret_out_of_repo(
    monkeypatch: Any, tmp_path: Any
) -> None:
    monkeypatch.setenv(NO_SAVE_KEY_ENV, "")
    console, output = _console()

    openai_compatible.persist_setup(
        console,
        tmp_path,
        base_url="http://localhost:20128/v1",
        api_key="router-secret",
        save_key=False,
    )

    contents = (tmp_path / ".repowise" / ".env").read_text(encoding="utf-8")
    assert "OPENAI_BASE_URL=http://localhost:20128/v1" in contents
    assert "router-secret" not in contents
    assert os.environ[NO_SAVE_KEY_ENV] == "1"
    assert "router-secret" not in output.getvalue()
