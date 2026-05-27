"""Repository request/response models."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, field_validator


class RepoCreate(BaseModel):
    name: str
    local_path: str
    url: str = ""
    default_branch: str = "main"
    settings: dict | None = None

    @field_validator("local_path")
    @classmethod
    def validate_local_path(cls, v: str) -> str:
        resolved = Path(v).resolve()
        if ".." in Path(v).parts:
            raise ValueError("local_path must not contain '..' segments")
        if not resolved.is_dir():
            raise ValueError(f"local_path does not exist or is not a directory: {resolved}")
        if not (resolved / ".git").exists():
            raise ValueError(f"local_path is not a git repository (no .git found): {resolved}")
        return str(resolved)


class RepoUpdate(BaseModel):
    name: str | None = None
    url: str | None = None
    default_branch: str | None = None
    settings: dict | None = None


class RepoResponse(BaseModel):
    id: str
    name: str
    url: str
    local_path: str
    default_branch: str
    head_commit: str | None
    settings: dict
    created_at: datetime
    updated_at: datetime
    # Workspace context — populated when the server is running in
    # workspace mode. ``status`` indicates whether the repo has been
    # indexed yet; the web UI uses it to render "needs index" CTA cards
    # instead of silently dropping unindexed workspace repos from the
    # sidebar. Always ``None`` in single-repo mode.
    workspace_alias: str | None = None
    workspace_status: str | None = None
    is_primary: bool | None = None
    docs_enabled: bool | None = None
    docs_skip_reason: str | None = None

    @classmethod
    def from_orm(cls, obj: object) -> RepoResponse:
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            url=obj.url,  # type: ignore[attr-defined]
            local_path=obj.local_path,  # type: ignore[attr-defined]
            default_branch=obj.default_branch,  # type: ignore[attr-defined]
            head_commit=obj.head_commit,  # type: ignore[attr-defined]
            settings=json.loads(obj.settings_json),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
        )
