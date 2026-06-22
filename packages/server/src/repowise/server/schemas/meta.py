"""Response schemas for the ``/api/meta`` endpoints (version + changelog)."""

from __future__ import annotations

from pydantic import BaseModel


class VersionResponse(BaseModel):
    """Server version, PyPI freshness, and (optional) per-repo store status."""

    server_version: str
    latest_version: str | None = None
    #: ``None`` when PyPI could not be reached (distinct from "up to date").
    update_available: bool | None = None
    upgrade_command: str

    # Store-format status for a specific repo (populated only when ``repo_id``
    # is supplied and resolvable). ``store_compatible`` is False only when a
    # reindex is recommended; we recommend, never force.
    store_format_version: int | None = None
    store_compatible: bool | None = None
    reindex_recommended: bool = False
    reindex_command: str | None = None


class ChangelogSectionModel(BaseModel):
    name: str
    items: list[str]


class ChangelogEntryModel(BaseModel):
    version: str
    label: str | None = None
    sections: list[ChangelogSectionModel]


class ChangelogResponse(BaseModel):
    entries: list[ChangelogEntryModel]
