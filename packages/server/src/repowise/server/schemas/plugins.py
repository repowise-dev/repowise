"""``/api/repos/{repo_id}/plugins``: ``repowise plugins --format json`` for one repository.

Field names are the CLI's, so a page and an agent read the same shape. A
setting carries whether it has a value, never the value.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class PluginStorage(BaseModel):
    """Storage revisions a plugin registered, as ``namespace:revision``."""

    applied: list[str] = Field(default_factory=list)
    pending: list[str] = Field(default_factory=list)


class PluginMissingSetting(BaseModel):
    """A required setting with no value, and the one command that sets it."""

    key: str
    fix: str


class PluginSettingState(BaseModel):
    """One setting a plugin declares. ``set`` says it has a value; the value is never sent."""

    key: str
    env: str | None = None
    secret: bool = False
    set: bool


class PluginRow(BaseModel):
    """One installed plugin and what it registered."""

    name: str
    #: ``entry point`` or ``env``.
    source: str
    target: str
    #: ``loaded``, ``failed``, ``skipped`` or ``pending``.
    status: str
    error: str | None = None
    #: ``kind:name`` pairs, e.g. ``evidence-source:pgstat`` or ``setting:plugins.pgstat.db``.
    seams: list[str] = Field(default_factory=list)
    storage: PluginStorage = Field(default_factory=PluginStorage)
    version: str | None = None
    configured: bool = True
    missing: list[PluginMissingSetting] = Field(default_factory=list)
    settings: list[PluginSettingState] = Field(default_factory=list)
    next_command: str | None = None


class SourceLastSync(BaseModel):
    """How a source's last sync ended."""

    at: str | None = None
    #: ``stored``, ``unavailable``, ``refused`` or ``failed``.
    state: str | None = None
    stored_at: str | None = None
    batches: int | None = None
    message: str | None = None


class SourceRow(BaseModel):
    """One source on a fetching seam. ``plugin`` is ``None`` for a built-in source."""

    source: str
    seam: str
    plugin: str | None = None
    configured: bool
    message: str | None = None
    next_command: str
    last_sync: SourceLastSync | None = None


class SeamRow(BaseModel):
    default: str | None = None
    implementations: list[str] = Field(default_factory=list)


class PluginsResponse(BaseModel):
    """What ``repowise plugins --format json`` prints, plus this server's running syncs."""

    api_version: int
    plugins: list[PluginRow] = Field(default_factory=list)
    sources: list[SourceRow] = Field(default_factory=list)
    seams: dict[str, SeamRow] = Field(default_factory=dict)
    document_extractors: dict[str, str] = Field(default_factory=dict)
    #: Sources a sync started from this server is running now; ``*`` means every configured one.
    syncing: list[str] = Field(default_factory=list)
    #: Why the last sync started here did not run, when it did not.
    sync_error: str | None = None


class SourcesSyncRequest(BaseModel):
    """Sync one source by name, or every configured source when ``source`` is omitted."""

    source: str | None = Field(default=None, max_length=128)


class SourcesSyncAccepted(BaseModel):
    accepted: bool = True
    syncing: list[str] = Field(default_factory=list)
