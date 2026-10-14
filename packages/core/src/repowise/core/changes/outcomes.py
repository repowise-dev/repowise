"""What happened to a merged change afterwards, from links a reader can check.

Two kinds of link, each quoting the message that makes it:

- ``revert``: a later commit reverts the change, matched by the rules in
  :mod:`repowise.core.analysis.decisions.reverts` (partial reverts never match);
- ``fix_reference``: a later commit names the change as the cause of what it
  fixes, in its own words: a ``Fixes: <sha>`` trailer, ``regression
  from/in/since``, ``caused by``, ``broken by/in/since``.

Each phrase was hand-checked on 14 repositories before it was kept. Looser
forms read well and named the wrong thing, so they are not read: ``Fixes #N``
(mostly an issue number, or a typo for one), ``introduced in #N`` (as often a
feature a later change builds on), and a fix commit quoting a sha (mostly a
revert or a squashed branch log).

A reference resolves only to a change it names unambiguously (a number on a
forge where issues and changes share one sequence, ``!N`` on GitLab, a sha
that is unique in the history), and only from a commit the change is an
ancestor of. Links to another repository's changes (URLs) are not read.

Beside the links, :func:`file_fix_rate` measures how many of the change's
files later received fix commits within N days: a rate with its basis, never
a claim that the change caused them. No link is inferred from blame (SZZ was
measured below its gate and not shipped; docs/architecture/bug-history.md).

Link kinds are providers on ``SeamRegistry("change-outcome")``. Every
registered provider runs, the local git ones first; a forge-backed provider
(a PR's linked follow-ups) registers another under its own name.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol

from repowise.core.registry.plugins import SeamRegistry, load_plugins

#: The window :func:`file_fix_rate` counts in, unless ``changes.outcome_fix_days`` says.
DEFAULT_FIX_DAYS = 30
_MAX_FIX_DAYS = 365
_FRAGMENT_WIDTH = 200

REVERT = "revert"
FIX_REFERENCE = "fix_reference"

#: Forges that number issues and changes in one sequence, so ``#N`` names at
#: most one of them. GitLab (``!N``), Azure and Bitbucket number them apart.
_SHARED_NUMBERING = frozenset({"github", "generic"})

_FIELD = "\x1f"
_RECORD = "\x1e"

# ``#N``, GitLab's ``!N``, or a sha of 7 to 40 hex digits.
_REF = r"(#\d{1,9}|!\d{1,9}|\b[0-9a-f]{7,40}\b)"
# Words may wrap onto the next line, never across a blank one: a phrase that
# ends one paragraph says nothing about the reference opening the next.
_S = r"(?:[ \t]+|[ \t]*\n[ \t]*)"
_NOUN = rf"(?:(?:commit|change|pr|pull request|merge request|mr){_S})?"
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # The kernel convention: ``Fixes: <sha> ("subject")`` names the commit that
    # introduced the bug.
    ("fixes_trailer", re.compile(r"^Fixes:[ \t]+([0-9a-f]{7,40})\b", re.I | re.M)),
    (
        "regression",
        re.compile(
            rf"\bregress(?:ion|ed)?{_S}(?:introduced{_S})?(?:from|in|since|by){_S}{_NOUN}{_REF}",
            re.I,
        ),
    ),
    ("caused_by", re.compile(rf"\bcaused{_S}by{_S}{_NOUN}{_REF}", re.I)),
    ("broken_by", re.compile(rf"\b(?:broken|broke){_S}(?:by|in|since){_S}{_NOUN}{_REF}", re.I)),
)


@dataclass(frozen=True, slots=True)
class OutcomeTarget:
    """The change outcomes are read for."""

    change_id: str
    head_sha: str
    commit_shas: tuple[str, ...]
    identity_kind: str
    identity_value: str
    forge: str
    forge_ref: str | None
    merged_at: datetime | None
    files: tuple[str, ...]

    @property
    def revert_targets(self) -> set[str]:
        """Commits whose revert undoes the whole change, not one commit of many."""
        shas = {self.head_sha}
        if len(self.commit_shas) == 1:
            shas.add(self.commit_shas[0])
        return shas

    @property
    def own(self) -> set[str]:
        return {self.head_sha, *self.commit_shas}


@dataclass(frozen=True, slots=True)
class OutcomeLink:
    """A later commit that names the change, and the words it names it in."""

    kind: str
    #: The later commit.
    sha: str
    #: The change's commit it names.
    target_sha: str
    #: The message fragment that makes the link, verbatim.
    evidence: str
    #: Which rule matched (``body``/``subject``/``pr`` for a revert, a phrase for a reference).
    rule: str
    basis: str = "declared"
    #: Revert only: the revert still holds at HEAD (not itself reverted, the change not re-landed).
    in_force: bool | None = None
    #: The stored change the later commit landed in, filled by the caller.
    change_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        out = {
            "kind": self.kind,
            "sha": self.sha,
            "change_id": self.change_id,
            "target_sha": self.target_sha,
            "rule": self.rule,
            "evidence": self.evidence,
            "basis": self.basis,
        }
        if self.kind == REVERT:
            out["in_force"] = self.in_force
        return out


class ChangeOutcomeProvider(Protocol):
    def links(self, repo_path: Path, head: str, change: OutcomeTarget) -> Sequence[OutcomeLink]:
        """Links to *change* from history up to *head*; none found is ``[]``."""
        ...


def _git_out(repo_path: Path | str, *args: str) -> str:
    from repowise.core.analysis.decisions.reverts import _git_out as read

    return read(repo_path, *args) or ""


def _fragment(text: str, start: int, end: int) -> str:
    """The line holding ``text[start:end]``, trimmed to a readable width."""
    lo = text.rfind("\n", 0, start) + 1
    hi = text.find("\n", end)
    line = text[lo : hi if hi >= 0 else len(text)].strip()
    return line if len(line) <= _FRAGMENT_WIDTH else line[: _FRAGMENT_WIDTH - 1] + "…"


# -- reverts -------------------------------------------------------------------


@lru_cache(maxsize=4)
def _revert_index(
    repo_path: str, head: str
) -> tuple[dict[str, list[Any]], frozenset[str], dict[str, str]]:
    """``(links by target, targets reverted at HEAD, revert subjects)``, once per HEAD."""
    from repowise.core.analysis.decisions.reverts import find_revert_links, reverted_at_head

    links = find_revert_links(repo_path, head)
    by_target: dict[str, list[Any]] = {}
    for link in links:
        by_target.setdefault(link.target, []).append(link)
    subjects: dict[str, str] = {}
    if links:
        raw = _git_out(
            repo_path,
            "log",
            "--no-walk=unsorted",
            f"--format=%H{_FIELD}%s",
            *dict.fromkeys(link.revert for link in links),
        )
        for line in raw.splitlines():
            sha, _, subject = line.partition(_FIELD)
            subjects[sha.strip()] = subject
    return by_target, frozenset(reverted_at_head(links)), subjects


class GitRevertOutcomes:
    """Reverts that name the change, by the decision-supersession rules."""

    def links(self, repo_path: Path, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
        by_target, in_force, subjects = _revert_index(str(repo_path), head)
        out = []
        for target in sorted(change.revert_targets):
            for link in by_target.get(target, ()):
                said = (
                    f"This reverts commit {target}"
                    if link.rule == "body"
                    else subjects.get(link.revert, "")
                )
                out.append(
                    OutcomeLink(
                        REVERT,
                        link.revert,
                        target,
                        said,
                        link.rule,
                        in_force=target in in_force,
                    )
                )
        return out


# -- references ----------------------------------------------------------------


@dataclass
class _References:
    """Every cause phrase in *head*'s history, keyed by what it names."""

    hist: Any
    shas: list[str] = field(default_factory=list)
    #: ``"#12"`` / ``"!12"`` / a sha token -> ``[(commit, rule, fragment)]``.
    by_token: dict[str, list[tuple[str, str, str]]] = field(default_factory=dict)


@lru_cache(maxsize=4)
def _reference_index(repo_path: str, head: str) -> _References | None:
    from repowise.core.analysis.decisions.reverts import _History
    from repowise.core.forges import detect_forge

    raw = _git_out(
        repo_path,
        "log",
        "--topo-order",
        head,
        f"--format=%H{_FIELD}%P{_FIELD}%s{_FIELD}%b{_RECORD}",
    )
    if not raw:
        return None
    hist = _History(forge=detect_forge(repo_path))
    refs = _References(hist)
    for record in raw.split(_RECORD):
        fields = record.strip("\n").split(_FIELD, 3)
        if len(fields) != 4 or not fields[0]:
            continue
        sha, parents, subject, body = fields
        hist._add(sha, parents, subject, body)
        message = f"{subject}\n\n{body}"  # git always parts them with a blank line
        named: set[str] = set()
        for rule, pattern in _RULES:
            for m in pattern.finditer(message):
                token = m.group(1).lower()
                if token not in named:
                    named.add(token)
                    frag = _fragment(message, m.start(), m.end())
                    refs.by_token.setdefault(token, []).append((sha, rule, frag))
    refs.shas = sorted(hist.position)
    return refs


class GitReferenceOutcomes:
    """Later commits whose message names the change as what they fix."""

    def links(self, repo_path: Path, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
        from repowise.core.analysis.decisions.reverts import _resolve

        refs = _reference_index(str(repo_path), head)
        if refs is None or change.head_sha not in refs.hist.position:
            return []
        hits: list[tuple[str, str, str, str]] = []  # (commit, target, rule, fragment)
        number = change.identity_value if change.identity_kind == "pr" else None
        # A number held by two commits is ambiguous: neither is named.
        if number is not None and len(refs.hist.by_pr.get(number, ())) == 1:
            tokens = []
            if change.forge in _SHARED_NUMBERING:
                tokens.append(f"#{number}")
            if change.forge == "gitlab":
                tokens.append(f"!{number}")
            for token in tokens:
                hits += [(c, change.head_sha, r, f) for c, r, f in refs.by_token.get(token, ())]
        own = change.own
        for token, rows in refs.by_token.items():
            if token[0] in "#!" or not any(s.startswith(token) for s in own):
                continue
            full = _resolve(token, refs.shas)  # unique in the history, or nothing
            if full in own:
                hits += [(c, full, r, f) for c, r, f in rows]
        out: dict[str, OutcomeLink] = {}
        for commit, target, rule, frag in hits:
            if commit in own or commit in out or not refs.hist.is_ancestor(target, commit):
                continue
            out[commit] = OutcomeLink(FIX_REFERENCE, commit, target, frag, rule)
        return sorted(out.values(), key=lambda link: refs.hist.position[link.sha], reverse=True)


change_outcome_providers: SeamRegistry[ChangeOutcomeProvider] = SeamRegistry("change-outcome")
change_outcome_providers.register("git-revert", GitRevertOutcomes())
change_outcome_providers.register("git-reference", GitReferenceOutcomes())


def outcome_links(repo_path: Path | str, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
    """Links from every registered provider, in registration order."""
    out: list[OutcomeLink] = []
    seen: set[tuple[str, str]] = set()
    load_plugins()
    for name in change_outcome_providers.names:
        for link in change_outcome_providers.get(name).links(Path(repo_path), head, change):
            if (link.kind, link.sha) not in seen:
                seen.add((link.kind, link.sha))
                out.append(link)
    return out


# -- file fix rate -------------------------------------------------------------


def fix_days(repo_path: Path | str) -> int:
    """``changes.outcome_fix_days`` from the repo config, else the default."""
    from repowise.core.repo_config import load_repo_config

    try:
        raw = (load_repo_config(repo_path).get("changes") or {}).get("outcome_fix_days")
        days = int(raw) if raw is not None else DEFAULT_FIX_DAYS
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_FIX_DAYS
    return days if 1 <= days <= _MAX_FIX_DAYS else DEFAULT_FIX_DAYS


def file_fix_rate(
    repo_path: Path | str, head: str, change: OutcomeTarget, days: int
) -> dict[str, Any] | None:
    """How many of the change's files a later fix commit touched within *days*.

    A fix is a commit the fix category holds (``is_fix_commit``, the rule every
    fix count uses) that descends from the change. Paths are matched as the
    change left them; a later rename is not followed. ``None`` when the change
    has no merge time or no files to count, or the history cannot be read.

    Ceiling: one date-bounded ``git log --name-only`` per change, about half
    a second on a mid-size repository; an export of thousands pays it each.
    """
    from repowise.core.ingestion.git_indexer._constants import is_fix_commit

    refs = _reference_index(str(repo_path), head)
    if change.merged_at is None or not change.files or refs is None:
        return None
    if change.head_sha not in refs.hist.position:
        return None
    start = int(change.merged_at.timestamp())
    end = int((change.merged_at + timedelta(days=days)).timestamp())
    # Bounded by date, then kept to the change's descendants. Not
    # ``--ancestry-path`` with ``--since``: the date cut stops the walk before
    # it reaches the change, and nothing qualifies.
    raw = _git_out(
        repo_path,
        "log",
        "--no-merges",
        f"--since=@{start}",
        f"--until=@{end}",
        "--name-only",
        f"--format={_RECORD}%H{_FIELD}%ct{_FIELD}%s",
        head,
    )
    files = set(change.files)
    fixed: dict[str, list[str]] = {}
    commits: set[str] = set()
    for record in raw.split(_RECORD):
        header, _, names = record.strip("\n").partition("\n")
        parts = header.split(_FIELD)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        sha, ts, subject = parts
        if not start < int(ts) <= end or not is_fix_commit(subject):
            continue
        touched = files.intersection(names.splitlines())
        if not touched or sha == change.head_sha:
            continue
        if not refs.hist.is_ancestor(change.head_sha, sha):
            continue
        commits.add(sha)
        for path in touched:
            fixed.setdefault(path, []).append(sha)
    head_ts = _git_out(repo_path, "log", "-1", "--format=%ct", head).strip()
    return {
        "files": len(files),
        "files_fixed": len(fixed),
        "days": days,
        "fix_commits": len(commits),
        "window_complete": head_ts.isdigit() and int(head_ts) >= end,
        "fixed": [{"path": p, "fixes": shas} for p, shas in sorted(fixed.items())],
    }


def target_of(row: Any) -> OutcomeTarget:
    """The :class:`OutcomeTarget` for a stored ``changes`` row."""
    import json

    def _load(raw: str | None) -> list[Any]:
        try:
            return list(json.loads(raw or "[]"))
        except (TypeError, ValueError):
            return []

    merged = row.merged_at
    if merged is not None and merged.tzinfo is None:  # SQLite drops tzinfo; the column is UTC
        from datetime import UTC

        merged = merged.replace(tzinfo=UTC)
    return OutcomeTarget(
        change_id=row.change_id,
        head_sha=row.head_sha,
        commit_shas=tuple(_load(row.commit_shas_json)),
        identity_kind=row.identity_kind,
        identity_value=row.identity_value,
        forge=row.forge,
        forge_ref=row.forge_ref,
        merged_at=merged,
        files=tuple(
            f["path"] for f in _load(row.files_json) if isinstance(f, dict) and f.get("path")
        ),
    )


__all__ = [
    "DEFAULT_FIX_DAYS",
    "FIX_REFERENCE",
    "REVERT",
    "ChangeOutcomeProvider",
    "GitReferenceOutcomes",
    "GitRevertOutcomes",
    "OutcomeLink",
    "OutcomeTarget",
    "change_outcome_providers",
    "file_fix_rate",
    "fix_days",
    "outcome_links",
    "target_of",
]
