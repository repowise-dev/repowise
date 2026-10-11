"""CLAUDE.md preview and write response models."""

from __future__ import annotations

from pydantic import BaseModel


class ClaudeMdResponse(BaseModel):
    """The Repowise-managed section, rendered but not written to disk."""

    content: str
    generated_at: str
    repo_name: str
    #: The H3 headings present in ``content``.
    sections: list[str] = []


class ClaudeMdGenerateResponse(BaseModel):
    """Where the regenerated section landed."""

    status: str = "generated"
    path: str
    generated_at: str
