"""config.yaml is the one home of exclude_patterns and wiki_style."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.repo_config import (
    RepoConfigError,
    load_repo_config,
    resolve_file_settings,
    save_file_settings,
)


def _write_config(root: Path, text: str) -> Path:
    path = root / ".repowise" / "config.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_resolve_prefers_config_and_falls_back_to_stored(tmp_path: Path) -> None:
    _write_config(tmp_path, "exclude_patterns:\n- vendor/\n")
    stored = {"exclude_patterns": ["old/"], "wiki_style": "caveman", "other": 1}

    assert resolve_file_settings(tmp_path, stored) == {
        "exclude_patterns": ["vendor/"],
        "wiki_style": "caveman",
    }
    assert resolve_file_settings(tmp_path / "missing", {}) == {}


def test_resolve_raises_on_broken_config(tmp_path: Path) -> None:
    _write_config(tmp_path, "- not a mapping\n")
    with pytest.raises(RepoConfigError):
        resolve_file_settings(tmp_path, {})


def test_save_preserves_other_keys_and_migrates_legacy(tmp_path: Path) -> None:
    _write_config(tmp_path, "provider: openai\nmodel: gpt\n")

    saved = save_file_settings(
        tmp_path, {"exclude_patterns": ["dist/"]}, {"wiki_style": "reference"}
    )

    assert saved == {"exclude_patterns": ["dist/"], "wiki_style": "reference"}
    assert load_repo_config(tmp_path) == {
        "provider": "openai",
        "model": "gpt",
        "exclude_patterns": ["dist/"],
        "wiki_style": "reference",
    }


def test_save_of_unchanged_or_default_values_leaves_file_alone(tmp_path: Path) -> None:
    path = _write_config(tmp_path, "provider: openai\n")
    before = path.read_bytes()

    save_file_settings(tmp_path, {"exclude_patterns": [], "wiki_style": "comprehensive"}, {})

    assert path.read_bytes() == before
    assert list((tmp_path / ".repowise").glob("*.tmp")) == []


def test_null_in_file_counts_as_unset(tmp_path: Path) -> None:
    _write_config(tmp_path, "exclude_patterns:\nwiki_style:\n")
    stored = {"wiki_style": "tutorial"}

    assert resolve_file_settings(tmp_path, stored) == {"wiki_style": "tutorial"}
    assert save_file_settings(tmp_path, {}, stored) == {"wiki_style": "tutorial"}


def test_save_default_or_empty_removes_key(tmp_path: Path) -> None:
    _write_config(tmp_path, "wiki_style: caveman\nexclude_patterns:\n- a/\n")

    saved = save_file_settings(
        tmp_path, {"wiki_style": "comprehensive", "exclude_patterns": []}, {}
    )

    assert saved == {}
    assert load_repo_config(tmp_path) == {}


def test_save_creates_config_when_absent(tmp_path: Path) -> None:
    save_file_settings(tmp_path, {"wiki_style": "tutorial"}, {})
    assert load_repo_config(tmp_path) == {"wiki_style": "tutorial"}
