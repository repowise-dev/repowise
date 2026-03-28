"""Tests for generation/models.py — 10 tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from repowise.core.generation.models import (
    ConfidenceDecayResult,
    GeneratedPage,
    GenerationConfig,
    compute_freshness,
    compute_page_id,
    decay_confidence,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_page(
    updated_at: datetime,
    source_hash: str = "deadbeef" * 8,
    confidence: float = 1.0,
) -> GeneratedPage:
    now_iso = updated_at.isoformat()
    return GeneratedPage(
        page_id="file_page:python_pkg/calculator.py",
        page_type="file_page",
        title="File: python_pkg/calculator.py",
        content="## Overview\nThis is a calculator.",
        source_hash=source_hash,
        model_name="mock-model-1",
        provider_name="mock",
        input_tokens=100,
        output_tokens=50,
        cached_tokens=0,
        generation_level=2,
        target_path="python_pkg/calculator.py",
        created_at=now_iso,
        updated_at=now_iso,
        confidence=confidence,
    )


def _utc(**kwargs) -> datetime:
    return datetime.now(UTC) - timedelta(**kwargs)


# ---------------------------------------------------------------------------
# compute_page_id
# ---------------------------------------------------------------------------


def test_compute_page_id_normal():
    pid = compute_page_id("file_page", "python_pkg/calculator.py")
    assert pid == "file_page:python_pkg/calculator.py"


def test_compute_page_id_scc():
    pid = compute_page_id("scc_page", "scc-0")
    assert pid == "scc_page:scc-0"


# ---------------------------------------------------------------------------
# GeneratedPage.total_tokens
# ---------------------------------------------------------------------------


def test_total_tokens_property():
    page = _make_page(_utc(days=0))
    page.input_tokens = 200
    page.output_tokens = 80
    assert page.total_tokens == 280


# ---------------------------------------------------------------------------
# compute_freshness
# ---------------------------------------------------------------------------


def test_freshness_same_hash_is_fresh():
    config = GenerationConfig()
    page = _make_page(_utc(days=0), source_hash="abc")
    status = compute_freshness(page, "abc", config)
    assert status == "fresh"


def test_freshness_different_hash_is_stale():
    config = GenerationConfig()
    page = _make_page(_utc(days=0), source_hash="abc")
    status = compute_freshness(page, "xyz", config)
    assert status == "stale"


def test_freshness_expired_by_age():
    config = GenerationConfig(expiry_threshold_days=30)
    page = _make_page(_utc(days=31), source_hash="abc")
    status = compute_freshness(page, "abc", config)
    assert status == "expired"


def test_freshness_stale_by_age_same_hash():
    config = GenerationConfig(staleness_threshold_days=7, expiry_threshold_days=30)
    page = _make_page(_utc(days=8), source_hash="abc")
    status = compute_freshness(page, "abc", config)
    assert status == "stale"


# ---------------------------------------------------------------------------
# decay_confidence
# ---------------------------------------------------------------------------


def test_decay_confidence_zero_days():
    config = GenerationConfig(expiry_threshold_days=30)
    page = _make_page(_utc(seconds=1), confidence=1.0)
    result = decay_confidence(page, config)
    assert isinstance(result, ConfidenceDecayResult)
    assert result.new_confidence > 0.99


def test_decay_confidence_halfway():
    config = GenerationConfig(expiry_threshold_days=30)
    page = _make_page(_utc(days=15), confidence=1.0)
    result = decay_confidence(page, config)
    assert 0.4 < result.new_confidence < 0.6


def test_decay_confidence_beyond_expiry_is_zero():
    config = GenerationConfig(expiry_threshold_days=30)
    page = _make_page(_utc(days=60), confidence=1.0)
    result = decay_confidence(page, config)
    assert result.new_confidence == 0.0
    assert result.freshness_status == "expired"


# ---------------------------------------------------------------------------
# GenerationConfig defaults
# ---------------------------------------------------------------------------


def test_generation_config_defaults():
    config = GenerationConfig()
    assert config.max_tokens == 16000
    assert config.temperature == 0.3
    assert config.token_budget == 48000
    assert config.max_concurrency == 5
    assert config.cache_enabled is True
    assert config.staleness_threshold_days == 7
    assert config.expiry_threshold_days == 30
    assert config.top_symbol_percentile == 0.10
    assert config.large_file_source_pct == 0.4
