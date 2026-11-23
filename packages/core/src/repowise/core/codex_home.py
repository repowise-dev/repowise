"""Where Codex keeps its user files. Stdlib only: the hook fast path imports it."""

from __future__ import annotations

import os
from pathlib import Path


def codex_home() -> Path:
    """Codex's home directory: ``$CODEX_HOME`` when set, else ``~/.codex``.

    Codex reads its config, hooks, prompts and sessions from here, so every
    path repowise reads or writes on the user's Codex side starts at it.
    """
    value = os.environ.get("CODEX_HOME", "").strip()
    return Path(value).expanduser().resolve() if value else Path.home() / ".codex"


def legacy_codex_home() -> Path | None:
    """``~/.codex`` when ``$CODEX_HOME`` points elsewhere, else None.

    Releases before ``$CODEX_HOME`` support wrote user files there, so uninstall
    still has to clean it.
    """
    legacy = Path.home() / ".codex"
    return legacy if codex_home().resolve() != legacy.resolve() else None
