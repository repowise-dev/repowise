"""Tests for the shared subprocess concurrency resolver."""

from __future__ import annotations

from repowise.core.providers.llm._concurrency import DEFAULT_CONCURRENCY, resolve_concurrency

_ENV = "REPOWISE_TEST_CONCURRENCY"


def test_default_when_unset(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    assert resolve_concurrency(_ENV, "test") == DEFAULT_CONCURRENCY == 4


def test_default_when_blank(monkeypatch):
    monkeypatch.setenv(_ENV, "  ")
    assert resolve_concurrency(_ENV, "test") == DEFAULT_CONCURRENCY


def test_override_can_raise_and_lower(monkeypatch):
    monkeypatch.setenv(_ENV, "8")
    assert resolve_concurrency(_ENV, "test") == 8
    monkeypatch.setenv(_ENV, " 2 ")
    assert resolve_concurrency(_ENV, "test") == 2


def test_invalid_value_falls_back_to_default(monkeypatch):
    monkeypatch.setenv(_ENV, "not-a-number")
    assert resolve_concurrency(_ENV, "test") == DEFAULT_CONCURRENCY


def test_zero_and_negative_clamp_to_one(monkeypatch):
    monkeypatch.setenv(_ENV, "0")
    assert resolve_concurrency(_ENV, "test") == 1
    monkeypatch.setenv(_ENV, "-3")
    assert resolve_concurrency(_ENV, "test") == 1
