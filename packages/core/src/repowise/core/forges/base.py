"""The forge contract: which host a repository lives on and how to talk about it.

``Forge`` is what callers code against. ``BaseForge`` is what a forge module
subclasses: the contract plus the hooks the registry and the remote parser use
(host claims, path shapes, canonical keys), so every fact about one forge sits
in that forge's module.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
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
    def parse_change_refs(
        self, subject: str, body: str = "", *, native: bool = True
    ) -> list[int]: ...
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


def env_flag(value: str | None) -> bool:
    """Whether a CI variable is on: set, and not blank, ``false`` or ``0``."""
    return (value or "").strip().lower() not in ("", "false", "0")


@dataclass(frozen=True, slots=True)
class CiSystem:
    """Where one CI system keeps its facts about a build, as env var names.

    Each tuple is read in order and the first non-blank value wins.
    ``repo_url`` entries are groups joined with ``/``, for a CI that splits
    the URL (GitHub's server and repository). ``read_ci`` in ``forges.ci``
    turns these into a ``CiContext``.
    """

    name: str
    markers: tuple[str, ...]  # any one on means a run of this CI
    base_branch: tuple[str, ...] = ()
    base_sha: tuple[str, ...] = ()
    head_sha: tuple[str, ...] = ()
    change_number: tuple[str, ...] = ()
    repo_url: tuple[tuple[str, ...], ...] = ()
    #: A var naming where the repo is hosted, for a CI that builds repos from
    #: other forges, and its lowercased values. Unmapped values fall back to
    #: parsing the repo URL.
    provider_var: str = ""
    providers: tuple[tuple[str, ForgeKind], ...] = ()

    def active(self, env: Mapping[str, str]) -> bool:
        return any(env_flag(env.get(var)) for var in self.markers)


def unique_ints(groups: list[str]) -> list[int]:
    """First-seen order, no repeats: the order refs appear in the message."""
    seen: dict[int, None] = {}
    for g in groups:
        if g:
            seen.setdefault(int(g), None)
    return list(seen)


def collect_refs(
    subject: str,
    body: str,
    subject_res: tuple[re.Pattern[str], ...],
    anywhere_res: tuple[re.Pattern[str], ...],
) -> list[int]:
    """Numbers captured by *subject_res* in the subject and *anywhere_res* in both.

    Merge and squash conventions only hold on the subject line; a body can
    quote other commits' subjects. Each pattern has one capture group of at
    most nine digits: a huge run of digits is no change number and would hit
    int()'s digit limit.
    """
    found = [m.group(1) for p in subject_res for m in p.finditer(subject or "")]
    for text in (subject, body):
        if text:
            for p in anywhere_res:
                found.extend(m.group(1) for m in p.finditer(text))
    return unique_ints(found)


@dataclass(frozen=True, slots=True)
class BaseForge:
    """Shared defaults; a forge module subclasses this and calls ``register``."""

    kind: ForgeKind
    label: str
    change_term: str = "pull request"
    #: The CI system this forge runs, if it has one.
    ci: CiSystem | None = None
    #: Path segments that start a web route on this forge and never name a
    #: group or repo, so they are cut from a URL on any host.
    route_markers: frozenset[str] = frozenset()
    #: Forms that say a commit *is* one change (a merge or squash message),
    #: read on the subject and on the body. A mention of another change, such
    #: as a link, is not one; those are ``parse_change_refs``.
    merge_subject_res: tuple[re.Pattern[str], ...] = ()
    merge_body_res: tuple[re.Pattern[str], ...] = ()
    #: Merge forms that only mean a change when the repo is on this forge.
    native_merge_subject_res: tuple[re.Pattern[str], ...] = ()
    #: On this forge the body's merge trailer outranks a subject suffix (a
    #: cherry-picked ``(#7)`` merged through MR ``!3`` is MR 3).
    merge_body_first: bool = False
    #: The noreply address this forge mints for a login, matched on the
    #: lowercased email, with a ``login`` group. ``noreply_fold`` is what the
    #: variants of one login fold to (an ``re.Match.expand`` template); empty
    #: keeps the address. ``forges.identity`` reads these for every forge.
    noreply_re: re.Pattern[str] | None = None
    noreply_fold: str = ""
    #: Automation this forge runs, matched on the raw name or email. Bots that
    #: commit to any host are in ``forges.identity``, not here.
    bot_name_re: re.Pattern[str] | None = None
    bot_email_re: re.Pattern[str] | None = None

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

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        """Every change the message mentions. ``native=False`` reads it for a
        repo on another forge, dropping forms that only hold on this one.

        No convention known: no refs."""
        return []

    def normalize_identity(self, email: str, name: str) -> tuple[str, bool]:
        """``(canonical_email, is_bot)``, read against every forge's rules,
        since an address keeps its minting forge's shape after a move."""
        from .identity import normalize_identity  # identity reads the registry

        return normalize_identity(email, name)
