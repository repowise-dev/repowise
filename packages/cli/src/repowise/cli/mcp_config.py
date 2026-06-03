"""Generic MCP config helpers for repowise."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tomllib
from pathlib import Path

import click


def generate_mcp_config(repo_path: Path) -> dict:
    """Generate MCP config JSON for a repository.

    Returns a dict in the standard mcpServers format.
    """
    abs_path = str(repo_path.resolve()).replace("\\", "/")
    return {
        "mcpServers": {
            "repowise": {
                "command": "repowise",
                "args": ["mcp", abs_path, "--transport", "stdio"],
                "description": "repowise: codebase intelligence — docs, graph, git signals, dead code, decisions",
            }
        }
    }


def resolve_codex_executable() -> str | None:
    """Return the executable path used to launch Codex, or None if unavailable."""

    return shutil.which("codex")


def is_codex_cli_installed() -> bool:
    """Return True when the Codex CLI is on PATH and runnable."""

    codex_cmd = resolve_codex_executable()
    if not codex_cmd:
        return False
    try:
        result = subprocess.run(
            [codex_cmd, "--version"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def is_codex_logged_in() -> bool:
    """Return True when the local Codex CLI reports an authenticated session."""

    codex_cmd = resolve_codex_executable()
    if not codex_cmd or not is_codex_cli_installed():
        return False
    try:
        result = subprocess.run(
            [codex_cmd, "login", "status"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def generate_codex_mcp_server_config(repo_path: Path) -> dict[str, object]:
    """Generate the Codex config.toml server table for repowise."""

    return {
        "command": "repowise",
        "args": ["mcp"],
        "cwd": str(repo_path.resolve()),
        "startup_timeout_sec": 20,
    }


def generate_codex_hooks_config() -> dict[str, object]:
    """Generate project-local Codex hooks for repowise context and freshness checks."""

    context_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Loading repowise context...",
    }
    freshness_hook = {
        "type": "command",
        "command": "repowise-augment --client codex",
        "timeout": 30,
        "statusMessage": "Checking repowise freshness...",
    }
    return {
        "hooks": {
            "SessionStart": [{"matcher": "startup|resume|clear", "hooks": [context_hook]}],
            "UserPromptSubmit": [{"hooks": [context_hook]}],
            "PostToolUse": [
                {"matcher": "Bash", "hooks": [freshness_hook]},
                {"matcher": "apply_patch|Edit|Write", "hooks": [freshness_hook]},
            ],
        }
    }


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return f"[{', '.join(json.dumps(item) for item in value)}]"
    raise TypeError(f"Unsupported TOML value: {value!r}")


def _codex_mcp_server_toml(repo_path: Path) -> str:
    lines = ["[mcp_servers.repowise]"]
    lines.extend(
        f"{key} = {_toml_value(value)}"
        for key, value in generate_codex_mcp_server_config(repo_path).items()
    )
    return "\n".join(lines)


def _toml_table_block(table_name: str, values: dict[str, object]) -> str:
    lines = [f"[{table_name}]"]
    lines.extend(f"{key} = {_toml_value(value)}" for key, value in values.items())
    return "\n".join(lines)


def save_mcp_config(repo_path: Path) -> Path:
    """Save MCP config to .repowise/mcp.json and return the path."""
    repowise_dir = repo_path / ".repowise"
    repowise_dir.mkdir(parents=True, exist_ok=True)
    config_path = repowise_dir / "mcp.json"
    config = generate_mcp_config(repo_path)
    config_path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    return config_path


def _merge_server_entries(servers: dict, new_entry: dict) -> dict:
    """Deep-merge *new_entry* server definitions into *servers* in place.

    For each server key, the generated ``command``/``args``/``description``
    overwrite the stored values (so path/command changes take effect), but any
    other keys the user added to the entry — most importantly an ``env`` block
    carrying BYOK provider keys — are preserved. A shallow ``servers.update()``
    would replace the whole entry and silently wipe ``env`` on every
    re-registration (``repowise init`` / ``update``). See issue #307.
    """
    for name, entry in new_entry.items():
        current = servers.get(name)
        if isinstance(current, dict) and isinstance(entry, dict):
            merged_entry = dict(current)
            merged_entry.update(entry)
            servers[name] = merged_entry
        else:
            servers[name] = entry
    return servers


def _ensure_valid_toml(merged_text: str, config_path: Path) -> None:
    """Abort before writing if the regex merge produced invalid TOML.

    The merge validates the *existing* file, but the table-rewrite regex only
    matches the bare ``[mcp_servers.repowise]`` / ``[features]`` spellings. A user
    who expressed the same key differently (quoted ``["features"]`` or inline under
    a parent table) would slip past the regex, and appending our block would yield a
    duplicate-key file. Re-parsing the merged result turns every such case into a
    clean abort with the original file untouched.
    """

    try:
        tomllib.loads(merged_text)
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: merging the repowise entry would produce "
            "invalid TOML (an existing entry may use a different key spelling). "
            "No changes were written."
        ) from exc


def enable_codex_hooks_feature(repo_path: Path) -> Path:
    """Enable Codex hooks in project-local .codex/config.toml."""

    config_path = repo_path / ".codex" / "config.toml"

    try:
        if config_path.exists():
            existing_text = config_path.read_text(encoding="utf-8")
            doc = tomllib.loads(existing_text)
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            existing_text = ""
            doc = {}
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid TOML. "
            "Fix or remove it and retry; no changes were written."
        ) from exc

    existing_features = doc.get("features", {})
    if not isinstance(existing_features, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: [features] must be a TOML table. "
            "Fix or remove it and retry; no changes were written."
        )

    features = dict(existing_features)
    features["hooks"] = True
    feature_block = _toml_table_block("features", features)
    table_re = re.compile(r"(?ms)^\s*\[features\]\s*\n.*?(?=^\s*\[|\Z)")
    merged_text = table_re.sub("", existing_text).rstrip()
    merged_text = f"{merged_text}\n\n{feature_block}\n" if merged_text else f"{feature_block}\n"
    _ensure_valid_toml(merged_text, config_path)
    config_path.write_text(merged_text, encoding="utf-8")
    return config_path


def save_codex_mcp_config(repo_path: Path) -> Path:
    """Merge the repowise MCP server into project-local .codex/config.toml."""

    config_path = repo_path / ".codex" / "config.toml"
    server_block = _codex_mcp_server_toml(repo_path)

    try:
        if config_path.exists():
            existing_text = config_path.read_text(encoding="utf-8")
            doc = tomllib.loads(existing_text)
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            config_path.write_text(f"{server_block}\n", encoding="utf-8")
            return config_path
    except tomllib.TOMLDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid TOML. "
            "Fix or remove it and retry; no changes were written."
        ) from exc

    servers = doc.get("mcp_servers")
    if servers is not None and not isinstance(servers, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: [mcp_servers] must be a TOML table. "
            "Fix or remove it and retry; no changes were written."
        )
    if isinstance(servers, dict):
        repowise = servers.get("repowise")
        if repowise is not None and not isinstance(repowise, dict):
            raise click.ClickException(
                f"Cannot update {config_path}: [mcp_servers.repowise] must be a TOML table. "
                "Fix or remove it and retry; no changes were written."
            )

    table_re = re.compile(r"(?ms)^\s*\[mcp_servers\.repowise\]\s*\n.*?(?=^\s*\[|\Z)")
    merged_text = table_re.sub("", existing_text).rstrip()
    merged_text = f"{merged_text}\n\n{server_block}\n" if merged_text else f"{server_block}\n"
    _ensure_valid_toml(merged_text, config_path)
    config_path.write_text(merged_text, encoding="utf-8")
    return config_path


def save_codex_hooks_config(repo_path: Path) -> Path:
    """Merge repowise hooks into project-local .codex/hooks.json."""

    hooks_path = repo_path / ".codex" / "hooks.json"
    new_config = generate_codex_hooks_config()

    if hooks_path.exists():
        existing = load_existing_config(hooks_path)
    else:
        hooks_path.parent.mkdir(parents=True, exist_ok=True)
        existing = {}

    hooks = existing.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise click.ClickException(
            f"Cannot update {hooks_path}: hooks must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
        )

    for event, entries in new_config["hooks"].items():
        event_hooks = hooks.setdefault(event, [])
        if not isinstance(event_hooks, list):
            raise click.ClickException(
                f"Cannot update {hooks_path}: hooks.{event} must contain a JSON array. "
                "Fix or remove it and retry; no changes were written."
            )
        for entry in entries:
            if not _has_repowise_hook_for_matcher(event_hooks, entry.get("matcher")):
                event_hooks.append(entry)

    hooks_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    enable_codex_hooks_feature(repo_path)
    return hooks_path


def save_root_mcp_config(repo_path: Path) -> Path:
    """Write .mcp.json at repo root for MCP clients that support discovery.

    Merges the repowise server entry into any existing mcpServers block
    so other MCP servers configured by the user are preserved.
    """
    config_path = repo_path / ".mcp.json"
    new_entry = generate_mcp_config(repo_path)["mcpServers"]

    if config_path.exists():
        existing = load_existing_config(config_path)
        servers = dict(existing.get("mcpServers", {}))
        _merge_server_entries(servers, new_entry)
        existing["mcpServers"] = servers
        merged = existing
    else:
        merged = {"mcpServers": new_entry}

    config_path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return config_path


def merge_mcp_entry(config_path: Path, new_entry: dict) -> bool:
    """Merge *new_entry* into the mcpServers block of *config_path*.

    Creates the file if it doesn't exist. Returns True on success.

    The per-server merge is deep: generated fields overwrite stored ones, but
    user-added keys such as an ``env`` block are preserved across
    re-registration (see :func:`_merge_server_entries`).
    """
    try:
        if config_path.exists():
            existing = load_existing_config(config_path)
        else:
            config_path.parent.mkdir(parents=True, exist_ok=True)
            existing = {}

        servers = dict(existing.get("mcpServers", {}))
        _merge_server_entries(servers, new_entry)
        existing["mcpServers"] = servers
        config_path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
        return True
    except OSError:
        return False


def load_existing_config(config_path: Path) -> dict:
    """Load an existing JSON config without silently replacing bad content."""
    try:
        existing = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file is not valid JSON. "
            "Fix or remove it and retry; no changes were written."
        ) from exc
    except OSError as exc:
        raise click.ClickException(
            f"Cannot update {config_path}: existing file could not be read. "
            "Fix the file permissions and retry; no changes were written."
        ) from exc
    if not isinstance(existing, dict):
        raise click.ClickException(
            f"Cannot update {config_path}: existing file must contain a JSON object. "
            "Fix or remove it and retry; no changes were written."
        )
    return existing


def _is_repowise_hook(hook: dict) -> bool:
    cmd = hook.get("command", "")
    return "repowise-augment" in cmd or "repowise augment" in cmd


def _has_repowise_hook_for_matcher(hook_list: list, matcher: object) -> bool:
    """Check if a repowise augment hook is registered for a matcher group."""

    for entry in hook_list:
        if entry.get("matcher") != matcher:
            continue
        for hook in entry.get("hooks", []):
            if _is_repowise_hook(hook):
                return True
    return False


def format_setup_instructions(repo_path: Path) -> str:
    """Return human-readable setup instructions for MCP clients."""
    config = generate_mcp_config(repo_path)
    server_block = json.dumps(config["mcpServers"]["repowise"], indent=4)
    abs_path = str(repo_path.resolve()).replace("\\", "/")

    return f"""
MCP Server Configuration
========================

Project .mcp.json: automatically written for MCP clients that support repo-local discovery.

Cursor (.cursor/mcp.json):
  {server_block}

Cline (cline_mcp_settings.json):
  "mcpServers": {{
    "repowise": {server_block}
  }}

Or run directly:
  repowise mcp {abs_path}
  repowise mcp {abs_path} --transport sse --port 7338

Config saved to: {repo_path / ".repowise" / "mcp.json"}
""".strip()
