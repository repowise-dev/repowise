"""Repo settings the PATCH and POST bodies carry that live in ``.repowise/config.yaml``.

``exclude_patterns`` and ``wiki_style`` are read by the CLI and the indexer
from the repo's config.yaml, so the server writes them there and keeps only the
remaining keys in ``Repository.settings_json``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import HTTPException

from repowise.server.routers._repo_generate import _reject_unknown_style
from repowise.server.schemas import RepoResponse


def stored_settings(raw: str | None) -> dict[str, Any]:
    """The row's ``settings_json`` as a dict; ``{}`` when unparseable."""
    try:
        data = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _validate(updates: dict[str, Any]) -> None:
    """400 for a value the CLI could not read back."""
    style = updates.get("wiki_style")
    if style is not None:
        if not isinstance(style, str):
            raise HTTPException(status_code=400, detail="wiki_style must be a string.")
        _reject_unknown_style(style, "wiki_style")
    patterns = updates.get("exclude_patterns")
    if patterns is not None and not (
        isinstance(patterns, list) and all(isinstance(p, str) for p in patterns)
    ):
        raise HTTPException(status_code=400, detail="exclude_patterns must be a list of strings.")


def save_settings(
    local_path: str, settings: dict[str, Any], stored: dict[str, Any]
) -> dict[str, Any]:
    """Write the config.yaml keys of *settings*; return what ``settings_json`` keeps.

    *stored* is the row's current settings. When *settings* sends none of the
    file keys, nothing is written and any legacy copy stays in the row. When it
    sends one, legacy copies move into the file with it and leave the row. A
    checkout this server cannot reach or write is an error, never a silent
    write to the row instead.
    """
    from repowise.core.repo_config import FILE_SETTING_KEYS, RepoConfigError, save_file_settings

    updates = {k: settings[k] for k in FILE_SETTING_KEYS if k in settings}
    rest = {k: v for k, v in settings.items() if k not in FILE_SETTING_KEYS}
    if not updates:
        return {**rest, **{k: stored[k] for k in FILE_SETTING_KEYS if k in stored}}
    _validate(updates)
    repo_path = Path(local_path)
    if not repo_path.is_dir():
        raise HTTPException(
            status_code=409,
            detail=(
                f"Can't save excluded paths or documentation style: {repo_path} is not "
                "reachable from this server. They are stored in its .repowise/config.yaml."
            ),
        )
    try:
        save_file_settings(repo_path, updates, stored)
    except RepoConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Can't write {repo_path / '.repowise' / 'config.yaml'}: {exc.strerror or exc}",
        ) from exc
    return rest


def effective_settings(repo: Any) -> dict[str, Any]:
    """The row's settings with the config.yaml keys read from its checkout.

    A legacy stored value shows until the file sets the key. An unreadable
    config leaves the stored settings as they are: a listing must not fail on
    one broken file, and saving those keys reports the parse error.
    """
    from repowise.core.repo_config import (
        FILE_SETTING_KEYS,
        RepoConfigError,
        resolve_file_settings,
    )

    stored = stored_settings(repo.settings_json)
    if not repo.local_path:
        return stored
    try:
        effective = resolve_file_settings(repo.local_path, stored)
    except (RepoConfigError, OSError):
        return stored
    rest = {k: v for k, v in stored.items() if k not in FILE_SETTING_KEYS}
    return {**rest, **effective}


def repo_response(repo: Any) -> RepoResponse:
    """:class:`RepoResponse` for *repo*, settings as the CLI sees them."""
    return RepoResponse.from_orm(repo, settings=effective_settings(repo))
