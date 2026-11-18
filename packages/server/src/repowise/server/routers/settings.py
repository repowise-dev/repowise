"""``/api/repos/{repo_id}/settings``: list and change any repowise setting.

The same registry, validation and writers as ``repowise config``, and the same
JSON: a refused write carries the CLI's refusal code (``unknown_setting``,
``env_only``, ``config_unreadable``, ``invalid_value``) in ``detail.code``.
Settings saved per user (``~/.repowise``) are listed but not written here;
``user_setting`` refuses them with the command that changes them.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, NoReturn

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.settings import AREAS, Setting, all_settings, get
from repowise.core.settings.views import APPLIES, requirement, resolve_many, view
from repowise.server.deps import get_db_session, verify_api_key
from repowise.server.routers._local_git import local_repo_path
from repowise.server.schemas.settings import (
    SettingChangeResponse,
    SettingsResponse,
    SettingUpdate,
)

router = APIRouter(tags=["settings"], dependencies=[Depends(verify_api_key)])

_FIX_FILE = "Fix the file, then try again."


def _refuse(status: int, code: str, message: str, **extra: str) -> NoReturn:
    raise HTTPException(status_code=status, detail={"code": code, "message": message, **extra})


def _setting_view(s: Setting, resolved: Any) -> dict[str, Any]:
    return {
        **view(s, resolved),
        "accepts": requirement(s),
        "applies": APPLIES[s.reload],
        "writable": s.scope == "repo",
    }


def _suggestions(repo_path: Path) -> list[dict[str, Any]]:
    # The response and the page already carry suggestions; this returns none
    # until the registry can say which values fit a repository.
    return []


@router.get("/api/repos/{repo_id}/settings", response_model=SettingsResponse)
async def list_settings(
    repo_id: str,
    include_internal: bool = Query(False, alias="all"),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Every user-facing setting by area, with its value and where it came from.

    ``?all=1`` adds debug and test settings.
    """
    from repowise.core.repo_config import RepoConfigError

    repo_path = await local_repo_path(session, repo_id)
    settings = all_settings(include_internal=include_internal)
    try:
        resolved = resolve_many(repo_path, settings)
    except RepoConfigError as exc:
        _refuse(409, "config_unreadable", str(exc), remedy=_FIX_FILE)
    views = [_setting_view(s, r) for s, r in zip(settings, resolved, strict=True)]
    areas = [
        {"area": area, "settings": [v for v in views if v["area"] == area]} for area in AREAS
    ]
    return {
        "areas": [a for a in areas if a["settings"]],
        "suggestions": _suggestions(repo_path),
    }


@router.patch("/api/repos/{repo_id}/settings", response_model=SettingChangeResponse)
async def change_setting(
    repo_id: str,
    body: SettingUpdate,
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, Any]:
    """Set one setting, or reset it to its default, in ``.repowise/config.yaml``.

    Answers with what changed, what moved with it, and the undo commands.
    """
    from repowise.core.repo_config import RepoConfigError
    from repowise.core.settings.views import change_report
    from repowise.core.settings.writers import EnvOnlyError, reset_value, set_value

    repo_path = await local_repo_path(session, repo_id)
    try:
        setting = get(body.key)
    except KeyError:
        _refuse(404, "unknown_setting", f"No setting named {body.key}.")
    if setting.scope == "user":
        _refuse(
            409,
            "user_setting",
            f"{setting.key} is saved for your user account, not this repository.",
            remedy=f"Run `repowise config set {setting.key} <value>` in a terminal.",
        )
    try:
        if body.reset:
            change = reset_value(repo_path, setting.key)
        else:
            change = set_value(repo_path, setting.key, body.value)
    except EnvOnlyError as exc:
        _refuse(409, "env_only", str(exc))
    except RepoConfigError as exc:
        _refuse(
            409,
            "config_unreadable",
            f"{exc}. Nothing was saved, so the file keeps what it has.",
            remedy=_FIX_FILE,
        )
    except ValueError as exc:
        _refuse(400, "invalid_value", str(exc), accepts=requirement(setting))
    return change_report("reset" if body.reset else "set", change)
