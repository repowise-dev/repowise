"""Repository request/response models."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, field_validator

from repowise.core.docs_mode import DocsMode
from repowise.core.index_scope import load_index_scope

_URL_SCHEME_RE = re.compile(r"([A-Za-z][A-Za-z0-9+.-]*)://")
_QUERY_RE = re.compile(r"[?#]")
_SSH_SCHEMES = frozenset({"ssh", "git+ssh", "ssh+git"})


def strip_credentials(url: str | None) -> str | None:
    """Drop secrets from a remote before it leaves the server.

    A token can sit in the userinfo (``https://user:glpat-x@host/...``,
    ``git+https://ghp_x@host/...``) or the query (``?private_token=``). Every
    scheme loses its query, fragment and userinfo, except that ssh keeps a bare
    username: ``ssh://git@host/...`` and scp-style ``git@host:path`` name an
    account, not a secret, and the UI's avatar parser expects ``git@``.
    """
    if not url:
        return url
    url = url.strip()
    scheme_match = _URL_SCHEME_RE.match(url)
    if not scheme_match:
        # scp-style `user[:password]@host:path`. Git never sends a password here,
        # but one typed in (even with a `/`) would still reach the browser. A
        # local path (`/srv/a:b@c`, `.\x`, `C:\x`) is not a remote, and without a
        # `host:` after the `@` the `@` is in the path (`host:org/repo@v1.git`).
        userinfo, at, rest = url.partition("@")
        local = "\\" in userinfo or userinfo[:1] in ("/", ".")
        if at and ":" in userinfo and ":" in rest and not local:
            return f"{userinfo.split(':', 1)[0]}@{rest}"
        return url
    scheme = scheme_match.group(1)
    rest = url[scheme_match.end() :]
    userinfo, hostpart = "", rest
    # Userinfo is found before the query is cut, so a `?` or `#` typed into a
    # password cannot split it and leave a prefix behind.
    head = rest.split("/", 1)[0]
    if "@" in head:
        userinfo, _, host = head.rpartition("@")
        hostpart = host + rest[len(head) :]
    elif ":" in head:
        # An unencoded `/` in a password moves the authority past the first
        # `/`, and `user:123/x` even passes for host:port. Such a password
        # always leaves a `:` before that `/`, so only then is a later `@` the
        # end of the userinfo; `host/repo@v1.git` keeps its path. Ceiling:
        # `host:8443/repo@v1.git` loses its host. A port plus a path `@` is
        # rare, and losing a host beats a leak.
        before_query = _QUERY_RE.split(rest, maxsplit=1)[0]
        if "@" in before_query:
            userinfo, _, hostpart = before_query.partition("@")
    hostpart = _QUERY_RE.split(hostpart, maxsplit=1)[0]
    user = userinfo.split(":", 1)[0] if scheme.lower() in _SSH_SCHEMES else ""
    return f"{scheme}://{user + '@' if user else ''}{hostpart}"


class RepoCreate(BaseModel):
    name: str
    local_path: str
    url: str = ""
    default_branch: str = "main"
    settings: dict | None = None
    # Enqueue the first full index immediately after registration. On by
    # default so adding a repo from the UI takes it straight to indexed;
    # pass false to register metadata only.
    index: bool = True

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
    # ``docs_enabled`` only says whether pages exist; ``docs_mode`` says who
    # wrote them, which is what a client needs to offer the "upgrade to
    # model-written pages" path.
    docs_enabled: bool | None = None
    docs_mode: DocsMode | None = None
    docs_skip_reason: str | None = None
    # Mirrors of the same state.json read: which index tier this repo was
    # built at.
    run_mode: str | None = None
    git_tier: str | None = None
    # Canonical versioned projection shared with CLI status and MCP metadata.
    index_scope: dict | None = None
    # Set on POST /api/repos responses when registration auto-enqueued the
    # first index; clients attach to /api/jobs/{id}/stream with it.
    initial_job_id: str | None = None

    @classmethod
    def from_orm(cls, obj: object) -> RepoResponse:
        local_path = obj.local_path  # type: ignore[attr-defined]
        scope = load_index_scope(local_path) if local_path else None
        return cls(
            id=obj.id,  # type: ignore[attr-defined]
            name=obj.name,  # type: ignore[attr-defined]
            url=strip_credentials(obj.url) or "",  # type: ignore[attr-defined]
            local_path=local_path,
            default_branch=obj.default_branch,  # type: ignore[attr-defined]
            head_commit=obj.head_commit,  # type: ignore[attr-defined]
            settings=json.loads(obj.settings_json),  # type: ignore[attr-defined]
            created_at=obj.created_at,  # type: ignore[attr-defined]
            updated_at=obj.updated_at,  # type: ignore[attr-defined]
            index_scope=scope,
        )


class RepoDeletedResponse(BaseModel):
    """What a repository delete removed, for the confirmation toast."""

    ok: bool = True
    deleted_pages: int = 0


class JobAcceptedResponse(BaseModel):
    """The 202 payload every background-job launch returns.

    ``stream_token`` authorizes ``/api/jobs/{job_id}/stream`` directly, so a
    client can attach without a second round-trip or an API key in the query
    string.
    """

    job_id: str
    status: str = "accepted"
    stream_token: str


class RepoSummaryRow(BaseModel):
    """One repository's headline figures, for the multi-repo dashboard.

    Every count here is a count of the thing its name says. ``file_count`` in
    particular is file nodes only: ``/stats`` counts every ``graph_nodes`` row,
    which on this repo is 38,813 against 3,600 actual files, because symbol
    nodes live in the same table.
    """

    id: str
    name: str
    local_path: str
    updated_at: datetime | None = None
    #: "indexed" | "needs_index" | "missing_dir" — same vocabulary as
    #: ``RepoResponse.workspace_status``, which the sidebar already renders.
    status: str = "indexed"

    file_count: int = 0
    symbol_count: int = 0
    entry_point_count: int = 0

    #: Documentation pages, and how many of them are still fresh. Both counts
    #: ship rather than a percentage so a caller can print "3,797 of 4,059"
    #: and the ratio without the two disagreeing.
    doc_page_count: int = 0
    doc_fresh_page_count: int = 0

    dead_export_count: int = 0

    #: Files carrying git history, and the hotspot subset. The denominator is
    #: here because hotspots are only meaningful against it.
    tracked_file_count: int = 0
    hotspot_count: int = 0

    #: Latest health snapshot. ``None`` when the repo has never been analysed —
    #: distinct from a score of 0, which would mean "analysed, and terrible".
    average_health: float | None = None
    hotspot_health: float | None = None
    health_taken_at: datetime | None = None

    #: Index-vs-checkout freshness. ``index_behind`` is ``None`` when the
    #: comparison could not run (no git checkout on disk, unreadable HEAD)
    #: rather than ``False``, so "current" and "unknown" stay separable.
    indexed_commit: str | None = None
    live_head: str | None = None
    index_behind: bool | None = None


class ReposSummaryResponse(BaseModel):
    repos: list[RepoSummaryRow]
