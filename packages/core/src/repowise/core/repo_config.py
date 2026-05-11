"""Repo-local configuration helpers shared by CLI, server, and core paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.yaml"


def get_repowise_dir(repo_path: Path | str) -> Path:
    """Return the repo-local ``.repowise`` directory."""
    return Path(repo_path) / ".repowise"


def load_repo_config(repo_path: Path | str) -> dict[str, Any]:
    """Load ``.repowise/config.yaml`` or return an empty dict if absent."""
    config_path = get_repowise_dir(repo_path) / CONFIG_FILENAME
    if not config_path.exists():
        return {}

    text = config_path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]

        result = yaml.safe_load(text) or {}
        if isinstance(result, dict) and isinstance(result.get("reasoning"), bool):
            raw_reasoning = _read_flat_scalar(text, "reasoning")
            if raw_reasoning:
                result["reasoning"] = raw_reasoning
        return result
    except ImportError:
        # Simple line-by-line parser for the flat key: value format we write.
        result: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result


def _read_flat_scalar(text: str, key: str) -> str | None:
    """Read a top-level scalar from config text before YAML bool coercion."""
    for line in text.splitlines():
        current_key, separator, value = line.partition(":")
        if separator and current_key.strip() == key:
            return value.split("#", 1)[0].strip().strip("'\"")
    return None
