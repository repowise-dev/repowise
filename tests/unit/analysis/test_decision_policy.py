"""Resolution, presets, and persistence of the decision capture policy."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from repowise.core.analysis.decisions.policy import (
    CAPTURE_SOURCE_KEYS,
    PRESET_NAMES,
    SOURCE_SPECS,
    DiscoveryBudget,
    preset_policy,
    resolve_policy,
)
from repowise.core.analysis.decisions.policy_store import (
    PolicyConflictError,
    load_policy,
    policy_etag,
    write_policy,
)
from repowise.core.analysis.decisions.provenance import RETIRED_SOURCES
from repowise.core.repo_config import load_repo_config


def _write_config(repo: Path, text: str) -> None:
    cfg_dir = repo / ".repowise"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.yaml").write_text(text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("config", [None, {}, {"decisions": {}}, {"decisions": None}])
def test_absent_config_resolves_to_prior_behaviour(config):
    """A repo indexed before the policy existed must not change on upgrade."""
    policy = resolve_policy(config).policy

    assert policy.enabled is True
    assert policy.llm is True
    legacy = [key for key in CAPTURE_SOURCE_KEYS if key != "session_discovery"]
    assert all(policy.source_enabled(key) for key in legacy)
    assert all(policy.llm_allowed(key) for key in legacy)
    # A source added after this config shape existed is off until asked for:
    # an upgrade must not start a model call nobody enabled.
    assert policy.source_enabled("session_discovery") is False
    assert policy.preset_name() == "default"


def test_legacy_session_mining_false_disables_the_session_source():
    policy = resolve_policy({"decisions": {"session_mining": False}}).policy

    assert policy.source_enabled("session") is False
    assert policy.source_enabled("adr") is True


def test_legacy_session_mining_is_reported_not_swallowed():
    resolution = resolve_policy({"decisions": {"session_mining": True}})

    assert resolution.legacy_keys == ("session_mining",)


def test_legacy_session_mining_off_cannot_be_widened_by_a_source_key():
    """The kill switch wins. Widening past it would start reading transcripts
    on a config that had switched them off."""
    policy = resolve_policy(
        {"decisions": {"session_mining": False, "sources": {"session": True}}}
    ).policy

    assert policy.source_enabled("session") is False


def test_a_source_key_can_still_narrow_past_legacy_session_mining():
    policy = resolve_policy(
        {"decisions": {"session_mining": True, "sources": {"session": False}}}
    ).policy

    assert policy.source_enabled("session") is False


def test_legacy_bool_sources_resolve_unchanged():
    policy = resolve_policy({"decisions": {"sources": {"comment": False, "pr": False}}}).policy

    assert policy.source_enabled("comment") is False
    assert policy.source_enabled("pr") is False
    assert policy.source_enabled("adr") is True


@pytest.mark.parametrize("retired", RETIRED_SOURCES)
def test_retired_source_keys_say_retired_not_unknown(retired):
    """These were documented switches. "Unknown" reads as the user's typo."""
    resolution = resolve_policy({"decisions": {"sources": {retired: False}}})

    assert any(retired in w and "retired" in w for w in resolution.warnings)
    assert not any("Unknown decision source" in w for w in resolution.warnings)
    assert all(
        resolution.policy.source_enabled(key)
        for key in CAPTURE_SOURCE_KEYS
        if key != "session_discovery"
    )


@pytest.mark.parametrize(
    "config",
    [
        {"decisions": "nope"},
        {"decisions": {"sources": "nope"}},
        {"decisions": {"enabled": "yes"}},
        {"decisions": {"llm": 1}},
        {"decisions": {"sources": {"adr": "yes"}}},
        {"decisions": {"preset": "aggressive"}},
        {"decisions": {"nonsense": True}},
    ],
)
def test_malformed_config_warns_and_falls_back(config):
    """Never silently discarded: a typo that reads as a working switch is worse
    than a loud one."""
    resolution = resolve_policy(config)

    assert resolution.warnings
    assert resolution.policy.source_enabled("adr") is True


def test_unknown_per_source_key_warns():
    resolution = resolve_policy({"decisions": {"sources": {"adr": {"enabled": True, "wat": 1}}}})

    assert any("wat" in w for w in resolution.warnings)


def test_global_llm_off_stops_every_model_stage():
    policy = resolve_policy({"decisions": {"llm": False}}).policy

    assert policy.any_llm_allowed() is False
    assert all(not policy.llm_allowed(key) for key in CAPTURE_SOURCE_KEYS)
    # Deterministic capture survives.
    assert policy.source_enabled("adr") is True
    assert policy.enabled_index_sources() == ("inline_marker", "adr")


def test_per_source_llm_off_leaves_other_sources_alone():
    policy = resolve_policy({"decisions": {"sources": {"adr": {"enabled": True, "llm": False}}}}).policy

    assert policy.llm_allowed("adr") is False
    assert policy.llm_allowed("pr") is True
    assert policy.source_enabled("adr") is True


def test_global_off_disables_capture_but_not_manual_entry():
    policy = resolve_policy({"decisions": {"enabled": False}}).policy

    assert all(not policy.source_enabled(key) for key in CAPTURE_SOURCE_KEYS)
    assert policy.source_enabled("cli") is True
    assert policy.enabled_index_sources() == ()


def test_llm_only_source_is_dropped_from_the_index_run_when_the_model_is_off():
    """It would run, call nothing and return zero, which reads as an empty repo."""
    policy = resolve_policy({"decisions": {"llm": False}}).policy

    assert "pr" not in policy.enabled_index_sources()
    assert "git_archaeology" not in policy.enabled_index_sources()


def test_unknown_source_is_never_enabled():
    policy = resolve_policy(None).policy

    assert policy.source_enabled("code_comment") is False
    assert policy.llm_allowed("code_comment") is False


# ---------------------------------------------------------------------------
# Presets and runtime status
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", PRESET_NAMES)
def test_every_preset_round_trips_to_its_own_name(name):
    assert preset_policy(name).preset_name() == name


def test_unknown_preset_raises():
    with pytest.raises(ValueError, match="Unknown preset"):
        preset_policy("aggressive")


def test_off_preset_keeps_manual_entry_and_stops_everything_else():
    policy = preset_policy("off")

    assert policy.source_enabled("cli") is True
    assert policy.any_llm_allowed() is False
    assert policy.enabled_index_sources() == ()


def test_local_only_preset_makes_no_model_calls():
    policy = preset_policy("local_only")

    assert policy.any_llm_allowed() is False
    assert policy.source_enabled("session") is True
    assert policy.source_enabled("inline_marker") is True


def test_custom_policy_reports_custom():
    policy = preset_policy("full").with_source("adr", enabled=False)

    assert policy.preset_name() == "custom"


def test_runtime_names_a_reason_for_every_source():
    runtime = preset_policy("full").runtime(provider_available=False)

    assert {rt.key for rt in runtime} == {spec.key for spec in SOURCE_SPECS}
    assert all(rt.reason for rt in runtime)


def test_missing_provider_degrades_rather_than_fails():
    by_key = {
        rt.key: rt for rt in preset_policy("full").runtime(provider_available=False)
    }

    # An LLM-only source is skipped, not failed.
    assert by_key["pr"].status == "skipped_no_provider"
    # A hybrid source keeps its deterministic stage.
    assert by_key["adr"].status == "deterministic_only"
    assert by_key["cli"].status == "always_on"


def test_llm_off_status_says_skipped_not_failed():
    by_key = {
        rt.key: rt
        for rt in preset_policy("full").with_llm(False).runtime(provider_available=True)
    }

    assert "off" in by_key["pr"].reason.lower()
    assert by_key["adr"].status == "deterministic_only"


def test_authority_routes_cannot_be_switched_off():
    with pytest.raises(ValueError, match="authority route"):
        preset_policy("full").with_source("cli", enabled=False)


def test_with_source_rejects_unknown_keys():
    with pytest.raises(ValueError, match="Unknown decision source"):
        preset_policy("full").with_source("code_comment", enabled=False)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


def test_write_preserves_unrelated_config_keys(tmp_path):
    _write_config(tmp_path, "provider: anthropic\nmodel: claude-x\ncoverage:\n  path: cov.xml\n")

    write_policy(tmp_path, preset_policy("local_only"))

    cfg = load_repo_config(tmp_path)
    assert cfg["provider"] == "anthropic"
    assert cfg["coverage"] == {"path": "cov.xml"}
    assert cfg["decisions"]["llm"] is False


def test_write_round_trips_through_resolution(tmp_path):
    policy = preset_policy("full").with_source("comment", enabled=False, llm=False)

    write_policy(tmp_path, policy)

    assert load_policy(tmp_path).policy == policy


def test_write_replaces_the_legacy_session_mining_key(tmp_path):
    _write_config(tmp_path, "decisions:\n  session_mining: false\n")

    write_policy(tmp_path, load_policy(tmp_path).policy)

    raw = yaml.safe_load((tmp_path / ".repowise" / "config.yaml").read_text(encoding="utf-8"))
    assert "session_mining" not in raw["decisions"]
    assert raw["decisions"]["sources"]["session"] is False


def test_stale_etag_conflicts_and_writes_nothing(tmp_path):
    write_policy(tmp_path, preset_policy("full"))
    stale = policy_etag(preset_policy("local_only"))
    before = (tmp_path / ".repowise" / "config.yaml").read_bytes()

    with pytest.raises(PolicyConflictError):
        write_policy(tmp_path, preset_policy("off"), expected_etag=stale)

    assert (tmp_path / ".repowise" / "config.yaml").read_bytes() == before


def test_matching_etag_writes(tmp_path):
    resolution = write_policy(tmp_path, preset_policy("full"))

    write_policy(tmp_path, preset_policy("off"), expected_etag=policy_etag(resolution.policy))

    assert load_policy(tmp_path).policy.enabled is False


def test_etag_changes_with_the_policy():
    assert policy_etag(preset_policy("full")) != policy_etag(preset_policy("off"))
    assert policy_etag(preset_policy("full")) == policy_etag(preset_policy("full"))


def test_a_write_that_dies_before_serializing_leaves_the_config_untouched(tmp_path, monkeypatch):
    _write_config(tmp_path, "provider: anthropic\n")
    original = (tmp_path / ".repowise" / "config.yaml").read_bytes()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("serializer died")

    monkeypatch.setattr("yaml.dump", _boom)
    with pytest.raises(RuntimeError):
        write_policy(tmp_path, preset_policy("off"))

    assert (tmp_path / ".repowise" / "config.yaml").read_bytes() == original
    assert not list((tmp_path / ".repowise").glob(".config.*.tmp"))


def test_a_write_that_dies_mid_flight_leaves_the_config_untouched(tmp_path, monkeypatch):
    """The case the temp file exists for: the failure lands after mkstemp.

    Patching yaml.dump alone never reaches that code, because serialization
    happens before the temp file is opened.
    """
    import os

    _write_config(tmp_path, "provider: anthropic\n")
    original = (tmp_path / ".repowise" / "config.yaml").read_bytes()

    def _boom(*_args, **_kwargs):
        raise OSError("disk went away")

    monkeypatch.setattr(os, "replace", _boom)
    with pytest.raises(OSError):
        write_policy(tmp_path, preset_policy("off"))

    assert (tmp_path / ".repowise" / "config.yaml").read_bytes() == original
    assert not list((tmp_path / ".repowise").glob(".config.*.tmp"))


def test_a_broken_config_file_raises_rather_than_resolving_to_defaults(tmp_path):
    """A malformed decisions block is a warning; a malformed file is an error."""
    from repowise.core.repo_config import RepoConfigError

    _write_config(tmp_path, "decisions:\n\tsources: {}\n")

    with pytest.raises(RepoConfigError):
        load_policy(tmp_path)


# ---------------------------------------------------------------------------
# Broad session discovery: opt-in, and its budget
# ---------------------------------------------------------------------------


def test_discovery_is_off_by_default_and_on_in_the_discovery_presets():
    assert preset_policy("default").source_enabled("session_discovery") is False
    assert preset_policy("off").source_enabled("session_discovery") is False
    assert preset_policy("local_only").llm_allowed("session_discovery") is False
    assert preset_policy("balanced").llm_allowed("session_discovery") is True
    assert preset_policy("full").llm_allowed("session_discovery") is True


def test_the_legacy_default_is_not_the_full_preset():
    """Reusing ``full`` here is how a new source switches itself on everywhere."""
    assert resolve_policy(None).policy != preset_policy("full")


def test_discovery_has_no_deterministic_stage_so_llm_off_disables_it():
    policy = preset_policy("balanced").with_llm(False)
    runtime = {rt.key: rt for rt in policy.runtime(provider_available=True)}

    assert runtime["session_discovery"].status == "disabled"
    assert policy.llm_allowed("session_discovery") is False


def test_discovery_without_a_provider_is_skipped_not_failed():
    runtime = {
        rt.key: rt for rt in preset_policy("balanced").runtime(provider_available=False)
    }
    assert runtime["session_discovery"].status == "skipped_no_provider"


def test_discovery_budget_round_trips_through_the_config_block():
    policy = preset_policy("balanced").with_discovery(max_sessions=4, max_input_tokens=9000)
    block = policy.to_config_block()

    assert block["discovery"] == {"max_sessions": 4, "max_input_tokens": 9000}
    assert resolve_policy({"decisions": block}).policy == policy
    assert policy.preset_name() == "custom"


def test_a_default_budget_is_not_written_back():
    assert "discovery" not in preset_policy("balanced").to_config_block()


@pytest.mark.parametrize(
    "raw",
    [
        {"max_sessions": 0},
        {"max_sessions": 999},
        {"max_input_tokens": 10},
        {"max_input_tokens": 10_000_000},
        {"max_sessions": "twelve"},
        {"max_sessions": True},
    ],
)
def test_out_of_range_budgets_warn_and_fall_back(raw):
    resolution = resolve_policy({"decisions": {"discovery": raw}})

    assert resolution.warnings
    assert resolution.policy.discovery == DiscoveryBudget()


def test_a_non_mapping_discovery_block_warns():
    resolution = resolve_policy({"decisions": {"discovery": "big"}})

    assert any("discovery" in w for w in resolution.warnings)
    assert resolution.policy.discovery == DiscoveryBudget()


def test_with_discovery_rejects_an_out_of_range_value():
    with pytest.raises(ValueError, match="max_sessions"):
        preset_policy("balanced").with_discovery(max_sessions=0)


def test_a_stored_preset_does_not_adopt_a_source_added_after_it_was_written():
    """The upgrade path that `_LEGACY_DEFAULT` alone does not cover.

    `write_policy` stamps `preset:` into every config it writes, alongside a
    `sources:` block enumerating every source that existed then. Resolving the
    preset's *current* membership over that block is how a stored `balanced`
    would silently acquire a model call on upgrade.
    """
    stored = {
        "decisions": {
            "preset": "balanced",
            "enabled": True,
            "llm": True,
            "sources": {
                "inline_marker": True,
                "git_archaeology": True,
                "adr": True,
                "pr": True,
                "comment": False,
                "session": True,
            },
        }
    }
    policy = resolve_policy(stored).policy

    assert policy.source_enabled("session_discovery") is False
    assert policy.llm_allowed("session_discovery") is False
    assert policy.source_enabled("session") is True


def test_a_bare_preset_declaration_still_gets_its_current_membership():
    """No enumeration means no claim about which sources it covered."""
    policy = resolve_policy({"decisions": {"preset": "balanced"}}).policy

    assert policy.llm_allowed("session_discovery") is True


def test_a_preset_written_today_round_trips_with_discovery_on():
    policy = preset_policy("balanced")
    stored = {"decisions": {**policy.to_config_block(), "preset": "balanced"}}

    assert resolve_policy(stored).policy == policy
