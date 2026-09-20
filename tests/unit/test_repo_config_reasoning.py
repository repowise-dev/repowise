"""Tests for boolean reasoning config aliases (reasoning: true/false)."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.reasoning import normalize_reasoning

# ---------------------------------------------------------------------------
# normalize_reasoning — boolean aliases
# ---------------------------------------------------------------------------


class TestNormalizeReasoningBooleanAliases:
    def test_false_maps_to_off(self) -> None:
        assert normalize_reasoning("false") == "off"

    def test_true_maps_to_auto(self) -> None:
        assert normalize_reasoning("true") == "auto"

    def test_false_case_insensitive(self) -> None:
        assert normalize_reasoning("False") == "off"
        assert normalize_reasoning("FALSE") == "off"

    def test_true_case_insensitive(self) -> None:
        assert normalize_reasoning("True") == "auto"
        assert normalize_reasoning("TRUE") == "auto"

    def test_valid_modes_still_work(self) -> None:
        for mode in ("auto", "off", "none", "minimal", "low", "medium", "high", "xhigh", "max"):
            assert normalize_reasoning(mode) == mode

    def test_invalid_mode_still_raises(self) -> None:
        with pytest.raises(ValueError, match="reasoning must be one of"):
            normalize_reasoning("verbose")

    def test_none_returns_default(self) -> None:
        assert normalize_reasoning(None) == "auto"

    def test_none_with_custom_default(self) -> None:
        assert normalize_reasoning(None, default="off") == "off"


# ---------------------------------------------------------------------------
# load_repo_config — boolean reasoning in YAML
# ---------------------------------------------------------------------------


class TestLoadRepoConfigBooleanReasoning:
    def _write_config(self, tmp_path: Path, content: str) -> Path:
        repo = tmp_path / "repo"
        (repo / ".repowise").mkdir(parents=True)
        (repo / ".repowise" / "config.yaml").write_text(content, encoding="utf-8")
        return repo

    def test_reasoning_false_loads_as_off(self, tmp_path: Path) -> None:
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, "reasoning: false\n")
        cfg = load_repo_config(repo)
        assert cfg["reasoning"] == "off"

    def test_reasoning_true_loads_as_auto(self, tmp_path: Path) -> None:
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, "reasoning: true\n")
        cfg = load_repo_config(repo)
        assert cfg["reasoning"] == "auto"

    def test_reasoning_valid_string_unchanged(self, tmp_path: Path) -> None:
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, "reasoning: high\n")
        cfg = load_repo_config(repo)
        assert cfg["reasoning"] == "high"

    def test_reasoning_absent_key_not_set(self, tmp_path: Path) -> None:
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, "other_key: value\n")
        cfg = load_repo_config(repo)
        assert "reasoning" not in cfg

    def test_reasoning_quoted_false_passes_through(self, tmp_path: Path) -> None:
        """Quoted 'false' is a string in YAML, not a bool — no coercion path."""
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, "reasoning: 'false'\n")
        cfg = load_repo_config(repo)
        # YAML treats 'false' as the string "false", not Python False.
        # The bool-detection path doesn't trigger, but normalize_reasoning
        # handles it via the defense-in-depth alias.
        assert cfg["reasoning"] == "false"

    def test_reasoning_quoted_true_passes_through(self, tmp_path: Path) -> None:
        """Quoted 'true' is a string in YAML, not a bool — no coercion path."""
        from repowise.core.repo_config import load_repo_config

        repo = self._write_config(tmp_path, 'reasoning: "true"\n')
        cfg = load_repo_config(repo)
        assert cfg["reasoning"] == "true"
