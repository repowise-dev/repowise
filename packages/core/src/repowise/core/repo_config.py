"""Repo-local configuration helpers shared by CLI, server, and core paths."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

CONFIG_FILENAME = "config.yaml"

#: Named here rather than imported from the manifest module, which imports yaml
#: and the analysis package; this file is on the cheap config path.
MANIFEST_BASENAME = "decisions.yaml"


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
    except Exception as exc:
        # A broken config must never be silently treated as "no config": the
        # user's provider/model/coverage settings would silently vanish and
        # every run would use defaults. Name the file and the parse error.
        raise RepoConfigError(
            f"Could not parse {config_path}: {exc}"
        ) from exc


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
        os.replace(tmp_name, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


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

    # Ignore rule first, secret second. If the .gitignore write fails (a
    # read-only checkout, a root-owned file) this raises before the key is on
    # disk, rather than after. The failure then costs the user a saved key
    # instead of leaving a committable one behind.
    if ensure_gitignored and value is not None:
        _ensure_env_gitignored(repo_path)

    env_dir.mkdir(parents=True, exist_ok=True)
    env_file.write_text("\n".join(existing_lines) + "\n", encoding="utf-8")
    # The file holds API keys; keep it owner-only where the OS honours it
    # (best-effort: a no-op on Windows).
    try:
        import os

        os.chmod(env_file, 0o600)
    except OSError:
        pass


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
    atomic_write_text(
        gitignore, newline.join([*lines, *addition]) + newline, newline=""
    )
    return True


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
