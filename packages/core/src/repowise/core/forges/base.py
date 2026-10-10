"""The forge contract: which host a repository lives on and how to talk about it.

``Forge`` is what callers code against. ``BaseForge`` is what a forge module
subclasses: the contract plus the hooks the registry and the remote parser use
(host claims, path shapes, canonical keys), so every fact about one forge sits
in that forge's module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol
from urllib.parse import quote


class ForgeKind(StrEnum):
    GITHUB = "github"
    GITLAB = "gitlab"
    AZURE = "azure"
    BITBUCKET = "bitbucket"
    GENERIC = "generic"


@dataclass(frozen=True, slots=True)
class RemoteRef:
    """One remote, parsed. Fields hold unescaped names; ``web_base`` is escaped."""

    forge: ForgeKind
    host: str  # lowercased, no userinfo, no port
    namespace: tuple[str, ...]  # ("group", "sub") / ("org", "project") / ("owner",)
    repo: str
    web_base: str  # https://host/group/sub/repo or https://dev.azure.com/org/project/_git/repo
    is_self_hosted: bool


@dataclass(frozen=True, slots=True)
class RemoteParts:
    """A remote split into host and path, before any forge reads it.

    ``segments`` are unescaped, with ``.git`` dropped from the last one and any
    web route (GitLab's ``/-/...``) cut off. ``web_origin`` is where the web UI
    most likely lives: the remote's own origin for http(s), else https on the
    bare host, since an ssh port says nothing about the web port.
    """

    scheme: str  # "http", "https", "ssh", "git" or "scp"
    host: str
    segments: tuple[str, ...]
    web_origin: str


class Forge(Protocol):
    kind: ForgeKind
    label: str  # "GitHub", "GitLab", "Azure DevOps", "Bitbucket", "Git"
    change_term: str  # "pull request" | "merge request"

    def commit_url(self, ref: RemoteRef, sha: str) -> str: ...
    def change_url(self, ref: RemoteRef, number: int) -> str: ...
    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str: ...
    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str: ...
    def format_change_ref(self, number: int) -> str: ...
    def parse_change_refs(self, subject: str, body: str = "") -> list[int]: ...
    def normalize_identity(self, email: str, name: str) -> tuple[str, bool]: ...


_HOST_RE = re.compile(r"[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?|[0-9a-f:.]*:[0-9a-f:.]*")


def split_host_port(authority: str) -> tuple[str, str] | None:
    """``(host, port)`` from ``host[:port]`` or ``[v6]:port``, host lowercased.

    ``None`` when malformed. Parsed remotes and host overrides both go through
    here, so an override key always compares equal to a parsed host.
    """
    if authority.startswith("["):
        host, sep, tail = authority[1:].partition("]")
        if not sep or (tail and not tail.startswith(":")):
            return None
        port = tail[1:]
    else:
        host, _, port = authority.partition(":")
    host = host.lower()
    if not _HOST_RE.fullmatch(host) or (port and not port.isdigit()):
        return None
    return host, port


#: A full or abbreviated commit id (sha1 or sha256), as opposed to a branch.
_SHA_RE = re.compile(r"[0-9a-fA-F]{7,64}")


def looks_like_sha(rev: str) -> bool:
    return bool(_SHA_RE.fullmatch(rev))


def quote_segment(segment: str) -> str:
    """Escape one path segment for a URL; spaces in Azure names are the usual case."""
    return quote(segment, safe="!$&'()*+,;=:@~")


def quote_path(path: str) -> str:
    """Escape a repo-relative file path, keeping its slashes."""
    return quote(path.lstrip("/"), safe="/!$&'()*+,;=:@~")


def join_url(origin: str, *segments: str) -> str:
    return "/".join([origin, *(quote_segment(s) for s in segments)])


@dataclass(frozen=True, slots=True)
class BaseForge:
    """Shared defaults; a forge module subclasses this and calls ``register``."""

    kind: ForgeKind
    label: str
    change_term: str = "pull request"
    #: Env vars whose presence means a CI run on this forge.
    ci_env_markers: tuple[str, ...] = ()
    #: Path segments that start a web route on this forge and never name a
    #: group or repo, so they are cut from a URL on any host.
    route_markers: frozenset[str] = frozenset()

    # -- registry hooks -------------------------------------------------

    def claims_host(self, host: str) -> bool:
        """Whether *host* is this forge's by name alone (no override needed)."""
        return False

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        """The remote in this forge's path shape, or ``None`` when it does not fit."""
        raise NotImplementedError

    def canonical_path(self, parts: RemoteParts) -> str | None:
        """A forge-specific lookup key; ``None`` keeps the plain host/path key."""
        return None

    # -- contract ---------------------------------------------------------

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        raise NotImplementedError

    def change_url(self, ref: RemoteRef, number: int) -> str:
        raise NotImplementedError

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        raise NotImplementedError

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        raise NotImplementedError

    def format_change_ref(self, number: int) -> str:
        return f"#{number}"

    def parse_change_refs(self, subject: str, body: str = "") -> list[int]:
        """No convention known: no refs."""
        return []

    def normalize_identity(self, email: str, name: str) -> tuple[str, bool]:
        """No noreply form or bot account known: the email lowercased, a person."""
        return (email or "").strip().lower(), False
