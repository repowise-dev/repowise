"""Tests for `.repowise/health-rules.json` overrides."""

from __future__ import annotations

import json
from pathlib import Path

from repowise.core.analysis.health.config import HealthConfig
from repowise.core.analysis.health.models import Severity


def _write(tmp_path: Path, payload: dict) -> Path:
    repowise = tmp_path / ".repowise"
    repowise.mkdir()
    p = repowise / "health-rules.json"
    p.write_text(json.dumps(payload))
    return tmp_path


def test_load_missing_file_returns_empty(tmp_path: Path):
    cfg = HealthConfig.load(tmp_path)
    assert cfg.disabled_biomarkers == []
    assert cfg.rules == []


def test_load_parses_repo_wide_disabled(tmp_path: Path):
    _write(tmp_path, {"disabled_biomarkers": ["dry_violation", "primitive_obsession"]})
    cfg = HealthConfig.load(tmp_path)
    assert cfg.disabled_biomarkers == ["dry_violation", "primitive_obsession"]


def test_per_file_disabled_matches_globs(tmp_path: Path):
    _write(
        tmp_path,
        {
            "rules": [
                {
                    "path": "src/legacy/*",
                    "disabled_biomarkers": ["complex_method", "large_method"],
                },
                {
                    "path": "**/*.generated.ts",
                    "disabled_biomarkers": ["dry_violation"],
                },
            ]
        },
    )
    cfg = HealthConfig.load(tmp_path)
    files = [
        "src/legacy/old.py",
        "src/legacy/older.py",
        "src/modern/api.py",
        "frontend/lib/types.generated.ts",
    ]
    pfd = cfg.per_file_disabled(files)
    assert pfd["src/legacy/old.py"] == {"complex_method", "large_method"}
    assert pfd["src/legacy/older.py"] == {"complex_method", "large_method"}
    assert "src/modern/api.py" not in pfd
    assert pfd["frontend/lib/types.generated.ts"] == {"dry_violation"}


def test_to_analyzer_config_shape(tmp_path: Path):
    _write(
        tmp_path,
        {
            "disabled_biomarkers": ["bumpy_road"],
            "rules": [{"path": "src/*", "disabled_biomarkers": ["large_method"]}],
        },
    )
    cfg = HealthConfig.load(tmp_path)
    out = cfg.to_analyzer_config(["src/foo.py", "test/bar.py"])
    assert out["disabled_biomarkers"] == ["bumpy_road"]
    pfd = out["per_file_disabled"]
    assert isinstance(pfd, dict)
    assert pfd["src/foo.py"] == {"large_method"}
    assert "test/bar.py" not in pfd


def test_glob_and_path_glob_aliases_accepted(tmp_path: Path):
    """``glob`` (shown in older docs) and ``path_glob`` work like ``path``."""
    _write(
        tmp_path,
        {
            "rules": [
                {"glob": "tests/**", "disabled_biomarkers": ["large_method"]},
                {"path_glob": "src/legacy/*", "disabled_biomarkers": ["dry_violation"]},
            ]
        },
    )
    cfg = HealthConfig.load(tmp_path)
    assert [r.path_glob for r in cfg.rules] == ["tests/**", "src/legacy/*"]
    pfd = cfg.per_file_disabled(["tests/unit/test_x.py", "src/legacy/old.py"])
    assert pfd["tests/unit/test_x.py"] == {"large_method"}
    assert pfd["src/legacy/old.py"] == {"dry_violation"}


def test_repo_wide_severity_overrides_parsed_and_normalized(tmp_path: Path):
    _write(
        tmp_path,
        {"severity_overrides": {"complex_method": "low", "god_class": "MEDIUM"}},
    )
    cfg = HealthConfig.load(tmp_path)
    assert cfg.severity_overrides == {
        "complex_method": Severity.LOW,
        "god_class": Severity.MEDIUM,
    }
    out = cfg.to_analyzer_config(["src/foo.py"])
    assert out["severity_overrides"] == {
        "complex_method": Severity.LOW,
        "god_class": Severity.MEDIUM,
    }


def test_invalid_severity_value_dropped(tmp_path: Path):
    _write(
        tmp_path,
        {"severity_overrides": {"complex_method": "bogus", "god_class": "high"}},
    )
    cfg = HealthConfig.load(tmp_path)
    assert cfg.severity_overrides == {"god_class": Severity.HIGH}


def test_per_path_severity_overrides_materialize(tmp_path: Path):
    _write(
        tmp_path,
        {
            "rules": [
                {"path": "src/legacy/*", "severity_overrides": {"large_method": "low"}},
            ]
        },
    )
    cfg = HealthConfig.load(tmp_path)
    pfso = cfg.per_file_severity_overrides(["src/legacy/old.py", "src/modern/api.py"])
    assert pfso["src/legacy/old.py"] == {"large_method": Severity.LOW}
    assert "src/modern/api.py" not in pfso


def test_small_team_profile_expands_with_explicit_override_winning(tmp_path: Path):
    _write(
        tmp_path,
        {
            "profile": "small-team",
            # Explicit entry overrides the profile preset for this biomarker.
            "severity_overrides": {"ownership_risk": "medium"},
        },
    )
    cfg = HealthConfig.load(tmp_path)
    assert cfg.profile == "small-team"
    resolved = cfg.to_analyzer_config([])["severity_overrides"]
    # Preset entry present...
    assert resolved["developer_congestion"] == Severity.LOW
    # ...and the explicit key won over the preset's ownership_risk=LOW.
    assert resolved["ownership_risk"] == Severity.MEDIUM


def test_unknown_profile_ignored(tmp_path: Path):
    _write(tmp_path, {"profile": "enterprise"})
    cfg = HealthConfig.load(tmp_path)
    assert cfg.profile is None
    assert cfg.to_analyzer_config([])["severity_overrides"] == {}


def test_malformed_file_falls_back_silently(tmp_path: Path):
    repowise = tmp_path / ".repowise"
    repowise.mkdir()
    (repowise / "health-rules.json").write_text("{not json")
    cfg = HealthConfig.load(tmp_path)
    assert cfg.disabled_biomarkers == []
    assert cfg.rules == []
