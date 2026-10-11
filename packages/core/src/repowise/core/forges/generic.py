"""Any other git host: Gitea, cgit, a bare ssh server, or a forge with no override.

Nothing is known about its web pages, so every URL builder returns ``""`` and
callers render plain text. Commit messages are read the GitHub way, the
convention most other hosts and mirrors follow.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import github
from .base import BaseForge, ForgeKind, RemoteParts, RemoteRef, join_url
from .registry import register


@dataclass(frozen=True, slots=True)
class Generic(BaseForge):
    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        segs = parts.segments
        if not segs:
            return None
        web_base = join_url(parts.web_origin, *segs)
        return RemoteRef(self.kind, parts.host, segs[:-1], segs[-1], web_base, True)

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return ""

    def change_url(self, ref: RemoteRef, number: int) -> str:
        return ""

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        return ""

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        return ""

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        return github.change_refs(subject, body)


register(
    Generic(kind=ForgeKind.GENERIC, label="Git", merge_subject_res=github.MERGE_SUBJECT_RES)
)
