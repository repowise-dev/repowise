"""Publish a local repo on repowise.dev by asking it to index the GitHub remote.

Nothing local is uploaded: the hosted indexer clones the repo from GitHub, so
only what has been pushed is published. This module holds the whole decision
(which repo, which branch, what the platform answered, what to tell the user)
and returns it as a :class:`PublishResult`, so ``repowise publish`` and the
local web UI's publish button say exactly the same thing.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from repowise.cli.platform.links import site_link

#: Every link this flow prints or opens is credited to it.
SRC = "cli_publish"

#: The GitHub App that gives repowise.dev read access to private repos.
GITHUB_APP_INSTALL_URL = "https://github.com/apps/repowise-app/installations/new"

#: The hosted MCP endpoint for one repo.
MCP_URL = "https://api.repowise.dev/mcp/{owner}/{name}"

PUSHED_ONLY = (
    "Only what's pushed to GitHub is published. Local uncommitted or unpushed "
    "changes are not. Use --ref <branch> to publish another branch."
)
HOW_LONG = "A hosted index usually takes about 10 minutes, longer for big repos."

#: Paid tiers. Anyone else hitting a private-repo refusal needs a plan first.
_PAID_TIERS = {"pro", "teams", "admin"}

# https://github.com/o/n(.git), git@github.com:o/n(.git), ssh://git@github.com/o/n
_GITHUB_REMOTE = re.compile(
    r"^(?:https?://(?:[^@/]+@)?github\.com/|git@github\.com:|ssh://git@github\.com(?::\d+)?/)"
    r"(?P<owner>[A-Za-z0-9-]+)/(?P<name>[A-Za-z0-9._-]+?)(?:\.git)?/?$"
)


@dataclass
class PublishResult:
    """What happened, in words a person reads, plus the links to act on.

    ``outcome`` is one of ``published | already_published | needs_app |
    needs_plan | cap | too_big | rate_limited | not_github | signed_out |
    offline | error``.
    """

    outcome: str
    message: str
    #: The one link to act on next, if any.
    url: str | None = None
    #: Further plain lines, shown after the message.
    details: list[str] = field(default_factory=list)
    #: What the CLI opens in the browser (the indexing page on success).
    open_url: str | None = None
    #: ``owner/name`` once the remote was read.
    repo: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "message": self.message,
            "url": self.url,
            "details": list(self.details),
            "open_url": self.open_url,
            "repo": self.repo,
        }


def parse_github_remote(url: str) -> tuple[str, str] | None:
    """``(owner, name)`` for a GitHub remote URL, ``None`` for anything else."""
    match = _GITHUB_REMOTE.match(url.strip())
    if not match:
        return None
    return match.group("owner"), match.group("name")


def _git(repo_path: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def read_remote(repo_path: Path) -> tuple[str, str] | None:
    """The GitHub ``(owner, name)`` of ``origin``, or ``None``."""
    url = _git(repo_path, "remote", "get-url", "origin")
    return parse_github_remote(url) if url else None


def pushed_branch(repo_path: Path) -> str | None:
    """The branch on ``origin`` the current branch tracks, or ``None``.

    ``None`` (detached HEAD, a branch never pushed, or one tracking another
    remote) means "publish the default branch": a ref GitHub does not have
    would only be refused.
    """
    upstream = _git(repo_path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    if not upstream or not upstream.startswith("origin/"):
        return None
    return upstream.removeprefix("origin/") or None


def is_signed_in() -> bool:
    from repowise.cli.platform import auth

    try:
        return auth.get_valid_credentials() is not None
    except Exception:
        return False


def _detail_text(body: dict[str, Any]) -> str:
    detail = body.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("message") or "")
    return str(detail or "")


def _detail_code(body: dict[str, Any]) -> str | None:
    detail = body.get("detail")
    return detail.get("code") if isinstance(detail, dict) else None


def _account() -> dict[str, Any]:
    """``/auth/me`` for the refusals whose advice depends on the plan."""
    from repowise.cli.platform import auth

    try:
        return auth.fetch_account() or {}
    except Exception:
        return {}


def _trial_days(account: dict[str, Any]) -> int:
    if not account.get("trial_eligible"):
        return 0
    days = account.get("trial_days")
    return days if isinstance(days, int) and days > 0 else 0


def publish(repo_path: Path, *, ref: str | None = None, src: str = SRC) -> PublishResult:
    """Ask repowise.dev to index this repo's GitHub remote.

    The caller signs the user in first; signed out, this only says so.
    ``src`` tags every link in the result with the surface that published.
    Never raises.
    """
    remote = read_remote(repo_path)
    if remote is None:
        return PublishResult(
            outcome="not_github",
            message="Publish works with GitHub repos. Push this repo to GitHub first.",
        )
    owner, name = remote
    repo = f"{owner}/{name}"
    if not is_signed_in():
        return PublishResult(
            outcome="signed_out",
            message="Sign in to repowise.dev first: repowise login",
            repo=repo,
        )

    branch = ref or pushed_branch(repo_path)
    payload: dict[str, Any] = {"url": f"https://github.com/{repo}"}
    if branch:
        payload["ref"] = branch

    from repowise.cli.platform.client import default_client

    status, body = default_client.post_json("repos/index", payload, timeout=60.0)
    result = _interpret(status, body, owner=owner, name=name, src=src)
    result.repo = repo
    return result


def _interpret(
    status: int, body: dict[str, Any], *, owner: str, name: str, src: str
) -> PublishResult:
    repo = f"{owner}/{name}"
    repo_page = site_link(f"repo/{owner}/{name}", src)
    code = _detail_code(body)
    text = _detail_text(body)

    if status == 200 and body.get("short_id"):
        mcp = MCP_URL.format(owner=owner, name=name)
        connect = (
            f"Use it from Claude.ai or ChatGPT: add {mcp} as a connector. "
            f"How: {site_link('hosted', src, fragment='mcp')}"
        )
        if body.get("status") == "failed":
            return PublishResult(
                outcome="error",
                message="repowise.dev could not start the index. Try again in a few minutes.",
            )
        if body.get("status") == "ready":
            return PublishResult(
                outcome="already_published",
                message=f"{repo} is already on repowise.dev at this commit:",
                url=repo_page,
                details=[connect],
            )
        indexing = site_link(f"s/{body['short_id']}/indexing", src)
        return PublishResult(
            outcome="published",
            message="Your repo is being indexed:",
            url=indexing,
            open_url=indexing,
            details=[HOW_LONG, f"When it's ready: {repo_page}", connect],
        )

    if status == 0:
        return PublishResult(
            outcome="offline",
            message="Couldn't reach repowise.dev. Check your connection and try again.",
        )

    if status == 401:
        return PublishResult(
            outcome="signed_out",
            message="Your repowise.dev sign-in has expired. Run repowise login, then publish again.",
        )

    private = code in {
        "repo_not_found_or_private",
        "private_repo_needs_plan",
        "private_repo_needs_app",
    }
    if private:
        account = _account()
        if (account.get("tier") or "free") not in _PAID_TIERS:
            # The repo page runs the whole private-repo path on the site:
            # trial checkout, GitHub App install, then the index starts.
            days = _trial_days(account)
            offer = (
                f"Start a {days}-day free Pro trial (card required, cancel anytime). "
                "This page takes you through it, then GitHub access, then the index:"
                if days
                else "Upgrade to Pro on this page; it then takes you through GitHub access and the index:"
            )
            lead = (
                "GitHub can't see this repo without access, so it is private or the name is wrong."
                if code == "repo_not_found_or_private"
                else "This repo is private."
            )
            return PublishResult(
                outcome="needs_plan",
                message=f"{lead} Private repos need Pro. {offer}",
                url=repo_page,
            )
        lead = (
            "GitHub can't see this repo. If it is private, install"
            if code == "repo_not_found_or_private"
            else "This repo is private. Install"
        )
        return PublishResult(
            outcome="needs_app",
            message=f"{lead} the Repowise GitHub App on it, then publish again:",
            url=GITHUB_APP_INSTALL_URL,
        )

    if code == "repo_curated":
        return PublishResult(
            outcome="already_published",
            message=f"{repo} is kept up to date on repowise.dev by the Repowise team:",
            url=repo_page,
        )

    if status == 402:
        # The repo-count limit. Its detail is plain text naming the limit.
        account = _account()
        days = _trial_days(account)
        if (account.get("tier") or "free") == "free":
            if days:
                return PublishResult(
                    outcome="cap",
                    message=(
                        f"Free accounts index 2 repos. Start a {days}-day Pro trial for "
                        "5 repos and private ones (card required, cancel anytime):"
                    ),
                    url=site_link(
                        "pricing",
                        src,
                        params={"checkout": "pro", "interval": "monthly", "trial": "1"},
                    ),
                )
            return PublishResult(
                outcome="cap",
                message="Free accounts index 2 repos. Pro indexes 5, private ones too:",
                url=site_link("pricing", src),
            )
        return PublishResult(
            outcome="cap",
            message=text or "Your plan's repo limit is reached.",
            url=site_link("pricing", src),
        )

    if status == 413:
        return PublishResult(
            outcome="too_big",
            message=text or "This repo is too big for your plan.",
            details=["Free indexes repos up to 250 MB; Pro up to 5 GB."],
            url=site_link("pricing", src),
        )

    if status == 429:
        if "in flight" in text:
            return PublishResult(
                outcome="rate_limited",
                message=f"{text} Then publish again.",
            )
        return PublishResult(
            outcome="rate_limited",
            message="You can start 10 hosted indexes a day. Try again tomorrow.",
        )

    return PublishResult(
        outcome="error",
        message=f"repowise.dev refused the publish (HTTP {status}): {text or 'no details'}",
    )
