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


def config_fingerprint(repo_path: Path | str) -> str:
    """SHA-256 hex of ``.repowise/config.yaml`` + ``health-rules.json`` content.

    Used by ``repowise update`` and the index writers (CLI init, server jobs)
    to detect config changes across runs without relying on filesystem
    timestamps. Missing files are skipped, so an absent config still yields a
    stable hash.
    """
    import hashlib

    rw_dir = get_repowise_dir(repo_path)
    h = hashlib.sha256()
    for name in ("config.yaml", "health-rules.json"):
        p = rw_dir / name
        if p.exists():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


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


def save_repo_env_key(
    repo_path: Path | str,
    env_var: str,
    value: str | None,
    *,
    ensure_gitignored: bool = True,
) -> None:
    """Set (``value``) or remove (``value=None``) ``env_var`` in ``.repowise/.env``.

    The reusable filesystem primitive behind both the CLI's key persistence and
    the server's ``set_api_key``, kept here (next to :func:`load_repo_env`) so
    neither has to hand-roll a second dotenv writer. It rewrites only the one
    matching line, so unrelated keys in the file are preserved; setting an
    existing key updates it in place rather than appending a duplicate.

    Only the ``env_var`` line is touched: comments, ``export`` prefixes on other
    lines, and blank lines are left as-is. Removing a key that isn't present is a
    no-op, and never creates the file.

    A ``value`` containing a newline is rejected: it would otherwise inject extra
    ``KEY=value`` lines that a later ``load_repo_env`` would parse as separate
    environment variables. A real API key never contains one.
    """
    if value is not None and ("\n" in value or "\r" in value):
        raise ValueError("env value must not contain a newline")
    env_dir = get_repowise_dir(repo_path)
    env_file = env_dir / ".env"

    existing_lines: list[str] = []
    found = False
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            # Match both `KEY=` and `export KEY=` forms for the target var.
            bare = (
                stripped[len("export ") :].lstrip() if stripped.startswith("export ") else stripped
            )
            if bare.startswith(f"{env_var}="):
                found = True
                if value is not None:
                    existing_lines.append(f"{env_var}={value}")
                # value is None: drop the line (removal).
            else:
                existing_lines.append(line)

    if value is None:
        if not found:
            return  # nothing to remove, don't create an empty file
    elif not found:
        existing_lines.append(f"{env_var}={value}")

    env_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    # The file holds API keys; keep it owner-only where the OS honours it
    # (best-effort: a no-op on Windows).
    try:
        import os

        os.chmod(env_file, 0o600)
    except OSError:
        pass

    if ensure_gitignored:
        _ensure_env_gitignored(repo_path)


def _ensure_env_gitignored(repo_path: Path | str) -> None:
    """Add ``.repowise/.env`` to the repo's ``.gitignore`` if not already listed."""
    gitignore = Path(repo_path) / ".gitignore"
    pattern = ".repowise/.env"

    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        # Line membership, not substring, so the pattern buried in an unrelated
        # comment doesn't suppress the real ignore rule.
        if pattern in {line.strip() for line in content.splitlines()}:
            return
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n# repowise API keys (local)\n{pattern}\n"
        gitignore.write_text(content, encoding="utf-8")
    else:
        gitignore.write_text(
            f"# repowise API keys (local)\n{pattern}\n",
            encoding="utf-8",
        )
