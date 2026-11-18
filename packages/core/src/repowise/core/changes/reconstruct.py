"""Merged changes read back from git: one per first-parent commit of HEAD.

The main line's first-parent chain is the sequence of things that landed. A
merge commit lands the commits its other parents bring in (``M^1..M^2``), with
the files it changed against its first parent; a squash or lone commit lands
itself. Rebase merges leave no grouping in git, so each rebased commit reads
as its own change.

A merge that names no change of its own (``Merge branch 'main' of …``, a
release branch brought back) often carries changes that do: squash commits
and PR merges made on the other side. Those come out as their own changes, and
the merge keeps only the commits nothing else claims; a merge left with none
is bookkeeping and is dropped.

Identity, first that applies: the PR or MR number the message names
(:func:`~repowise.core.forges.changes.change_number`), a Gerrit ``Change-Id``
trailer, the merge commit, the commit.

Git calls per run: the first-parent walk with ``--numstat``; when it met a
merge, one metadata walk over the commits merges brought in; and when a merge
was split, one ``--numstat`` read of the commits split out of it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from repowise.core.forges import ForgeKind, change_number, get_forge
from repowise.core.ingestion.git_indexer._constants import classify_commit_category

SHAPE_MERGE = "merge"
SHAPE_SQUASH = "squash"
SHAPE_COMMIT = "commit"

KIND_PR = "pr"
KIND_CHANGE_ID = "change_id"
KIND_MERGE = "merge"
KIND_COMMIT = "commit"

_ID_PREFIX = {KIND_PR: "pr", KIND_CHANGE_ID: "change", KIND_MERGE: "merge", KIND_COMMIT: "commit"}
_NAMED = (KIND_PR, KIND_CHANGE_ID)

_CHANGE_ID_RE = re.compile(r"^Change-Id:\s*(I[0-9a-f]{40})\s*$", re.MULTILINE)
#: Merge subjects that are boilerplate; the change's title is the body's first line.
_BOILERPLATE_MERGE = (
    "Merge pull request ",
    "Merge branch ",
    "Merge remote-tracking branch ",
    "Merged in ",
)
_AZURE_MERGED = re.compile(r"^Merged PR \d+:\s*(.+)$")
#: A git trailer's key has no digits, so an issue key (``FINERACT-2455: …``)
#: never reads as one.
_TRAILER_KEY = re.compile(r"^([A-Za-z][A-Za-z-]*):\s")
_KNOWN_TRAILERS = frozenset({"change-id", "cc", "reviewed-on"})
_GITLAB_MERGE_REF = "See merge request "
#: Shas per ``git log --no-walk`` call, well under Windows' command-line limit.
_SHA_BATCH = 200


@dataclass
class ReconstructedChange:
    change_id: str
    identity_kind: str
    identity_value: str
    shape: str
    forge: str
    forge_ref: str | None
    title: str
    merged_at: datetime | None
    head_sha: str
    author_name: str
    author_email: str
    #: Non-merge commits the change landed, newest first.
    commit_shas: list[str]
    #: ``(path, added, deleted)``, renames at their new path.
    files: list[tuple[str, int, int]]
    category: str
    #: The head commit's message body. Read by metadata sources, never stored.
    body: str = field(default="", repr=False)

    @property
    def lines_added(self) -> int:
        return sum(added for _, added, _ in self.files)

    @property
    def lines_deleted(self) -> int:
        return sum(deleted for _, _, deleted in self.files)


@dataclass(frozen=True)
class _Commit:
    sha: str
    parents: tuple[str, ...]
    author_name: str
    author_email: str
    ts: int
    subject: str
    body: str

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1


def _is_trailer(line: str, in_final_paragraph: bool) -> bool:
    if not (m := _TRAILER_KEY.match(line)):
        return False
    key = m.group(1).lower()
    return in_final_paragraph or key.endswith("-by") or key in _KNOWN_TRAILERS


def _body_title(body: str) -> str | None:
    """A merge body's first line that is neither a trailer nor git's own text."""
    lines = [line.strip() for line in body.strip().splitlines()]
    # Git keeps trailers in the final paragraph; a body of one paragraph is
    # the title (``fix: x``), save a known trailer such as ``Signed-off-by``.
    final = next((i for i in range(len(lines) - 1, 0, -1) if not lines[i - 1] and lines[i]), 0)
    for i, line in enumerate(lines):
        if line in ("Conflicts:", "# Conflicts:"):
            return None  # git's conflict list, kept when a merge commit is not edited
        if line and not line.startswith(_GITLAB_MERGE_REF) and not _is_trailer(line, 0 < final <= i):
            return line
    return None


def change_title(subject: str, body: str, is_merge: bool) -> str:
    """The change's title: a merge's own boilerplate subject gives way to its body."""
    if not is_merge:
        return subject
    if m := _AZURE_MERGED.match(subject):
        return m.group(1).strip()
    if subject.startswith(_BOILERPLATE_MERGE):
        return _body_title(body) or subject
    return subject


#: A squash title's trailing ``(#N)`` (GitHub) or ``(!N)`` (GitLab).
_OWN_REF_SUFFIX = re.compile(r"\s*\((?:#|!)(\d+)\)\s*$")


def without_own_ref(title: str, number: str) -> str:
    """*title* less a trailing ``(#N)``/``(!N)`` naming its own change *number*.

    Surfaces print the ref beside the title, so the forge's suffix repeats it.
    A suffix naming another change stays.
    """
    m = _OWN_REF_SUFFIX.search(title)
    if m and m.group(1) == number and m.start() > 0:
        return title[: m.start()]
    return title


def change_identity(
    subject: str, body: str, sha: str, is_merge: bool, forge: ForgeKind | str
) -> tuple[str, str, str | None]:
    """``(kind, value, forge_ref)`` in the order PR number, Change-Id, merge, commit."""
    number = change_number(subject, body, forge)
    if number is not None:
        return KIND_PR, str(number), get_forge(forge).format_change_ref(number)
    ids = _CHANGE_ID_RE.findall(body)
    if ids:
        return KIND_CHANGE_ID, ids[-1], None
    return (KIND_MERGE if is_merge else KIND_COMMIT), sha, None


def _folded_files(numstat_lines: list[str]) -> list[tuple[str, int, int]]:
    from repowise.core.ingestion.git_commit_index import _parse_numstat

    changes, _moved = _parse_numstat(numstat_lines)
    folded: dict[str, tuple[int, int]] = {}
    for path, added, deleted in changes:
        if path:
            a, d = folded.get(path, (0, 0))
            folded[path] = (a + added, d + deleted)
    return [(path, a, d) for path, (a, d) in folded.items()]


def _merged_files(*file_lists: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
    return _folded_files([f"{a}\t{d}\t{p}" for files in file_lists for p, a, d in files])


def _first_parent_records(repo: Any, rev: str, limit: int) -> list[tuple[dict, list[str]]]:
    from repowise.core.ingestion.git_indexer.records import (
        _LOG_FORMAT,
        _RECORD_SEP,
        _parse_commit_record,
    )

    # ``-m`` with ``--first-parent`` diffs a merge against its first parent
    # only, on old and new git alike.
    raw = repo.git.log(
        f"-{limit}", "--first-parent", "-m", "-M", "--numstat", f"--format={_LOG_FORMAT}", rev
    )
    out = []
    for record in raw.split(_RECORD_SEP):
        if record.strip() and (parsed := _parse_commit_record(record)) is not None:
            out.append(parsed)
    return out


def _graph(repo: Any, head: str, exclude: list[str]) -> dict[str, _Commit]:
    """Every commit reachable from *head* and from none of *exclude*."""
    raw = repo.git.log(
        "--format=%x00%H%x1f%P%x1f%aN%x1f%aE%x1f%ct%x1f%s%x1f%b",
        head,
        *(f"^{sha}" for sha in exclude),
    )
    graph: dict[str, _Commit] = {}
    for record in raw.split("\x00"):
        parts = record.split("\x1f")
        if len(parts) < 7:
            continue
        sha, parents, name, email, ts, subject = parts[:6]
        try:
            stamp = int(ts)
        except ValueError:
            stamp = 0
        sha = sha.strip()
        graph[sha] = _Commit(
            sha, tuple(parents.split()), name, email, stamp, subject, "\x1f".join(parts[6:]).strip()
        )
    return graph


def _numstats(repo: Any, shas: list[str]) -> dict[str, list[tuple[str, int, int]]]:
    """Each commit's files against its first parent."""
    out: dict[str, list[tuple[str, int, int]]] = {}
    for i in range(0, len(shas), _SHA_BATCH):
        raw = repo.git.log(
            "--no-walk=unsorted",
            "-m",
            "--first-parent",
            "-M",
            "--numstat",
            "--format=%x00%H",
            *shas[i : i + _SHA_BATCH],
        )
        for record in raw.split("\x00"):
            lines = record.strip().splitlines()
            if lines:
                out[lines[0].strip()] = _folded_files([ln for ln in lines[1:] if ln.strip()])
    return out


def _is_ancestor(repo: Any, sha: str, head: str) -> bool:
    try:
        repo.git.merge_base("--is-ancestor", sha, head)
    except Exception:
        return False
    return True


def reconstruct_changes(
    repo: Any,
    forge: ForgeKind | str,
    *,
    limit: int,
    since_sha: str | None = None,
    taken: Mapping[str, str] | None = None,
) -> list[ReconstructedChange] | None:
    """Changes landed on HEAD's first-parent line, newest first.

    *limit* bounds the first-parent commits read. With *since_sha* only
    changes after it are read; ``None`` is returned when it is no longer an
    ancestor of HEAD (a rebase or branch switch), and the caller rebuilds.
    *taken* maps stored change ids to their head sha, so a repeated identity
    (a PR number cherry-picked twice) gets a distinct id while the change that
    holds it keeps its own across runs.
    """
    try:
        head = repo.head.commit.hexsha
    except Exception:
        return []
    if since_sha:
        if since_sha == head:
            return []
        if not _is_ancestor(repo, since_sha, head):
            return None
    rev = f"{since_sha}..{head}" if since_sha else head
    records = _first_parent_records(repo, rev, limit)
    if not records:
        return []

    graph: dict[str, _Commit] = {}
    if any(header["is_merge"] for header, _ in records):
        oldest_parents = records[-1][0]["parents"]
        graph = _graph(repo, head, [*oldest_parents[:1], *([since_sha] if since_sha else [])])

    builder = _Builder(repo, forge, graph, taken)
    builder.claimed.update(header["sha"] for header, _ in records)
    # Oldest first: a branch merged twice belongs to the first merge, and the
    # first holder of a repeated identity keeps the plain id.
    for header, numstat_lines in reversed(records):
        builder.landed(header, _folded_files(numstat_lines))
    builder.out.sort(key=lambda c: (c.merged_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    return builder.out


class _Builder:
    def __init__(
        self,
        repo: Any,
        forge: ForgeKind | str,
        graph: Mapping[str, _Commit],
        taken: Mapping[str, str] | None,
    ) -> None:
        self.repo = repo
        self.forge = forge
        self.graph = graph
        self.ids = dict(taken or {})
        self.claimed: set[str] = set()
        self.out: list[ReconstructedChange] = []

    def _members(self, starts: tuple[str, ...], within: set[str] | None = None) -> list[str]:
        """Commits reachable from *starts* that nothing has landed yet."""
        found: list[str] = []
        stack = list(starts)
        while stack:
            sha = stack.pop()
            if sha in self.claimed or sha not in self.graph:
                continue
            if within is not None and sha not in within:
                continue
            self.claimed.add(sha)
            found.append(sha)
            stack.extend(self.graph[sha].parents)
        return found

    def _own(self, shas: list[str]) -> list[str]:
        own = [s for s in shas if not self.graph[s].is_merge]
        own.sort(key=lambda s: self.graph[s].ts, reverse=True)
        return own

    def landed(self, header: dict, files: list[tuple[str, int, int]]) -> None:
        """Record the change one first-parent commit landed."""
        commit = _Commit(
            header["sha"],
            tuple(header["parents"]),
            header["author_name"],
            header["author_email"],
            header["ts"],
            header["subject"],
            header["body"],
        )
        kind, value, ref = change_identity(
            commit.subject, commit.body, commit.sha, commit.is_merge, self.forge
        )
        if not commit.is_merge:
            self._emit(commit, kind, value, ref, [commit.sha], files)
            return
        members = self._members(commit.parents[1:])
        if kind in _NAMED:
            self._emit(commit, kind, value, ref, self._own(members), files)
            return
        self._split(commit, kind, value, ref, members, files)

    def _split(
        self,
        merge: _Commit,
        kind: str,
        value: str,
        ref: str | None,
        members: list[str],
        files: list[tuple[str, int, int]],
    ) -> None:
        """An unnamed merge: changes it carried come out on their own."""
        pool = set(members)
        named = sorted(
            (s for s in members if self._identity(self.graph[s])[0] in _NAMED),
            # Merges first, so a commit inside a named branch stays with it.
            key=lambda s: (not self.graph[s].is_merge, self.graph[s].ts),
        )
        if not named:
            self._emit(merge, kind, value, ref, self._own(members), files)
            return
        self.claimed.difference_update(pool)
        groups: list[tuple[_Commit, list[str]]] = []
        for sha in named:
            if sha in self.claimed:
                continue
            self.claimed.add(sha)
            node = self.graph[sha]
            inner = self._members(node.parents[1:], pool) if node.is_merge else []
            groups.append((node, inner))
        rest = self._own([s for s in members if s not in self.claimed])
        self.claimed.update(pool)
        stats = _numstats(self.repo, [node.sha for node, _ in groups] + rest)
        for node, inner in groups:
            k, v, r = self._identity(node)
            commits = self._own(inner) if node.is_merge else [node.sha]
            self._emit(node, k, v, r, commits, stats.get(node.sha, []))
        if rest:
            self._emit(merge, kind, value, ref, rest, _merged_files(*(stats.get(s, []) for s in rest)))

    def _identity(self, commit: _Commit) -> tuple[str, str, str | None]:
        return change_identity(commit.subject, commit.body, commit.sha, commit.is_merge, self.forge)

    def _emit(
        self,
        head: _Commit,
        kind: str,
        value: str,
        ref: str | None,
        commits: list[str],
        files: list[tuple[str, int, int]],
    ) -> None:
        change_id = f"{_ID_PREFIX[kind]}-{value}"
        if self.ids.get(change_id, head.sha) != head.sha:
            change_id = f"{change_id}-{head.sha[:12]}"
        self.ids[change_id] = head.sha
        author_name, author_email = head.author_name, head.author_email
        if head.is_merge and commits:
            # The oldest commit's author opened the branch; the merge's author
            # only pressed the button.
            oldest = self.graph[commits[-1]]
            author_name, author_email = oldest.author_name, oldest.author_email
        if head.is_merge:
            shape = SHAPE_MERGE
        elif kind == KIND_PR:
            shape = SHAPE_SQUASH
        else:
            shape = SHAPE_COMMIT
        title = change_title(head.subject, head.body, head.is_merge)
        if kind == KIND_PR:
            title = without_own_ref(title, value)
        self.out.append(
            ReconstructedChange(
                change_id=change_id,
                identity_kind=kind,
                identity_value=value,
                shape=shape,
                forge=str(ForgeKind(self.forge)),
                forge_ref=ref,
                title=title,
                merged_at=datetime.fromtimestamp(head.ts, tz=UTC) if head.ts > 0 else None,
                head_sha=head.sha,
                author_name=author_name,
                author_email=author_email,
                commit_shas=commits,
                files=files,
                category=classify_commit_category(title),
                body=head.body,
            )
        )


__all__ = [
    "KIND_CHANGE_ID",
    "KIND_COMMIT",
    "KIND_MERGE",
    "KIND_PR",
    "SHAPE_COMMIT",
    "SHAPE_MERGE",
    "SHAPE_SQUASH",
    "ReconstructedChange",
    "change_identity",
    "change_title",
    "reconstruct_changes",
]
