"""The ``issue-source`` seam: a plugin that links commits to tracker issues.

A tracker (Jira, Linear, GitHub or GitLab Issues) knows which commits fixed a
bug that git's own history cannot name: ``ENG-42: handle empty carts`` reads
as a feature to the fix rule, and the tracker says ENG-42 is a Bug. A plugin
registers a source from its ``register()``::

    from repowise.core.issues import issue_sources

    issue_sources.register("linear", LinearIssues())

The host (``repowise sources sync``, or a hosted API with its own tokens)
calls :func:`run_issue_fetch` with the commits of the defect window; the
source returns one :class:`IssueLink` per (commit, issue) it can name, and the
host stores them (:func:`~repowise.core.persistence.crud.issue_links.store_issue_links`).
Indexing and scoring read stored links only and never touch the network.

The contract:

* :class:`IssueRequest` carries the repository's identity, the commits to
  look at (sha, subject, body and the change number their message names),
  plain settings and an opaque credentials object, as ``evidence-source``
  does. Credentials are the host's: repowise passes the object through, never
  stores, logs or prints it.
* Key extraction is the source's own: tracker key formats differ.
  :func:`tracker_keys` (``ENG-42``) and :func:`number_refs` (``#42``) cover
  the common ones.
* Each :class:`IssueLink` names its commit by ``sha`` (one of the request's)
  or by ``change`` (a change number from the request, which the host resolves
  to every commit that names it), and the issue's key, type, status, title,
  url and a short basis (how the commit names the issue).
* Only ``bug`` and ``incident`` change anything: such a commit counts as a
  fix even when its message reads otherwise. A link never removes a fix the
  git rule found.
* Failure: raise :class:`SourceUnavailable` when the tracker is down or rate
  limits, :class:`SourceRefused` when it refuses the credentials or settings
  (the host shows the message as written). Anything else, including a link
  that breaks the rules above, fails the fetch. The links read before a
  failure are stored, but only a complete fetch drops the links it no
  longer returns.
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from repowise.core.evidence.sources import FetchOutcome, SourceRefused, SourceUnavailable
from repowise.core.registry.plugins import SeamRegistry

#: Every type a link may carry. Map the tracker's own names onto these.
ISSUE_TYPES = ("bug", "incident", "feature", "task", "other")
#: The types that make a commit a fix.
DEFECT_TYPES = frozenset({"bug", "incident"})

#: Column caps; longer text is cut, a longer key refused.
MAX_KEY = 64
MAX_STATUS = 64
MAX_TITLE = 200
MAX_BASIS = 200
MAX_URL = 2048


@dataclass(frozen=True)
class IssueCommit:
    """One commit the source is asked about."""

    sha: str
    subject: str
    body: str = ""
    #: The PR or MR number the message says this commit is, or None.
    change: int | None = None


@dataclass(frozen=True)
class IssueRequest:
    """What a source reads: one repository's commits."""

    repository_id: str
    repository_name: str = ""
    commits: Sequence[IssueCommit] = ()
    #: Plain settings (a project key, a workspace). Never secrets.
    settings: Mapping[str, Any] = field(default_factory=dict)
    #: The host's credentials object, passed through untouched and left out of ``repr``.
    credentials: Any = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class IssueLink:
    """One commit linked to one issue."""

    key: str
    #: One of :data:`ISSUE_TYPES`.
    type: str
    #: The commit's full sha, one of the request's; or leave it empty and set *change*.
    sha: str = ""
    change: int | None = None
    status: str = ""
    title: str = ""
    url: str = ""
    #: How the commit names the issue, quoted where the link is shown,
    #: e.g. ``"subject names ENG-42"`` or ``"PR #12 closes #40"``.
    basis: str = ""


@runtime_checkable
class IssueSource(Protocol):
    """A plugin that links commits to tracker issues."""

    #: The name it registers under, e.g. ``"linear"``.
    name: str

    def fetch(self, request: IssueRequest) -> Iterable[IssueLink]:
        """The links for *request*'s commits; a commit with no issue yields nothing."""
        ...


#: Source name -> source. The host decides which to run and with which credentials.
issue_sources: SeamRegistry[IssueSource] = SeamRegistry("issue-source")


# ---------------------------------------------------------------------------
# Key extraction helpers
# ---------------------------------------------------------------------------

# A project key of 2 to 10 capitals or digits, a dash, a number: ENG-42.
_TRACKER_KEY = re.compile(
    r"(?<![A-Za-z0-9_-])([A-Z][A-Z0-9_]{1,9})-([1-9][0-9]{0,8})(?![A-Za-z0-9-])"
)
# Standards and encodings that look like keys.
_NOT_KEYS = frozenset({"CVE", "GHSA", "ISO", "PEP", "RFC", "SHA", "UTF"})
_NUMBER_REF = re.compile(r"(?<![\w&/#])#([1-9][0-9]{0,8})(?![\w-])")


def tracker_keys(text: str, projects: Iterable[str] | None = None) -> list[str]:
    """``ENG-42``-shaped keys in *text*, first mention first, each once.

    Pass *projects* (``["ENG", "OPS"]``) to keep only those projects' keys; a
    tracker with known projects should, since ``UTF-8``-like words match too.
    """
    wanted = {p.upper() for p in projects} if projects is not None else None
    found: dict[str, None] = {}
    for m in _TRACKER_KEY.finditer(text or ""):
        project = m.group(1)
        if project in _NOT_KEYS or (wanted is not None and project not in wanted):
            continue
        found.setdefault(f"{project}-{m.group(2)}", None)
    return list(found)


def number_refs(text: str) -> list[int]:
    """``#42``-shaped issue numbers in *text*, first mention first, each once."""
    return list(dict.fromkeys(int(m.group(1)) for m in _NUMBER_REF.finditer(text or "")))


# ---------------------------------------------------------------------------
# Host side
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StoredLink:
    """A link after the host checked it: one commit sha, text within the column caps."""

    sha: str
    key: str
    type: str
    status: str
    title: str
    url: str
    basis: str


def resolve_links(links: Iterable[IssueLink], commits: Sequence[IssueCommit]) -> list[StoredLink]:
    """*links* checked against the contract, each change resolved to its commits.

    Raises ``ValueError`` naming the first link that breaks a rule. A change
    no requested commit names resolves to nothing.
    """
    known = {c.sha for c in commits}
    by_change: dict[int, list[str]] = {}
    for c in commits:
        if c.change is not None:
            by_change.setdefault(c.change, []).append(c.sha)
    out: list[StoredLink] = []
    for link in links:
        shas = _link_shas(link, known, by_change)
        kind = (link.type or "").lower()
        if kind not in ISSUE_TYPES:
            raise ValueError(f"link {link.key!r} has type {link.type!r}; use one of {ISSUE_TYPES}")
        key = (link.key or "").strip()
        if not key or len(key) > MAX_KEY:
            raise ValueError(f"an issue key must be 1 to {MAX_KEY} characters, got {link.key!r}")
        for sha in shas:
            out.append(
                StoredLink(
                    sha=sha,
                    key=key,
                    type=kind,
                    status=(link.status or "")[:MAX_STATUS],
                    title=(link.title or "").strip()[:MAX_TITLE],
                    url=(link.url or "")[:MAX_URL],
                    basis=(link.basis or "").strip()[:MAX_BASIS],
                )
            )
    return out


def _link_shas(link: IssueLink, known: set[str], by_change: dict[int, list[str]]) -> list[str]:
    if bool(link.sha) == (link.change is not None):
        raise ValueError(f"link {link.key!r} must name exactly one of sha and change")
    if link.sha:
        if link.sha not in known:
            raise ValueError(
                f"link {link.key!r} names commit {link.sha!r}, which was not asked about"
            )
        return [link.sha]
    return by_change.get(int(link.change), [])  # type: ignore[arg-type]


async def run_issue_fetch(
    source: IssueSource,
    request: IssueRequest,
    store: Callable[[list[StoredLink], bool], Awaitable[dict[str, Any]]],
) -> FetchOutcome:
    """Fetch from *source* and hand its links to *store*, under the failure contract.

    *store* takes the checked links and whether the fetch completed; only a
    complete fetch may drop stored links the source no longer returns (in
    OSS, :func:`~repowise.core.persistence.crud.issue_links.store_issue_links`).
    The outcome's ``cursor`` is always None: a request names its commits.

    Ceiling: ``fetch`` is a plain iterable, so a source that waits on the
    network holds this event loop, as ``evidence-source`` does.
    """
    outcome = FetchOutcome(status="ok", cursor=None)
    links: list[IssueLink] = []
    batches: Any = None
    try:
        batches = iter(source.fetch(request))
        for link in batches:
            links.append(link)
        resolved = resolve_links(links, request.commits)
    except Exception as exc:
        outcome.status, outcome.error = _failure(exc, request.credentials)
        # What was read before the failure still stands, minus a link that broke a rule.
        resolved = _resolvable(links, request.commits)
    finally:
        close = getattr(batches, "close", None)
        if close is not None:
            # Closing a generator must not mask what the fetch did.
            with contextlib.suppress(Exception):
                close()
    try:
        if resolved or outcome.status == "ok":
            outcome.stored.append(await store(resolved, outcome.status == "ok"))
    except Exception as exc:
        # A database error quotes the values it was bound to, so only its type goes out.
        outcome.status, outcome.error = "failed", f"storing the links failed ({type(exc).__name__})"
    return outcome


def _failure(exc: BaseException, credentials: Any) -> tuple[str, str]:
    if isinstance(exc, SourceUnavailable):
        return "unavailable", str(exc) or "the tracker is unavailable"
    if isinstance(exc, SourceRefused):
        return "refused", str(exc) or "the tracker refused the request"
    message = f"{type(exc).__name__}: {exc}"
    # An HTTP client's error can quote the request it sent, token included.
    if isinstance(credentials, str) and credentials and credentials in message:
        message = message.replace(credentials, "***")
    return "failed", message


def _resolvable(links: list[IssueLink], commits: Sequence[IssueCommit]) -> list[StoredLink]:
    out: list[StoredLink] = []
    for link in links:
        try:
            out.extend(resolve_links([link], commits))
        except ValueError:
            continue
    return out


__all__ = [
    "DEFECT_TYPES",
    "ISSUE_TYPES",
    "IssueCommit",
    "IssueLink",
    "IssueRequest",
    "IssueSource",
    "SourceRefused",
    "SourceUnavailable",
    "StoredLink",
    "issue_sources",
    "number_refs",
    "resolve_links",
    "run_issue_fetch",
    "tracker_keys",
]
