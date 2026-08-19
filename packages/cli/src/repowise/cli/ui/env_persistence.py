""".env persistence — save/load API keys in .repowise/.env."""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(repo_path: Path) -> None:
    """Load ``<repo>/.repowise/.env`` into ``os.environ`` (without overwriting).

    Supports ``export KEY=value``, quoted values (``KEY="value"``, ``KEY='value'``),
    and inline comments (``KEY=value  # comment``).
    """
    env_file = repo_path / ".repowise" / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # Support `export KEY=value`
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, raw_value = line.partition("=")
        key = key.strip()
        # Strip inline comments from the value (e.g. "sk-xxx  # my key")
        raw_value = raw_value.strip()
        if "#" in raw_value:
            # Only strip if # is preceded by whitespace (avoid stripping # in URLs)
            hash_idx = raw_value.find(" #")
            if hash_idx == -1:
                hash_idx = raw_value.find("\t#")
            if hash_idx >= 0:
                raw_value = raw_value[:hash_idx].rstrip()
        # Strip matching surrounding quotes
        value = _strip_quotes(raw_value)
        # Don't overwrite existing env vars (explicit env takes priority)
        if key and value and key not in os.environ:
            os.environ[key] = value


def _strip_quotes(value: str) -> str:
    """Strip one pair of matching surrounding single or double quotes."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _save_key_to_dotenv(repo_path: Path, env_var: str, value: str) -> None:
    """Append or update a key in ``<repo>/.repowise/.env``.

    Thin wrapper over the shared core writer so the CLI and the server persist
    keys through one implementation (see ``core.repo_config.save_repo_env_key``).
    """
    from repowise.core.repo_config import save_repo_env_key

    save_repo_env_key(repo_path, env_var, value)
