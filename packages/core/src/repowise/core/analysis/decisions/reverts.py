"""Revert-based supersession: retire a decision whose commits were reverted.

A decision mined from a commit is shown as current for as long as nothing says
otherwise, including when a later commit reverted it. Git records the reversal
itself, so this reads it from history rather than from similarity (the
semantic detector in :mod:`.evolution` stays off; see the note there).

A revert is matched to its target by three rules, strongest first, and a
commit whose message fits a stronger rule is never retried with a weaker one:

- **body**: the message carries ``This reverts commit <40-hex sha>`` (what
  ``git revert`` writes).
- **subject**: the subject is ``Revert "<S>"`` and exactly one other commit in
  history has subject ``S``, both compared with a trailing ``(#N)`` or
  ``(gh-N)`` stripped.
- **pr**: the subject is ``revert:``/``revert(scope):`` and the message
  references ``#N`` or ``/pull/N``, with exactly one commit that is that pull
  request (subject ending ``(#N)`` or ``Merge pull request #N``).

Every match also needs the target to be an ancestor of the revert, and the two
must change at least one file in common. A revert that calls itself partial is
not matched at all.

A revert can itself be reverted, so a commit counts as reverted at HEAD only
when at least one of its reverts is not (parity along the chain). Even then it
is not reported when its change came back another way (the decision holds):
a later commit carries its subject again (ignoring ``reland``/``reapply``
markers, a ticket prefix and word endings); a later commit closes, in subject
or body, an issue its subject names (``Fixes #N``); a later commit under its
issue key edits its files; or the files it added are all present at HEAD and
the ones it deleted all absent.

A decision is retired only when *every* evidence commit it rests on is reverted
at HEAD and it has no evidence that is not a commit (an ADR file, an inline
marker). Anything partial or ambiguous is left as it was.

Ceiling: uniqueness for the subject and pr rules is judged over the history the
clone holds, so a shallow clone can make a duplicate subject look unique. The
target must still be an ancestor of the revert, which bounds the damage to
commits inside the clone's window.
"""

from __future__ import annotations

import bisect
import json
import re
import subprocess
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import structlog

from repowise.core.analysis.git_cli import _git

logger = structlog.get_logger(__name__)

__all__ = [
    "REVERT_SUPERSEDED_PREFIX",
    "RevertLink",
    "apply_revert_supersession",
    "find_revert_links",
    "reverted_at_head",
]

#: ``superseded_by`` for a record retired here: ``revert:<sha prefix>``. It is
#: not a decision id, so no lineage walk or alias lookup resolves it, and
#: ``unretire_auto_superseded`` (which keys on ``auto-detected:`` edges) never
#: touches it. The column is 32 characters, so the sha is cut to fit.
REVERT_SUPERSEDED_PREFIX = "revert:"
_SUPERSEDED_BY_WIDTH = 32

_BODY_RE = re.compile(r"This reverts commit ([0-9a-f]{40})\b")
_SUBJECT_RE = re.compile(r'^Revert "(.+)"$')
_CONVENTIONAL_RE = re.compile(r"^revert(?:\([^)]*\))?!?:", re.IGNORECASE)
_PR_REF_RE = re.compile(r"(?:#|/pull/)(\d+)\b")
_PR_SUFFIX_RE = re.compile(r"\s*\((?:#|gh-)(\d+)\)\s*$", re.IGNORECASE)
_MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+)\b")
_PARTIAL_RE = re.compile(r"\bpart(?:ial(?:ly)?|ly)\b", re.IGNORECASE)
_RELAND_WORD_RE = re.compile(r"\s*\(?\b(?:re-?land(?:ed)?|re-?appl(?:y|ied))\b\)?:?", re.IGNORECASE)
_TICKET_PREFIX_RE = re.compile(
    r"^(?:(?:fix(?:e[sd])?|refs?|close[sd]?)\s+#?\d+[,\s]*)+(?:--|:|-)\s*", re.IGNORECASE
)
#: An issue key in a subject: ``#123``, ``gh-123``, ``ABC-123``.
_ISSUE_KEY_RE = re.compile(r"(?:(?<![\w/])#|\b[A-Za-z]{2,10}-)\d+\b")
_CLOSES_RE = re.compile(r"\b(?:fix(?:e[sd])?|close[sd]?|resolve[sd]?)\s+#(\d+)\b", re.IGNORECASE)

_WORD_ENDING_RE = re.compile(r"(?<=\w{3})(?:ed|es|s|d)$")
_CHANGES_BATCH = 500
_FIELD = "\x1f"
_RECORD = "\x1e"


@dataclass(frozen=True)
class RevertLink:
    """``revert`` undoes ``target``; ``rule`` is body | subject | pr.

    ``relanded``: the target's change came back after the revert by some other
    commit. The link still counts for parity along a revert chain, but never
    reports its target as reverted.
    """

    revert: str
    target: str
    rule: str
    relanded: bool = False


def _git_out(repo_path: Path | str, *args: str) -> str | None:
    """Stdout of a git command, or ``None`` when it fails (never raises)."""
    try:
        return _git(list(args), str(repo_path))
    except (OSError, subprocess.SubprocessError):
        return None


def _strip_pr_suffix(subject: str) -> str:
    while True:
        stripped = _PR_SUFFIX_RE.sub("", subject)
        if stripped == subject:
            return subject.strip()
        subject = stripped


def _reland_key(subject: str) -> str:
    """A subject reduced to what a re-land of the same change would share.

    Drops "reland"/"reapply" markers, a leading ticket prefix ("Fixed #N --"),
    final punctuation and word endings, so "Use X for Y" and a later
    "Fixed #9 -- Used X for Y." compare equal. Looser only ever means more
    matches count as re-landed, which leaves a decision as it was.
    """
    text = _RELAND_WORD_RE.sub(" ", _strip_pr_suffix(subject)).replace("`", "")
    text = _TICKET_PREFIX_RE.sub("", text.strip()).rstrip(" .").lower()
    return " ".join(_WORD_ENDING_RE.sub("", w) for w in text.split())


def _issue_keys(subject: str) -> set[str]:
    """Issue keys a subject names, leaving out its trailing pull request number."""
    return {k.lower() for k in _ISSUE_KEY_RE.findall(_strip_pr_suffix(subject))}


def _changes(repo_path: Path | str, shas: set[str]) -> dict[str, dict[str, str]]:
    """Path -> status letter (A, M, D, ...) per commit, against its first parent."""
    out: dict[str, dict[str, str]] = {}
    ordered = sorted(shas)
    # Chunked to keep the command line short (Windows caps it near 32K).
    for i in range(0, len(ordered), _CHANGES_BATCH):
        text = _git_out(
            repo_path,
            "log",
            "--no-walk=unsorted",
            "-m",
            "--first-parent",
            "--name-status",
            "--no-renames",
            f"--format={_RECORD}%H",
            *ordered[i : i + _CHANGES_BATCH],
        )
        for block in (text or "").split(_RECORD):
            sha, _, rest = block.partition("\n")
            rows = (line.split("\t", 1) for line in rest.splitlines())
            if sha.strip():
                out[sha.strip()] = {r[1]: r[0][:1] for r in rows if len(r) == 2}
    return out


def _pr_number(subject: str) -> str | None:
    m = _PR_SUFFIX_RE.search(subject) or _MERGE_PR_RE.match(subject)
    return m.group(1) if m else None


def _read_candidates(repo_path: Path | str, head: str) -> list[tuple[str, str, str]]:
    """(sha, subject, body) of every commit whose message could fit a rule."""
    out = _git_out(
        repo_path,
        "log",
        head,
        "-i",
        "-E",
        "--grep=^revert",
        "--grep=This reverts commit [0-9a-f]{40}",
        f"--format=%H{_FIELD}%s{_FIELD}%b{_RECORD}",
    )
    candidates = []
    for raw in (out or "").split(_RECORD):
        parts = raw.strip("\n").split(_FIELD)
        if len(parts) == 3 and parts[0]:
            candidates.append((parts[0].strip(), parts[1], parts[2]))
    return candidates


def _append(index: dict[str, list[str]], key: str, sha: str) -> None:
    index.setdefault(key, []).append(sha)


@dataclass
class _History:
    """*head*'s history, indexed for matching reverts and spotting re-lands.

    Commits are read in topological order, which lists every commit before its
    parents, so a commit listed ahead of another is never its ancestor.
    """

    position: dict[str, int] = field(default_factory=dict)
    parents: dict[str, list[str]] = field(default_factory=dict)
    subject_of: dict[str, str] = field(default_factory=dict)
    by_subject: dict[str, list[str]] = field(default_factory=dict)
    by_pr: dict[str, list[str]] = field(default_factory=dict)
    by_reland_key: dict[str, list[str]] = field(default_factory=dict)
    by_closed_issue: dict[str, list[str]] = field(default_factory=dict)
    by_issue_key: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def read(cls, repo_path: Path | str, head: str) -> _History | None:
        out = _git_out(
            repo_path,
            "log",
            "--topo-order",
            head,
            f"--format=%H{_FIELD}%P{_FIELD}%s{_FIELD}%b{_RECORD}",
        )
        if out is None:
            return None
        hist = cls()
        for raw in out.split(_RECORD):
            fields = raw.strip("\n").split(_FIELD, 3)
            if len(fields) == 4 and fields[0]:
                hist._add(*fields)
        return hist

    def _add(self, sha: str, parent_list: str, subject: str, body: str) -> None:
        self.position[sha] = len(self.position)
        self.parents[sha] = parent_list.split()
        self.subject_of[sha] = subject
        _append(self.by_subject, _strip_pr_suffix(subject), sha)
        _append(self.by_reland_key, _reland_key(subject), sha)
        # The body too: a later fix often closes the issue there ("Also fixes #N").
        for issue in set(_CLOSES_RE.findall(f"{subject}\n{body}")):
            _append(self.by_closed_issue, issue, sha)
        for key in _issue_keys(subject):
            _append(self.by_issue_key, key, sha)
        pr = _pr_number(subject)
        if pr:
            _append(self.by_pr, pr, sha)

    def is_ancestor(self, older: str, newer: str) -> bool:
        # Walk *newer*'s parents, never past *older*'s place in the order: an
        # ancestor of *older* is listed after it, so nothing there leads back.
        limit = self.position[older]
        stack, seen = [newer], {newer}
        while stack:
            sha = stack.pop()
            if sha == older:
                return True
            for parent in self.parents.get(sha, ()):
                if parent not in seen and self.position.get(parent, limit + 1) <= limit:
                    seen.add(parent)
                    stack.append(parent)
        return False

    def match(self, sha: str, subject: str, body: str) -> list[RevertLink]:
        """The links one candidate message makes, by the strongest rule it fits."""
        message = f"{subject}\n{body}"
        if _PARTIAL_RE.search(message):
            # "Partially revert X": X's decision may well still hold.
            return []
        cited = _BODY_RE.findall(message)
        if cited:
            return [RevertLink(sha, t, "body") for t in dict.fromkeys(cited) if t != sha]
        m = _SUBJECT_RE.match(_strip_pr_suffix(subject))
        if m:
            others = [s for s in self.by_subject.get(_strip_pr_suffix(m.group(1)), []) if s != sha]
            return [RevertLink(sha, others[0], "subject")] if len(others) == 1 else []
        if _CONVENTIONAL_RE.match(subject):
            refs = set(_PR_REF_RE.findall(message))
            targets = {s for n in refs for s in self.by_pr.get(n, []) if s != sha}
            return [RevertLink(sha, targets.pop(), "pr")] if len(targets) == 1 else []
        return []


@dataclass
class _Relands:
    """Whether a reverted target's change came back after its revert."""

    hist: _History
    revert_shas: set[str]
    head_paths: set[str] = field(default_factory=set)
    changes_of: dict[str, dict[str, str]] = field(default_factory=dict)

    def after(self, link: RevertLink, sha: str) -> bool:
        # Not an ancestor of the revert: a descendant, or a parallel branch
        # merged later. Both count, which only ever keeps a decision. Only
        # commits that matched as reverts are left out; a message that merely
        # mentions one ("reverted in <sha>, re-implemented here") is a re-land.
        return (
            sha != link.target
            and sha not in self.revert_shas
            and self.hist.position[sha] < self.hist.position[link.revert]
        )

    def same_issue_later(self, link: RevertLink) -> set[str]:
        keys = _issue_keys(self.hist.subject_of[link.target])
        return {s for k in keys for s in self.hist.by_issue_key.get(k, []) if self.after(link, s)}

    def files(self, sha: str) -> set[str]:
        return set(self.changes_of.get(sha, {}))

    def in_force_at_head(self, target: str) -> bool:
        # The files the target added are all there and the ones it deleted are
        # all gone: whatever undid it, a later change put its effect back.
        changes = self.changes_of.get(target, {})
        added = {p for p, st in changes.items() if st == "A"}
        deleted = {p for p, st in changes.items() if st == "D"}
        return bool(added or deleted) and added <= self.head_paths and not deleted & self.head_paths

    def relanded(self, link: RevertLink) -> bool:
        # The same subject again, a commit closing an issue the target named, a
        # commit under the target's issue key that edits its files (a reworked
        # second attempt), or its added and deleted files standing as it left
        # them.
        subject = self.hist.subject_of[link.target]
        later = set(self.hist.by_reland_key.get(_reland_key(subject), []))
        for issue in _PR_REF_RE.findall(subject):
            later.update(self.hist.by_closed_issue.get(issue, []))
        if self.in_force_at_head(link.target) or any(self.after(link, s) for s in later):
            return True
        target_files = self.files(link.target)
        return any(self.files(s) & target_files for s in self.same_issue_later(link))


def find_revert_links(repo_path: Path | str, head: str = "HEAD") -> list[RevertLink]:
    """Every revert in *head*'s history matched to its target, ancestors only."""
    # Only messages that can fit a rule; the full history is read only if one
    # of them does.
    candidates = _read_candidates(repo_path, head)
    hist = _History.read(repo_path, head) if candidates else None
    if hist is None:
        return []
    proposed = [link for c in candidates for link in hist.match(*c)]
    relands = _Relands(hist, {link.revert for link in proposed})
    proposed = [link for link in proposed if link.target in hist.subject_of]
    # Every diff the checks read, in one batch rather than one git per commit.
    relands.changes_of = _changes(
        repo_path,
        {
            sha
            for link in proposed
            for sha in (link.target, link.revert, *relands.same_issue_later(link))
        },
    )
    relands.head_paths = set(
        (_git_out(repo_path, "ls-tree", "-r", "--name-only", head) or "").split("\n")
    )
    return [
        replace(link, relanded=relands.relanded(link))
        for link in proposed
        # A squash commit can carry a branch's "This reverts commit" lines
        # without its diff undoing any of them; a revert touches its target.
        if relands.files(link.target) & relands.files(link.revert)
        and hist.is_ancestor(link.target, link.revert)
    ]


def reverted_at_head(links: list[RevertLink]) -> dict[str, RevertLink]:
    """Commits whose change is undone at HEAD, each with a revert still in force.

    A commit is reverted when one of its reverts is not itself reverted; a
    revert of a revert restores the original, a third revert undoes it again.
    Every link counts for that, but a commit whose change was re-landed is
    not reported.
    """
    reverts_of: dict[str, list[RevertLink]] = {}
    for link in links:
        reverts_of.setdefault(link.target, []).append(link)

    memo: dict[str, RevertLink | None] = {}

    def effective(sha: str, seen: frozenset[str]) -> RevertLink | None:
        if sha in memo:
            return memo[sha]
        found = None
        for link in reverts_of.get(sha, []):
            # A cycle cannot happen through ancestry; guard anyway.
            if link.revert in seen:
                continue
            if effective(link.revert, seen | {sha}) is None:
                found = link
                break
        memo[sha] = found
        return found

    out: dict[str, RevertLink] = {}
    for sha in reverts_of:
        link = effective(sha, frozenset())
        if link is not None and not link.relanded:
            out[sha] = link
    return out


def _resolve(sha: str, full_shas: list[str]) -> str | None:
    """A stored sha (possibly abbreviated) as the unique full sha it names."""
    sha = sha.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{7,40}", sha):
        return None
    i = bisect.bisect_left(full_shas, sha)
    if i >= len(full_shas) or not full_shas[i].startswith(sha):
        return None
    if i + 1 < len(full_shas) and full_shas[i + 1].startswith(sha):
        return None
    return full_shas[i]


async def _live_records(session: Any, repository_id: str) -> list[Any]:
    """Records this pass may retire, plus those it retired before."""
    from sqlalchemy import or_, select

    from repowise.core.analysis.decisions.lifecycle import RETIRED_STATUSES
    from repowise.core.persistence.models import DecisionRecord

    query = select(DecisionRecord).where(
        DecisionRecord.repository_id == repository_id,
        or_(
            DecisionRecord.status.not_in(tuple(RETIRED_STATUSES)),
            DecisionRecord.superseded_by.startswith(REVERT_SUPERSEDED_PREFIX),
        ),
    )
    return list((await session.execute(query)).scalars().all())


async def _commit_evidence(session: Any, records: list[Any]) -> dict[str, set[str] | None]:
    """Each record's evidence commits; ``None`` when it also rests on other evidence.

    Other evidence is an evidence row without a commit or a file named on the
    record itself (an ADR, an inline marker): the decision is stated there, not
    only in a commit.
    """
    from sqlalchemy import select

    from repowise.core.persistence.models import DecisionEvidence

    out: dict[str, set[str] | None] = {}
    for rec in records:
        try:
            commits = json.loads(rec.evidence_commits_json or "[]")
        except ValueError:
            commits = []
        out[rec.id] = (
            None if rec.evidence_file else {c for c in commits if isinstance(c, str) and c}
        )
    if not records:
        return out
    query = select(DecisionEvidence.decision_id, DecisionEvidence.evidence_commit).where(
        DecisionEvidence.decision_id.in_([r.id for r in records])
    )
    for decision_id, commit in (await session.execute(query)).all():
        found = out.get(decision_id)
        if found is not None and commit:
            found.add(commit)
        elif not commit:
            out[decision_id] = None
    return out


async def apply_revert_supersession(
    session: Any, repository_id: str, repo_path: Path | str | None
) -> dict[str, int]:
    """Retire decisions whose evidence commits are all reverted at HEAD.

    Also restores a record this pass retired earlier once its commits are no
    longer all reverted (a later revert of the revert). Idempotent.
    """
    from repowise.core.analysis.decisions.evolution import _retire

    result = {"superseded": 0, "restored": 0}
    if not repo_path or not Path(repo_path).exists():
        return result
    records = await _live_records(session, repository_id)
    evidence = await _commit_evidence(session, records)
    if not any(evidence.values()):
        return result

    reverted = reverted_at_head(find_revert_links(repo_path))
    full_shas = sorted((_git_out(repo_path, "rev-list", "HEAD") or "").split())

    for rec in records:
        commits = evidence.get(rec.id)
        resolved = sorted(_resolve(c, full_shas) or "" for c in commits or ())
        all_reverted = bool(resolved) and all(c in reverted for c in resolved)
        retired_here = (rec.superseded_by or "").startswith(REVERT_SUPERSEDED_PREFIX)
        if all_reverted and not retired_here:
            revert_sha = reverted[resolved[0]].revert
            successor = f"{REVERT_SUPERSEDED_PREFIX}{revert_sha}"[:_SUPERSEDED_BY_WIDTH]
            await _retire(session, rec, successor_id=successor)
            result["superseded"] += 1
        elif retired_here and not all_reverted and rec.status == "superseded":
            # The revert was itself reverted: the decision holds again.
            # ``proposed``, as ``unretire_auto_superseded`` restores: the lower
            # claim, and one ``decision confirm`` away from active.
            rec.status = "proposed"
            rec.superseded_by = None
            result["restored"] += 1

    if any(result.values()):
        await session.flush()
        logger.info("decisions.revert_supersession", **result)
    return result
