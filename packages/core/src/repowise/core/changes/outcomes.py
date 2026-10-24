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
import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
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
        """Links to *change* from history up to *head*; none found is ``[]``.

        Raise :class:`OutcomeUnknownError` when the source cannot be read, so the
        item reads unknown instead of none.
        """
        ...


class OutcomeUnknownError(Exception):
    """What could not be read, said as a reason; the item reads unknown, never none."""


def _git_out(repo_path: Path | str, *args: str) -> str:
    """Git's stdout; a failure or timeout raises :class:`OutcomeUnknownError`."""
    from repowise.core.analysis.decisions.reverts import _git_out as read

    out = read(repo_path, *args)
    if out is None:
        raise OutcomeUnknownError(f"git could not answer `git {args[0]}` for this repository")
    return out


def _fragment(text: str, start: int, end: int) -> str:
    """The line holding ``text[start:end]``, trimmed to a readable width."""
    lo = text.rfind("\n", 0, start) + 1
    hi = text.find("\n", end)
    line = text[lo : hi if hi >= 0 else len(text)].strip()
    return line if len(line) <= _FRAGMENT_WIDTH else line[: _FRAGMENT_WIDTH - 1] + "…"


# One build per key at a time, shared by every thread that asks; a failed
# build raises to each caller and is not kept. The last few HEADs stay.
_INDEX_KEEP = 4
_index_lock = threading.Lock()
_index: dict[tuple[str, ...], Any] = {}
_index_locks: dict[tuple[str, ...], threading.Lock] = {}


def _once(key: tuple[str, ...], build: Any) -> Any:
    with _index_lock:
        lock = _index_locks.setdefault(key, threading.Lock())
    with lock:
        with _index_lock:
            if key in _index:
                return _index[key]
        value = build()
        with _index_lock:
            _index[key] = value
            while len(_index) > _INDEX_KEEP:
                old = next(iter(_index))
                _index.pop(old)
                _index_locks.pop(old, None)
        return value


def _forge(name: str) -> Any:
    from repowise.core.forges import ForgeKind

    try:
        return ForgeKind(name)
    except ValueError:
        return ForgeKind.GENERIC


# -- reverts -------------------------------------------------------------------


def _revert_index(
    repo_path: str, head: str, forge: str
) -> tuple[dict[str, list[Any]], frozenset[str], dict[str, str]]:
    """``(links by target, targets reverted at HEAD, revert subjects)``, once per HEAD."""

    def build() -> tuple[dict[str, list[Any]], frozenset[str], dict[str, str]]:
        from repowise.core.analysis.decisions.reverts import (
            HistoryUnreadableError,
            find_revert_links,
            reverted_at_head,
        )

        try:
            links = find_revert_links(repo_path, head, forge=_forge(forge), strict=True)
        except HistoryUnreadableError as exc:
            raise OutcomeUnknownError(str(exc)) from exc
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

    return _once(("revert", repo_path, head, forge), build)


class GitRevertOutcomes:
    """Reverts that name the change, by the decision-supersession rules."""

    def links(self, repo_path: Path, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
        by_target, in_force, subjects = _revert_index(str(repo_path), head, change.forge)
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

    def holder(self, number: str) -> str | None:
        """The one commit that *is* change *number*, leaving out reverts and re-lands.

        ``Revert "x (#5)" (#6)`` and ``Reland x (#5)`` carry the number without
        being that change; two other holders make it ambiguous.
        """
        from repowise.core.analysis.decisions.reverts import (
            _CONVENTIONAL_RE,
            _RELAND_WORD_RE,
            _SUBJECT_RE,
        )

        held = [
            sha
            for sha in self.hist.by_pr.get(number, ())
            if not (
                _SUBJECT_RE.match(subject := self.hist.subject_of.get(sha, ""))
                or _CONVENTIONAL_RE.match(subject)
                or _RELAND_WORD_RE.search(subject)
            )
        ]
        return held[0] if len(held) == 1 else None


def _reference_index(repo_path: str, head: str, forge: str) -> _References:
    def build() -> _References:
        from repowise.core.analysis.decisions.reverts import _History

        raw = _git_out(
            repo_path,
            "log",
            "--topo-order",
            head,
            f"--format=%H{_FIELD}%P{_FIELD}%s{_FIELD}%b{_RECORD}",
        )
        hist = _History(forge=_forge(forge))
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

    return _once(("reference", repo_path, head, forge), build)


_Hit = tuple[str, str, str, str]  # (later commit, target sha, rule, fragment)


def _number_hits(refs: _References, change: OutcomeTarget) -> list[_Hit]:
    """References by the change's number, when its own commit is the one holding it."""
    number = change.identity_value if change.identity_kind == "pr" else None
    if number is None or refs.holder(number) not in change.own:
        return []
    tokens = []
    if change.forge in _SHARED_NUMBERING:
        tokens.append(f"#{number}")
    if change.forge == "gitlab":
        tokens.append(f"!{number}")
    # Ceiling: Azure Repos also writes ``!N`` for a pull request; it is not
    # read until it is measured there.
    return [
        (c, change.head_sha, r, f) for token in tokens for c, r, f in refs.by_token.get(token, ())
    ]


def _sha_hits(refs: _References, change: OutcomeTarget) -> list[_Hit]:
    """References by a sha that is unique in the history and one of the change's own."""
    from repowise.core.analysis.decisions.reverts import _resolve

    own = change.own
    hits: list[_Hit] = []
    for token, rows in refs.by_token.items():
        if token[0] in "#!" or not any(s.startswith(token) for s in own):
            continue
        full = _resolve(token, refs.shas)  # unique in the history, or nothing
        if full in own:
            hits += [(c, full, r, f) for c, r, f in rows]
    return hits


class GitReferenceOutcomes:
    """Later commits whose message names the change as what they fix."""

    def links(self, repo_path: Path, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
        refs = _reference_index(str(repo_path), head, change.forge)
        if change.head_sha not in refs.hist.position:
            raise OutcomeUnknownError("the change is not in HEAD's history")
        own = change.own
        out: dict[str, OutcomeLink] = {}
        for commit, target, rule, frag in _number_hits(refs, change) + _sha_hits(refs, change):
            if commit in own or commit in out or not refs.hist.is_ancestor(target, commit):
                continue
            out[commit] = OutcomeLink(FIX_REFERENCE, commit, target, frag, rule)
        return sorted(out.values(), key=lambda link: refs.hist.position[link.sha], reverse=True)


change_outcome_providers: SeamRegistry[ChangeOutcomeProvider] = SeamRegistry("change-outcome")
change_outcome_providers.register("git-revert", GitRevertOutcomes())
change_outcome_providers.register("git-reference", GitReferenceOutcomes())


def outcome_links(repo_path: Path | str, head: str, change: OutcomeTarget) -> list[OutcomeLink]:
    """Links from every registered provider, in registration order.

    A provider that cannot read raises :class:`OutcomeUnknownError`, and so does this.
    """
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
    """``changes.outcome_fix_days`` from the repo config (1 to 365), else the default."""
    from repowise.core.repo_config import load_repo_config

    try:
        raw = (load_repo_config(repo_path).get("changes") or {}).get("outcome_fix_days")
        days = int(raw) if raw is not None else DEFAULT_FIX_DAYS
    except (TypeError, ValueError, AttributeError):
        return DEFAULT_FIX_DAYS
    return days if 1 <= days <= _MAX_FIX_DAYS else DEFAULT_FIX_DAYS


#: Paths per ``git log -- <paths>`` call, well under Windows' command-line limit.
_PATH_BATCH = 100


def _fix_commits_touching(
    repo_path: Path | str,
    head: str,
    files: list[str],
    start: int,
    end: int,
) -> list[tuple[str, set[str]]]:
    """``(sha, paths)`` of fix commits with ``start < ts <= end`` touching *files*."""
    from repowise.core.ingestion.git_indexer._constants import is_fix_commit

    found: list[tuple[str, set[str]]] = []
    for i in range(0, len(files), _PATH_BATCH):
        batch = files[i : i + _PATH_BATCH]
        # Bounded by date; the caller keeps the change's descendants. Not
        # ``--ancestry-path`` with ``--since``: the date cut stops the walk
        # before it reaches the change, and nothing qualifies.
        raw = _git_out(
            repo_path,
            "--literal-pathspecs",
            "log",
            "--no-merges",
            f"--since=@{start}",
            f"--until=@{end}",
            "--name-only",
            f"--format={_RECORD}%H{_FIELD}%ct{_FIELD}%s",
            head,
            "--",
            *batch,
        )
        wanted = set(batch)
        for record in raw.split(_RECORD):
            header, _, names = record.strip("\n").partition("\n")
            parts = header.split(_FIELD)
            if len(parts) != 3 or not parts[1].isdigit():
                continue
            sha, ts, subject = parts
            if start < int(ts) <= end and is_fix_commit(subject):
                touched = wanted.intersection(names.splitlines())
                if touched:
                    found.append((sha, touched))
    return found


def file_fix_rate(
    repo_path: Path | str, head: str, change: OutcomeTarget, days: int
) -> dict[str, Any]:
    """How many of the change's files a later fix commit touched within *days*.

    A fix is a commit the fix category holds (``is_fix_commit``, the rule every
    fix count uses) that descends from the change. Paths are matched as the
    change left them; a later rename is not followed. Raises
    :class:`OutcomeUnknownError` when there is nothing to count or git cannot answer.

    Ceiling: one date-bounded ``git log`` per 100 of the change's files; an
    export of thousands of changes pays it per change.
    """
    if change.merged_at is None:
        raise OutcomeUnknownError("the change has no merge time to count a window from")
    if not change.files:
        raise OutcomeUnknownError("the change lists no files")
    refs = _reference_index(str(repo_path), head, change.forge)
    if change.head_sha not in refs.hist.position:
        raise OutcomeUnknownError("the change is not in HEAD's history")
    start = int(change.merged_at.timestamp())
    end = int((change.merged_at + timedelta(days=days)).timestamp())
    files = sorted(set(change.files))
    fixed: dict[str, list[str]] = {}
    commits: set[str] = set()
    for sha, touched in _fix_commits_touching(repo_path, head, files, start, end):
        if sha == change.head_sha or not refs.hist.is_ancestor(change.head_sha, sha):
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
    "OutcomeUnknownError",
    "change_outcome_providers",
    "file_fix_rate",
    "fix_days",
    "outcome_links",
    "target_of",
]
