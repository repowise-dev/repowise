"""Tests for generation/models.py — 10 tests."""

from __future__ import annotations

import copy
import pickle
from datetime import UTC, datetime, timedelta

import pytest

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
    assert config.max_tokens == 16384
    assert config.temperature == 0.3
    assert config.token_budget == 48000
    assert config.source_evidence_token_budget == 8000
    assert config.source_evidence_files == {}
    assert config.max_concurrency == 12
    assert config.embed_concurrency == 12
    assert config.cache_enabled is True
    assert config.staleness_threshold_days == 7
    assert config.expiry_threshold_days == 30
    assert config.top_symbol_percentile == 0.10
    assert config.reasoning == "auto"


def test_generation_config_preserves_existing_positional_constructor_order():
    config = GenerationConfig(4096, 0.2, 12000, 4)

    assert config.max_concurrency == 4
    assert config.source_evidence_token_budget == 8000


def test_generation_config_remains_hashable_and_evidence_mapping_is_immutable():
    config = GenerationConfig.from_repo_config(
        {"generation_context": {"files": {"repo_overview": ["README.md"]}}}
    )

    assert isinstance(hash(config), int)
    assert "repo_overview" in config.source_evidence_files
    assert ("repo_overview", ("README.md",)) not in config.source_evidence_files
    assert config.source_evidence_files != tuple(config.source_evidence_files.items())
    assert tuple(config.source_evidence_files.items()) != config.source_evidence_files
    with pytest.raises(TypeError):
        config.source_evidence_files["repo_overview"] = ("other.md",)  # type: ignore[index]
    with pytest.raises(TypeError):
        dict.__setitem__(config.source_evidence_files, "repo_overview", ("other.md",))  # type: ignore[arg-type]
    with pytest.raises((AttributeError, TypeError)):
        config.source_evidence_files._items = ()  # type: ignore[attr-defined]
    # The backing store itself is read-only, so a reachable private handle
    # cannot mutate it in place and corrupt the cached hash.
    with pytest.raises(TypeError):
        config.source_evidence_files._items["repo_overview"] = ("other.md",)  # type: ignore[index]

    reordered = GenerationConfig(
        source_evidence_files={
            "onboarding/how_it_works": ("docs/flow.md",),
            "repo_overview": ("README.md",),
        }
    )
    original_order = GenerationConfig(
        source_evidence_files={
            "repo_overview": ("README.md",),
            "onboarding/how_it_works": ("docs/flow.md",),
        }
    )
    assert reordered == original_order
    assert hash(reordered) == hash(original_order)


def test_generation_config_to_dict_preserves_public_evidence_mapping_shape():
    config = GenerationConfig(
        source_evidence_files={"repo_overview": ("README.md",)},
    )

    snapshot = config.to_dict()

    assert type(snapshot["source_evidence_files"]) is dict
    assert snapshot["source_evidence_files"] == {"repo_overview": ("README.md",)}
    restored = GenerationConfig(**snapshot)
    assert restored.source_evidence_files == config.source_evidence_files


def test_generation_config_remains_copyable_with_immutable_evidence_mapping():
    config = GenerationConfig(
        source_evidence_files={"repo_overview": ("README.md",)},
    )

    assert copy.copy(config) == config
    assert copy.deepcopy(config) == config
    assert pickle.loads(pickle.dumps(config)) == config


def test_generation_config_reads_repo_max_tokens():
    config = GenerationConfig.from_repo_config({"max_tokens": "2345"})
    assert config.max_tokens == 2345


def test_generation_config_reads_source_evidence_settings():
    config = GenerationConfig.from_repo_config(
        {
            "generation_context": {
                "token_budget": 4321,
                "files": {
                    "repo_overview": ["README.md", "docs/architecture.md"],
                    "onboarding/how_it_works": ["docs/flow.md"],
                },
            }
        }
    )

    assert config.source_evidence_token_budget == 4321
    assert config.source_evidence_files == {
        "repo_overview": ("README.md", "docs/architecture.md"),
        "onboarding/how_it_works": ("docs/flow.md",),
    }


@pytest.mark.parametrize(
    "generation_context",
    [
        [],
        {"token_budget": -1},
        {"token_budget": True},
        {"files": []},
        {"files": {"module_page": ["README.md"]}},
        {"files": {"onboarding/project_overview": ["README.md"]}},
        {"files": {"repo_overview": "README.md"}},
    ],
)
def test_generation_config_rejects_invalid_source_evidence_settings(generation_context):
    with pytest.raises(ValueError, match="generation_context"):
        GenerationConfig.from_repo_config({"generation_context": generation_context})


def test_direct_generation_config_rejects_an_unconsumed_evidence_key() -> None:
    with pytest.raises(ValueError, match="project_overview is configured as repo_overview"):
        GenerationConfig(
            source_evidence_files={
                "onboarding/project_overview": ("README.md",),
            }
        )


def test_direct_generation_config_frames_evidence_errors_with_the_field_name() -> None:
    # Direct construction reports against the internal field, not the config
    # key from_repo_config uses -- the two framings are deliberate, so pin both.
    with pytest.raises(ValueError, match=r"^source_evidence_files keys must name"):
        GenerationConfig(source_evidence_files={"module_page": ("README.md",)})
    with pytest.raises(ValueError, match=r"^source_evidence_files\.repo_overview must be a list"):
        GenerationConfig(source_evidence_files={"repo_overview": "README.md"})


class TestRetiredEvidenceKeysDoNotBreakAnUpgrade:
    """A key naming a retired page is dropped, not raised on.

    The strictness elsewhere in this validator is aimed at a typo, which is
    only ever a mistake. A retired key is a config that was correct when it was
    written, and every user carrying one would hit a hard generation failure on
    their first update after the release that retired the page.
    """

    def test_a_retired_key_is_ignored_rather_than_rejected(self) -> None:
        from repowise.core.generation.page_redirects import RETIRED_IDS

        retired_keys = [
            page_id.split(":", 1)[1]
            for page_id in sorted(RETIRED_IDS)
            if page_id.startswith("onboarding:")
        ]
        assert retired_keys, "no retired onboarding keys to exercise"

        for key in retired_keys:
            config = GenerationConfig.from_repo_config(
                {"generation_context": {"files": {key: ["docs/x.md"]}}}
            )
            assert key not in config.source_evidence_files

    def test_a_retired_key_alongside_a_live_one_keeps_the_live_one(self) -> None:
        config = GenerationConfig.from_repo_config(
            {
                "generation_context": {
                    "files": {
                        "onboarding/codebase_map": ["docs/old.md"],
                        "repo_overview": ["README.md"],
                    }
                }
            }
        )

        assert config.source_evidence_files == {"repo_overview": ("README.md",)}

    def test_a_genuine_typo_still_raises(self) -> None:
        """The leniency is scoped to the retirement table, not to unknown keys."""
        with pytest.raises(ValueError, match="must name repo_overview"):
            GenerationConfig.from_repo_config(
                {"generation_context": {"files": {"onboarding/codebase_maps": ["docs/x.md"]}}}
            )


def test_direct_generation_config_normalizes_evidence_keys_like_the_config_loader() -> None:
    # Sharing one validator also aligns key normalization: direct construction
    # now strips keys before the membership check, as from_repo_config always
    # did. A padded key is accepted and stored trimmed, not rejected.
    config = GenerationConfig(source_evidence_files={" repo_overview ": ("README.md",)})

    assert config.source_evidence_files == {"repo_overview": ("README.md",)}


@pytest.mark.parametrize("value", [0, -1, True, 1.5, "not-a-number"])
def test_generation_config_rejects_invalid_repo_max_tokens(value):
    with pytest.raises(ValueError, match="positive integer"):
        GenerationConfig.from_repo_config({"max_tokens": value})


def test_generation_config_embed_concurrency_defaults_to_max_concurrency():
    config = GenerationConfig(max_concurrency=3)
    assert config.embed_concurrency == 3


def test_generation_config_normalizes_reasoning():
    config = GenerationConfig(reasoning="OFF")
    assert config.reasoning == "off"


def test_generation_config_accepts_native_reasoning_effort():
    config = GenerationConfig(reasoning="XHIGH")
    assert config.reasoning == "xhigh"


def test_generation_config_rejects_invalid_reasoning():
    with pytest.raises(ValueError):
        GenerationConfig(reasoning="verbose")
