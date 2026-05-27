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
    """Append or update a key in ``<repo>/.repowise/.env``."""
    env_dir = repo_path / ".repowise"
    env_dir.mkdir(parents=True, exist_ok=True)
    env_file = env_dir / ".env"

    # Read existing lines
    existing_lines: list[str] = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{env_var}="):
                existing_lines.append(f"{env_var}={value}")
                found = True
            else:
                existing_lines.append(line)

    if not found:
        existing_lines.append(f"{env_var}={value}")

    env_file.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")

    # Ensure .repowise/.env is gitignored
    _ensure_gitignored(repo_path)


def _ensure_gitignored(repo_path: Path) -> None:
    """Add ``.repowise/.env`` to ``.gitignore`` if not already present."""
    gitignore = repo_path / ".gitignore"
    pattern = ".repowise/.env"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        if pattern in content:
            return
        # Append to existing file
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# repowise API keys (local)\n{pattern}\n"
        gitignore.write_text(content, encoding="utf-8")
    else:
        gitignore.write_text(
            f"# repowise API keys (local)\n{pattern}\n",
            encoding="utf-8",
        )
