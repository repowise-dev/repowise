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


def save_repo_config(repo_path: Path | str, config: dict[str, Any]) -> None:
    """Write ``config`` to ``.repowise/config.yaml``, replacing the file.

    Callers should round-trip through :func:`load_repo_config` and merge so
    unrelated keys are preserved; this writer just serializes the final dict.
    Key order is preserved and flow style is block style, to match the files the
    CLI writes.
    """
    import yaml  # type: ignore[import-untyped]

    cfg_dir = get_repowise_dir(repo_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / CONFIG_FILENAME).write_text(
        yaml.dump(config, default_flow_style=False, sort_keys=False),
        encoding="utf-8",
    )


def _read_flat_scalar(text: str, key: str) -> str | None:
    """Read a top-level scalar from config text before YAML bool coercion."""
    for line in text.splitlines():
        current_key, separator, value = line.partition(":")
        if separator and current_key.strip() == key:
            return value.split("#", 1)[0].strip().strip("'\"")
    return None


def load_repo_env(repo_path: Path | str) -> dict[str, str]:
    """Parse ``.repowise/.env`` into a dict **without** mutating ``os.environ``.

    The CLI's ``load_dotenv`` merges the file into the live process
    environment, which is correct for ``repowise update`` (one repo per
    process) but unsafe for a long-lived ``repowise serve`` that fields
    requests for many repos in a workspace — one repo's keys would leak into
    every other repo's resolution. This pure reader lets the server resolve a
    provider per-repo from that repo's own ``.env`` instead.

    Accepts the same syntax as ``load_dotenv``: ``export KEY=value``, quoted
    values, and whitespace-delimited inline comments.
    """
    env_file = get_repowise_dir(repo_path) / ".env"
    if not env_file.exists():
        return {}

    result: dict[str, str] = {}
    try:
        text = env_file.read_text(encoding="utf-8")
    except OSError:
        return {}

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        raw_value = raw_value.strip()
        # Strip whitespace-delimited inline comments (keep '#' inside URLs).
        hash_idx = raw_value.find(" #")
        if hash_idx == -1:
            hash_idx = raw_value.find("\t#")
        if hash_idx >= 0:
            raw_value = raw_value[:hash_idx].rstrip()
        value = _strip_quotes(raw_value)
        if key and value:
            result[key] = value
    return result


def _strip_quotes(value: str) -> str:
    """Strip one pair of matching surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value
