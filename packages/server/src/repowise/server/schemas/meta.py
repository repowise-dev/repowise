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

    #: Highlights of the running release, from the bundled changelog. Drives the
    #: post-upgrade "What's new" and the "New" marks in the nav.
    highlights: list[ChangelogHighlightModel] = []
    #: Highlight titles of ``latest_version`` when it is newer, read from the
    #: changelog on GitHub (cached a day). Empty when offline or unknown.
    latest_highlights: list[str] = []


class ChangelogHighlightModel(BaseModel):
    id: str
    title: str
    blurb: str
    #: An app route; ``:id`` stands for the current repository.
    href: str | None = None


class ReleaseTypeCountsModel(BaseModel):
    fixes: int = 0
    features: int = 0
    perf: int = 0
    refactors: int = 0
    docs: int = 0


class ReleaseStatsModel(BaseModel):
    """A release's "By the numbers", from ``scripts/release_stats.py``."""

    since: str
    days: int
    prs: int
    contributors: int
    #: ``None`` when GitHub could not be asked who was new.
    first_time_contributors: int | None = None
    files_changed: int
    lines_added: int
    lines_removed: int
    types: ReleaseTypeCountsModel
    test_files_touched: int
    test_files_new: int
    issues_closed: int


class ChangelogSectionModel(BaseModel):
    name: str
    items: list[str]


class ChangelogEntryModel(BaseModel):
    #: ``unreleased`` for the next release's notes in a development build.
    version: str
    label: str | None = None
    summary: str = ""
    highlights: list[ChangelogHighlightModel] = []
    stats: ReleaseStatsModel | None = None
    sections: list[ChangelogSectionModel]


class ChangelogResponse(BaseModel):
    entries: list[ChangelogEntryModel]
