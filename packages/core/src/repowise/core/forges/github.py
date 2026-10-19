"""GitHub (github.com and Enterprise Server)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .base import (
    BaseForge,
    CiSystem,
    ForgeKind,
    RemoteParts,
    RemoteRef,
    collect_refs,
    join_url,
    quote_path,
    quote_segment,
)
from .registry import register

PUBLIC_HOST = "github.com"
_PUBLIC_ORIGIN = f"https://{PUBLIC_HOST}"
# ssh.github.com serves ssh over port 443; both aliases are github.com.
_HOST_ALIASES = frozenset({PUBLIC_HOST, "www.github.com", "ssh.github.com"})

# Squash merges end the subject with ``(#12)`` (``(gh-12)`` in some projects);
# merge commits open with ``Merge pull request #12``. A bare ``#12`` is an
# issue reference as often as a PR, so it is not read as one.
_SUBJECT_REF_RES = (
    re.compile(r"\((?:#|gh-)(\d{1,9})\)", re.IGNORECASE),
    re.compile(r"^Merge pull request #(\d{1,9})\b"),
)
_URL_REF_RE = re.compile(r"/pull/(\d{1,9})\b")

# ``NNN+login@users.noreply.github.com``, or the older ``login@...`` without
# the id. Both fold to the id-less form: the id is optional, the login is not.
_NOREPLY_RE = re.compile(r"^(?:\d+\+)?(?P<login>[^@\s+]+)@users\.noreply\.github\.com$")
_NOREPLY_FOLD = r"\g<login>@users.noreply.github.com"
_BOT_NAME_RE = re.compile(r"^github[-_ ]?actions$", re.IGNORECASE)
# ``noreply@github.com`` is the system author of web merges: a bot, and its
# own key, never folded into a person.
_BOT_EMAIL_RE = re.compile(
    r"@bots\.noreply\.github\.com|^(?:actions|noreply)@github\.com$", re.IGNORECASE
)

# What says a commit *is* one PR: a merge commit's subject, or the squash
# suffix closing the subject. ``Revert "x (#5)"`` merged no PR 5, so the
# suffix must end the subject, give or take a full stop or a ``[skip ci]``.
MERGE_SUBJECT_RES = (
    _SUBJECT_REF_RES[1],
    re.compile(r"\((?:#|[Gg][Hh]-)(\d{1,9})\)[\s.]*(?:\[[^\]\n]{0,40}\][\s.]*)?$"),
)


def change_refs(subject: str, body: str = "") -> list[int]:
    """PR numbers in a GitHub commit message, in order of appearance."""
    return collect_refs(subject, body, _SUBJECT_REF_RES, (_URL_REF_RE,))


@dataclass(frozen=True, slots=True)
class GitHub(BaseForge):
    def claims_host(self, host: str) -> bool:
        return host in _HOST_ALIASES

    def parse(self, parts: RemoteParts) -> RemoteRef | None:
        if len(parts.segments) < 2:
            return None
        owner, repo = parts.segments[0], parts.segments[1]
        public = parts.host in _HOST_ALIASES
        host = PUBLIC_HOST if public else parts.host
        origin = _PUBLIC_ORIGIN if public else parts.web_origin
        return RemoteRef(
            self.kind, host, (owner,), repo, join_url(origin, owner, repo), not public
        )

    def commit_url(self, ref: RemoteRef, sha: str) -> str:
        return f"{ref.web_base}/commit/{quote_segment(sha)}"

    def change_url(self, ref: RemoteRef, number: int) -> str:
        return f"{ref.web_base}/pull/{number}"

    def blob_url(self, ref: RemoteRef, rev: str, path: str, line: int | None = None) -> str:
        anchor = f"#L{line}" if line else ""
        return f"{ref.web_base}/blob/{quote_path(rev)}/{quote_path(path)}{anchor}"

    def compare_url(self, ref: RemoteRef, base: str, head: str) -> str:
        return f"{ref.web_base}/compare/{quote_path(base)}...{quote_path(head)}"

    def parse_change_refs(self, subject: str, body: str = "", *, native: bool = True) -> list[int]:
        return change_refs(subject, body)


register(
    GitHub(
        kind=ForgeKind.GITHUB,
        label="GitHub",
        ci=CiSystem(
            name="github_actions",
            markers=("GITHUB_ACTIONS",),
            base_branch=("GITHUB_BASE_REF",),
            head_sha=("GITHUB_SHA",),
            change_number=("GITHUB_REF",),  # refs/pull/N/merge on a pull request
            repo_url=(("GITHUB_SERVER_URL", "GITHUB_REPOSITORY"),),
        ),
        merge_subject_res=MERGE_SUBJECT_RES,
        noreply_re=_NOREPLY_RE,
        noreply_fold=_NOREPLY_FOLD,
        bot_name_re=_BOT_NAME_RE,
        bot_email_re=_BOT_EMAIL_RE,
    )
)
