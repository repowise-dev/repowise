"""Which forge hosts a checkout: its remote first, then the CI it runs in.

Reads ``.git/config`` directly rather than shelling out to git: this runs on
CLI start-up and server page loads, where a process spawn per call costs more
than reading one small file.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from pathlib import Path

from .base import ForgeKind
from .registry import all_forges, forge_hosts
from .remote import parse_remote

_REMOTES = ('remote "origin"', 'remote "upstream"')


def _common_git_dir(repo_root: Path) -> Path | None:
    """The directory holding the shared ``config``, following a worktree's ``.git`` file."""
    git_path = repo_root / ".git"
    if not git_path.is_file():
        return git_path if git_path.is_dir() else None
    try:
        pointer = git_path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not pointer.startswith("gitdir:"):
        return None
    gitdir = Path(pointer.split(":", 1)[1].strip())
    if not gitdir.is_absolute():
        gitdir = (repo_root / gitdir).resolve()
    # A linked worktree names its main git dir in `commondir`; a submodule's
    # gitdir holds its own config.
    try:
        common = (gitdir / "commondir").read_text(encoding="utf-8").strip()
    except OSError:
        return gitdir
    common_dir = Path(common)
    return common_dir if common_dir.is_absolute() else (gitdir / common_dir).resolve()


def read_remote_url(repo_root: Path | str) -> str | None:
    """``origin``'s URL, else ``upstream``'s, else ``None``. Never raises."""
    git_dir = _common_git_dir(Path(repo_root))
    if git_dir is None:
        return None
    # strict=False: git writes duplicate keys (two fetch refspecs) routinely.
    # interpolation=None: a `%40` in a remote's userinfo is not a template.
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read(git_dir / "config", encoding="utf-8")
        for section in _REMOTES:
            if parser.has_option(section, "url"):
                return parser.get(section, "url").strip() or None
    except (OSError, configparser.Error, UnicodeDecodeError):
        return None
    return None


def detect_forge(
    repo_root: Path | str | None = None, env: Mapping[str, str] | None = None
) -> ForgeKind:
    """The forge for a checkout: remote, then CI env vars, else ``GENERIC``.

    A remote on an unknown host does not settle it: a self-managed GitLab on
    ``git.corp.com`` with no override is still GitLab when ``GITLAB_CI`` says so.
    ``repo_root=None`` skips the remote; ``env=None`` reads ``os.environ``.
    """
    env = os.environ if env is None else env
    if repo_root is not None:
        url = read_remote_url(repo_root)
        ref = parse_remote(url, hosts=forge_hosts(repo_root, env)) if url else None
        if ref is not None and ref.forge is not ForgeKind.GENERIC:
            return ref.forge
    for forge in all_forges():
        if forge.ci is not None and forge.ci.active(env):
            return forge.kind
    return ForgeKind.GENERIC
