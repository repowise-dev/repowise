"""The Performance settings: where they are listed and what a change to one redoes."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.settings import get
from repowise.core.settings.registry import settings_in


def test_the_performance_area_lists_its_switches():
    keys = {s.key for s in settings_in("performance")}
    assert {
        "wiki.page_versions_keep",
        "refactoring.enabled",
        "health.duplication.enabled",
        "performance.warm_caches",
    } <= keys
    assert get("health.duplication.enabled").reload == "update"
    assert get("performance.warm_caches").reload == "restart"


@pytest.mark.parametrize(
    ("text", "health", "rebuild"),
    [
        ("health:\n  duplication:\n    enabled: false\n", True, True),
        ("performance:\n  warm_caches: false\n", False, False),
    ],
)
def test_a_switch_redoes_only_what_it_governs(tmp_path: Path, text, health, rebuild):
    from repowise.cli.commands.update_cmd.config_changes import classify_config_changes
    from repowise.core.repo_config import config_dependency_fingerprints, config_fingerprint

    config = tmp_path / ".repowise" / "config.yaml"
    config.parent.mkdir()
    config.write_text("language: en\n", encoding="utf-8")
    state = {
        "config_fingerprint": config_fingerprint(tmp_path),
        "config_dependency_fingerprints": config_dependency_fingerprints(tmp_path),
    }
    config.write_text("language: en\n" + text, encoding="utf-8")

    changes = classify_config_changes(tmp_path, state, {"language": "en"} | _yaml(text))

    assert changes.changed
    assert changes.health_changed is health
    assert changes.rebuild_required is rebuild
    assert not changes.traversal_changed
    assert not changes.generation_changed


def _yaml(text: str) -> dict:
    import yaml

    return yaml.safe_load(text)
