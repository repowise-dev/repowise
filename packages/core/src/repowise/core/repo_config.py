"""Repo-local configuration helpers shared by CLI, server, and core paths."""

from __future__ import annotations

import contextlib
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.yaml"

#: Named here rather than imported from the manifest module, which imports yaml
#: and the analysis package; this file is on the cheap config path.
MANIFEST_BASENAME = "decisions.yaml"

# Persisted config consumers.  Keep this matrix here, beside the canonical
# loader, so CLI and workspace update paths do not grow competing ideas of what
# a setting invalidates.  Unknown keys get their own conservative bucket: they
# are never silently folded into an unrelated health-only refresh.
CONFIG_DEPENDENCY_KEYS: dict[str, frozenset[str]] = {
    "traversal": frozenset({"exclude_patterns"}),
    "git_history": frozenset({"commit_limit", "follow_renames"}),
    "health": frozenset({"assertions", "coverage", "refactoring"}),
    "generation": frozenset(
        {
            "provider",
            "model",
            "embedder",
            "embedding_model",
            "reasoning",
            "max_tokens",
            "wiki_style",
            "language",
            "max_file_pages",
            "folder_coverage",
            "generation_context",
            "enable_onboarding",
            "context_window",
        }
    ),
    # Agent-surface preferences. init records `hooks` and `editor_files` after
    # it fingerprints the config, so leaving them in "other" turned the first
    # update after every init into a full rebuild.
    "state_only": frozenset(
        {"distill", "editor_files", "editor_setup", "events", "hooks", "mcp"}
    ),
}


def _dependency_keys() -> dict[str, frozenset[str]]:
    """:data:`CONFIG_DEPENDENCY_KEYS` plus the per-provider endpoint sections.

    ``openai: {base_url: ...}`` says where requests go, not what a page says,
    so moving an endpoint (the web UI's base URL field) must not rebuild the
    index. Imported here, not at module load: this file is on the hook path.
    """
    from repowise.core.providers.llm.specs import PROVIDER_SPECS

    keys = dict(CONFIG_DEPENDENCY_KEYS)
    keys["state_only"] = keys["state_only"] | frozenset(PROVIDER_SPECS)
    return keys


class RepoConfigError(ValueError):
    """A repo-local ``.repowise/config.yaml`` or ``.env`` could not be parsed.

    Raised instead of leaking the raw parser exception so callers can
    distinguish \"no config file\" (a normal empty dict) from \"the config file
    is broken\" (something the user must fix). The message names the file and
    the underlying parse error.
    """


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
        if not isinstance(result, dict):
            raise RepoConfigError(
                f"Could not parse {config_path}: expected a YAML mapping, "
                f"got {type(result).__name__}"
            )
        if isinstance(result.get("reasoning"), bool):
            raw_reasoning = _read_flat_scalar(text, "reasoning")
            if raw_reasoning:
                # Map common boolean strings to valid reasoning modes so
                # ``reasoning: false`` and ``reasoning: true`` work as users
                # expect instead of raising a ValueError.
                bool_to_reasoning: dict[str, str] = {"true": "auto", "false": "off"}
                result["reasoning"] = bool_to_reasoning.get(raw_reasoning, raw_reasoning)
        return result
    except ImportError:
        # Simple line-by-line parser for the flat key: value format we write.
        result: dict[str, Any] = {}
        for line in text.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                result[key.strip()] = value.strip()
        return result
    except Exception as exc:
        # A broken config must never be silently treated as "no config": the
        # user's provider/model/coverage settings would silently vanish and
        # every run would use defaults. Name the file and the parse error.
        raise RepoConfigError(f"Could not parse {config_path}: {exc}") from exc


def save_repo_config(repo_path: Path | str, config: dict[str, Any]) -> None:
    """Write ``config`` to ``.repowise/config.yaml``, replacing the file.

    Callers should round-trip through :func:`load_repo_config` and merge so
    unrelated keys are preserved; this writer just serializes the final dict.
    Key order is preserved and flow style is block style, to match the files the
    CLI writes.

    The write is atomic: serialize to a sibling temp file, fsync, then
    ``os.replace``. A crash or a serializer failure part-way leaves the previous
    bytes intact rather than a truncated config, which every reader would take
    for "no provider, no coverage settings, defaults everywhere".
    """
    import os
    import tempfile

    import yaml  # type: ignore[import-untyped]

    from repowise.core.fsutils import replace_with_retry

    cfg_dir = get_repowise_dir(repo_path)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = cfg_dir / CONFIG_FILENAME
    payload = yaml.dump(config, default_flow_style=False, sort_keys=False)

    fd, tmp_name = tempfile.mkstemp(dir=str(cfg_dir), prefix=".config.", suffix=".tmp")
    try:
        # No explicit newline: the previous writer used the platform default,
        # and config_fingerprint hashes raw bytes, so pinning LF here would
        # move the fingerprint of every existing Windows config on first save.
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    # Drop the resolver's parsed copy. Looked up, not imported: when the
    # resolver was never loaded there is no cache, and importing it here would
    # pull the provider stack into every config write.
    resolver = sys.modules.get("repowise.core.providers.llm.resolve")
    if resolver is not None:
        resolver.forget_cached(target)


#: Repo settings whose one home is ``.repowise/config.yaml``, so the CLI and the
#: web UI read and write the same value. A server row's ``settings_json`` may
#: still hold them from before: that stored value is a fallback while the file
#: lacks the key, and moves into the file on the next save.
FILE_SETTING_KEYS: tuple[str, ...] = ("exclude_patterns", "wiki_style")


def _file_setting_default(key: str) -> Any:
    """What an absent ``key`` means, so writing it would change nothing."""
    if key == "wiki_style":
        from repowise.core.generation.styles import DEFAULT_STYLE

        return DEFAULT_STYLE
    return []


def resolve_file_settings(repo_path: Path | str, stored: dict[str, Any]) -> dict[str, Any]:
    """The effective :data:`FILE_SETTING_KEYS` values: config.yaml's, else *stored*'s.

    *stored* is the legacy server copy (``settings_json``). Keys neither source
    sets are left out; a bare ``key:`` (null) in the file counts as unset.
    Raises :class:`RepoConfigError` for a broken config file.
    """
    config = load_repo_config(repo_path)
    resolved = {}
    for key in FILE_SETTING_KEYS:
        value = config.get(key)
        if value is None:
            value = stored.get(key)
        if value is not None:
            resolved[key] = value
    return resolved


def save_file_settings(
    repo_path: Path | str, updates: dict[str, Any], stored: dict[str, Any]
) -> dict[str, Any]:
    """Write *updates* for :data:`FILE_SETTING_KEYS` into config.yaml.

    Legacy *stored* values the file lacks move in with this save, so the server
    copy can be dropped afterwards. A value equal to what the file already
    means is not written, and the default removes the key: a save that changes
    nothing leaves the bytes, and so the config fingerprint, alone. Other keys
    are preserved. Returns the saved :data:`FILE_SETTING_KEYS` values.
    """
    config = load_repo_config(repo_path)
    before = dict(config)
    for key in FILE_SETTING_KEYS:
        default = _file_setting_default(key)
        if config.get(key) is None and stored.get(key) not in (None, default):
            config[key] = stored[key]
        if key in updates:
            _set_file_setting(config, key, updates[key], default)
    if config != before:
        save_repo_config(repo_path, config)
    return {key: config[key] for key in FILE_SETTING_KEYS if config.get(key) is not None}


def _set_file_setting(config: dict[str, Any], key: str, value: Any, default: Any) -> None:
    """Set *key* to *value* in *config*; the default (or ``None``) removes it."""
    value = default if value is None else value
    current = config.get(key)
    if value == (default if current is None else current):
        return
    if value == default:
        config.pop(key, None)
    else:
        config[key] = value


def config_fingerprint(repo_path: Path | str) -> str:
    """SHA-256 hex of ``.repowise/config.yaml`` + ``health-rules.json`` content.

    Used by ``repowise update`` and the index writers (CLI init, server jobs)
    to detect config changes across runs without relying on filesystem
    timestamps. Missing files are skipped, so an absent config still yields a
    stable hash.
    """
    rw_dir = get_repowise_dir(repo_path)
    h = hashlib.sha256()
    for name in ("config.yaml", "health-rules.json"):
        p = rw_dir / name
        if p.exists():
            h.update(name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()


def health_rules_fingerprint(repo_path: Path | str) -> str:
    """:func:`config_fingerprint`, degraded to an empty string when unreadable.

    What a change-health comparison pins its findings to. An empty string is a
    real answer there: it means "rules unknown", which never matches a stored
    fingerprint, so the rows are recomputed rather than trusted.
    """
    try:
        return config_fingerprint(repo_path)
    except Exception:
        return ""


def config_dependency_fingerprints(
    repo_path: Path | str, *, config: dict[str, Any] | None = None
) -> dict[str, str]:
    """Return semantic fingerprints grouped by their persisted consumers.

    ``config_fingerprint`` intentionally hashes raw bytes and remains the cheap
    broad change detector.  These hashes answer the next question: which layer
    actually became stale?  Formatting-only YAML edits therefore advance the
    broad fingerprint without paying for an analysis rebuild.

    ``health-rules.json`` belongs to the health bucket.  Its bytes are retained
    rather than parsed here because the health loader owns its validation and
    fallback semantics.
    """
    if config is None:
        config = load_repo_config(repo_path)
    dependency_keys = _dependency_keys()
    known = set().union(*dependency_keys.values())
    grouped: dict[str, Any] = {
        name: {key: config[key] for key in sorted(keys) if key in config}
        for name, keys in dependency_keys.items()
    }
    grouped["other"] = {key: config[key] for key in sorted(config) if key not in known}

    rules_path = get_repowise_dir(repo_path) / "health-rules.json"
    grouped["health"] = {
        "config": grouped["health"],
        "health_rules_sha256": (
            hashlib.sha256(rules_path.read_bytes()).hexdigest() if rules_path.exists() else None
        ),
    }

    return {
        name: hashlib.sha256(
            json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        ).hexdigest()
        for name, value in grouped.items()
    }


def changed_config_dependencies(previous: Any, current: dict[str, str]) -> set[str] | None:
    """Return changed dependency classes, or ``None`` for legacy state.

    A state file without the grouped hashes cannot safely identify which key
    moved.  Callers treat that one-time case conservatively rather than guessing
    from the new config alone.
    """
    if not isinstance(previous, dict):
        return None
    return {
        name for name in set(previous) | set(current) if previous.get(name) != current.get(name)
    }


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
    except OSError as exc:
        # The file exists (we checked above) but cannot be read — a
        # permission problem, not "no env file". Silent {} would resolve
        # every provider as unconfigured with no explanation.
        raise RepoConfigError(f"Could not read {env_file}: {exc}") from exc

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

    write_private_file(
        repo_path,
        ".env",
        "\n".join(existing_lines) + "\n",
        comment="repowise API keys (local)",
        ignore=ensure_gitignored and value is not None,
    )


def write_private_file(
    repo_path: Path | str, name: str, text: str, *, comment: str, ignore: bool = True
) -> Path:
    """Write a secret ``.repowise/<name>``: git-ignored first, owner-only after.

    Some repositories commit ``.repowise/`` for its shared config, so a secret
    there needs its own ignore rule. It goes in ``.repowise/.gitignore``, not
    the repo root's: nothing outside ``.repowise/`` is touched, and the rule
    travels with the directory when it is committed. Ignore rule first, secret
    second: if that write fails this raises before the secret is on disk
    rather than after, which costs the caller a saved secret instead of
    leaving a committable one behind.
    """
    if ignore:
        _ensure_gitignored(repo_path, name, comment)
    path = get_repowise_dir(repo_path) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    # Owner-only where the OS honours it (best-effort: a no-op on Windows).
    try:
        import os

        os.chmod(path, 0o600)
    except OSError:
        pass
    return path


def ensure_manifest_tracked(repo_path: Path | str) -> bool:
    """Let ``.repowise/decisions.yaml`` be committed. Returns whether it changed.

    The manifest is the only thing under ``.repowise/`` meant to travel with the
    repository, and a ``.repowise/`` rule blocks it: git does not descend into an
    excluded directory, so a negation for a file inside one never fires.

    The existing rule is left in place and three anchored lines are appended
    after it. Rewriting ``.repowise/`` to ``.repowise/*`` would look equivalent
    and is not: the first has no internal slash and so matches a ``.repowise``
    directory at *any* depth, while the second is anchored to this file's own
    directory. That rewrite would un-ignore every nested ``.repowise/`` in the
    tree, which is how a fixture's session database gets committed.
    """
    from repowise.core.fsutils import atomic_write_text

    gitignore = Path(repo_path) / ".gitignore"
    if not gitignore.exists():
        return False

    # Read as bytes: text mode translates CRLF away, and the line ending is
    # exactly what has to survive here. Rewriting a CRLF .gitignore as LF turns
    # one appended rule into a whole-file diff for every contributor on the
    # platform this project mostly runs on.
    raw = gitignore.read_bytes()
    content = raw.decode("utf-8", errors="replace")
    lines = content.splitlines()
    if f"!/.repowise/{MANIFEST_BASENAME}" in {line.strip() for line in lines}:
        return False

    newline = "\r\n" if b"\r\n" in raw else "\n"
    addition = [
        "",
        "# repowise: the decisions manifest is meant to be committed",
        "!/.repowise/",
        "/.repowise/*",
        f"!/.repowise/{MANIFEST_BASENAME}",
    ]
    atomic_write_text(gitignore, newline.join([*lines, *addition]) + newline, newline="")
    return True


def _ensure_gitignored(repo_path: Path | str, name: str, comment: str) -> None:
    """Ignore ``.repowise/<name>`` from ``.repowise/.gitignore`` unless git already does."""
    if _git_ignores(repo_path, f".repowise/{name}"):
        return  # e.g. a root ``.repowise/`` rule: a second line would only add noise

    gitignore = get_repowise_dir(repo_path) / ".gitignore"
    rule = f"/{name}"
    content = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    # Line membership, not substring, so the name buried in an unrelated
    # comment doesn't suppress the real ignore rule.
    if rule in {line.strip() for line in content.splitlines()}:
        return
    if content:
        content = content.rstrip("\n") + "\n\n"
    content += f"# {comment}\n{rule}\n"
    gitignore.parent.mkdir(parents=True, exist_ok=True)
    gitignore.write_text(content, encoding="utf-8")


def _git_ignores(repo_path: Path | str, pattern: str) -> bool:
    """Whether a ``.gitignore`` inside the repo already covers *pattern*.

    Rules from ``.git/info/exclude`` or a global excludes file do not count:
    teammates do not share them. ``False`` when git cannot say.
    """
    import subprocess

    try:
        result = subprocess.run(
            ["git", "check-ignore", "-v", "--no-index", pattern],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    # ``<source>:<line>:<rule><TAB><path>``; a repo .gitignore is reported relative.
    source = result.stdout.split("\t", 1)[0].rsplit(":", 2)[0]
    return Path(source).name == ".gitignore" and not Path(source).is_absolute()
