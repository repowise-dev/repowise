from __future__ import annotations

from pathlib import Path

import yaml

from repowise.core.repo_config import (
    changed_config_dependencies,
    config_dependency_fingerprints,
)


def _write_config(repo: Path, **values: object) -> None:
    config_dir = repo / ".repowise"
    config_dir.mkdir(exist_ok=True)
    (config_dir / "config.yaml").write_text(
        yaml.safe_dump(values, sort_keys=False), encoding="utf-8"
    )


def test_dependency_fingerprints_classify_config_keys(tmp_path: Path) -> None:
    _write_config(
        tmp_path,
        commit_limit=1,
        follow_renames=False,
        exclude_patterns=[],
        provider="mock",
        enable_onboarding=True,
    )
    baseline = config_dependency_fingerprints(tmp_path)

    cases = [
        ("git_history", {"commit_limit": 5}),
        ("git_history", {"follow_renames": True}),
        ("traversal", {"exclude_patterns": ["generated/**"]}),
        ("generation", {"provider": "openai"}),
        ("generation", {"enable_onboarding": False}),
        ("other", {"future_setting": True}),
    ]
    for expected, change in cases:
        values = {
            "commit_limit": 1,
            "follow_renames": False,
            "exclude_patterns": [],
            "provider": "mock",
            "enable_onboarding": True,
            **change,
        }
        _write_config(tmp_path, **values)
        current = config_dependency_fingerprints(tmp_path)
        assert changed_config_dependencies(baseline, current) == {expected}


def test_health_rules_only_invalidate_health(tmp_path: Path) -> None:
    _write_config(tmp_path, commit_limit=1)
    baseline = config_dependency_fingerprints(tmp_path)
    (tmp_path / ".repowise" / "health-rules.json").write_text(
        '{"disabled_biomarkers": ["ungoverned_hotspot"]}', encoding="utf-8"
    )

    assert changed_config_dependencies(baseline, config_dependency_fingerprints(tmp_path)) == {
        "health"
    }


def test_legacy_state_is_reported_as_unknown(tmp_path: Path) -> None:
    _write_config(tmp_path, commit_limit=1)
    assert changed_config_dependencies(None, config_dependency_fingerprints(tmp_path)) is None


def test_preloaded_config_preserves_tolerant_cli_fallback(tmp_path: Path) -> None:
    config_dir = tmp_path / ".repowise"
    config_dir.mkdir()
    (config_dir / "config.yaml").write_text("not: [valid", encoding="utf-8")

    fingerprints = config_dependency_fingerprints(tmp_path, config={})

    assert set(fingerprints) == {
        "traversal",
        "git_history",
        "health",
        "generation",
        "state_only",
        "other",
    }
