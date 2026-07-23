"""Bin a repository's production files into bounded, path-local groups.

One page per directory gives ``analysis/dead_code`` a page whether it earns one
and ``core/generation`` exactly one whether it needs three. One page per LLM
whim gives a different answer every run. This gives a third thing: a partition
of the directory tree into subtrees of bounded size, computed from the tree
itself.

The rule is a single bottom-up pass. A directory whose whole subtree fits under
the ceiling becomes one group and stops recursing, which is what keeps a page
about a place rather than about an arbitrary slice of one. A directory too big
to fit descends, and the pieces its children could not fill on their own are
accumulated in path order and flushed when they would overflow. Path locality
therefore falls out of the traversal rather than being asked for.

Three properties this has to hold, all of them load-bearing elsewhere:

* **Deterministic.** Children are visited in sorted order and every tie breaks
  on a path string, so the same file set always produces the same groups. Page
  identity hashes the member list (D2), so a grouping that drifted between runs
  would mint new page ids on every index and strand the old rows.
* **Total.** Every non-test production file lands in exactly one group. The
  validator asserts this rather than trusting it, but it is true by
  construction: the recursion partitions the tree and never drops a branch.
* **Local.** A group is a subtree or a run of adjacent siblings, never a
  scattering. That is what lets a group keep a real directory in
  ``target_path``, which the bench gold set matches on.

The layer signal steers which adjacent runs merge; it does not force a split.
A subtree small enough to fit stays one group even when it spans two layers,
because recursion stops as soon as a subtree fits. Splitting on layer wherever
layers disagree would fragment the outline and would inherit the layer data's
own noise, and an occasionally impure small page is the better trade.

The size ceiling comes from a coarse ladder keyed on repository size rather
than from a division, because a ceiling computed as ``total / target`` moves
whenever any file is added and would reshuffle the whole tree on a one-line
commit. The ladder only moves at a band edge.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from ..models import member_structural_key

#: Prefix for a concept group's structural key. Distinct from ``module`` and
#: ``scc`` so a key can never be mistaken for another page type's.
STRUCTURAL_KEY_PREFIX = "concept"


@dataclass(frozen=True)
class GroupingParams:
    """Size bounds for the partition.

    ``max_files`` is the ceiling a single group may reach; ``min_files`` is the
    size below which a group prefers to be absorbed by a neighbour rather than
    stand alone. ``min_files`` is advisory — a repository can contain a single
    isolated directory smaller than it — while ``max_files`` is enforced except
    for one unavoidable case: a single directory holding more files than the
    ceiling cannot be split any further by a path-based rule, and splitting it
    on some other axis would break locality. Those are reported, not hidden.
    """

    min_files: int = 10
    max_files: int = 30


# Repository size bands and the ceiling each one uses. Chosen so a repository
# of a few hundred files lands near 50 pages and one of a few thousand lands
# near 80, rather than growing without bound. Bands are wide on purpose: the
# ceiling must not move when a commit adds a file, because every group's
# identity would move with it.
_SIZE_LADDER: tuple[tuple[int, GroupingParams], ...] = (
    (600, GroupingParams(min_files=6, max_files=14)),
    (1200, GroupingParams(min_files=8, max_files=22)),
    (2400, GroupingParams(min_files=10, max_files=28)),
    (4800, GroupingParams(min_files=14, max_files=52)),
    (10_000, GroupingParams(min_files=20, max_files=95)),
)

_LADDER_TOP = GroupingParams(min_files=32, max_files=160)


def params_for(file_count: int) -> GroupingParams:
    """The size bounds a repository of *file_count* production files uses."""
    for ceiling, params in _SIZE_LADDER:
        if file_count <= ceiling:
            return params
    return _LADDER_TOP


@dataclass
class ConceptGroup:
    """The files one concept page will cover.

    ``target_path`` is a real directory and stays one. It is the page id
    (``compute_page_id`` is ``"{type}:{target_path}"``) and it is what the
    bench gold set matches by exact equality, so a group that merged several
    directories still has to name one of them. ``structural_key`` is where the
    abstract identity lives — a hash of the members, which is the only thing
    that actually says which page this is.
    """

    members: list[str]
    #: Directories fully contained in this group, sorted. A group is usually a
    #: subtree, so this is normally that subtree's directories.
    dirs: list[str]
    target_path: str
    dominant_layer: str = ""
    #: Set when the group is a single directory larger than the ceiling and
    #: therefore could not be split by any path-based rule.
    oversized: bool = False
    structural_key: str = field(default="", repr=False)

    def __post_init__(self) -> None:
        if not self.structural_key:
            self.structural_key = member_structural_key(
                self.members, prefix=STRUCTURAL_KEY_PREFIX
            )

    @property
    def file_count(self) -> int:
        return len(self.members)


# ---------------------------------------------------------------------------
# Directory tree
# ---------------------------------------------------------------------------


@dataclass
class _Node:
    path: str
    own: list[str] = field(default_factory=list)
    children: dict[str, _Node] = field(default_factory=dict)
    subtree: int = 0

    def child(self, name: str) -> _Node:
        node = self.children.get(name)
        if node is None:
            node = _Node(path=f"{self.path}/{name}" if self.path else name)
            self.children[name] = node
        return node


def _build_tree(paths: Iterable[str]) -> _Node:
    root = _Node(path="")
    for path in paths:
        parts = path.split("/")
        node = root
        for part in parts[:-1]:
            node = node.child(part)
        node.own.append(path)
    _count(root)
    return root


def _count(node: _Node) -> int:
    node.subtree = len(node.own) + sum(_count(c) for c in node.children.values())
    return node.subtree


def _subtree_files(node: _Node) -> list[str]:
    out = list(node.own)
    for _name, child in sorted(node.children.items()):
        out.extend(_subtree_files(child))
    return out


def _dirs_of(paths: Sequence[str]) -> list[str]:
    return sorted({p.rsplit("/", 1)[0] if "/" in p else "" for p in paths})


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


def _dominant(paths: Sequence[str], layer_of_file: dict[str, str]) -> str:
    """The layer most of *paths* belong to, ties broken on the layer id.

    Matches ``page_tree._dominant_layer`` deliberately: a group placed under a
    layer in the tree and a group whose merge decision consulted a different
    notion of "its layer" would disagree about where it belongs.
    """
    counts: dict[str, int] = {}
    for path in paths:
        layer = layer_of_file.get(path)
        if layer:
            counts[layer] = counts.get(layer, 0) + 1
    if not counts:
        return ""
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _target_path(members: Sequence[str], dirs: Sequence[str]) -> str:
    """The directory that names this group.

    The shallowest directory containing every member, which for a subtree group
    is that subtree's root and for a run of siblings is their shared parent.
    Derived from the members rather than picked from a list, so it does not
    depend on which directory happened to sort first.

    **This value is the page id** (``compute_page_id`` is
    ``"{page_type}:{target_path}"``), so two groups sharing one would be two
    pages sharing a row. The partition splits a directory's own files from its
    subdirectories' files, so a "files directly in ``routers``" group and a
    "everything under ``routers/``" group both compute ``routers`` here.
    :func:`_assign_targets` resolves that; this function is the unqualified
    answer.
    """
    if not members:
        return dirs[0] if dirs else ""
    split = [m.split("/")[:-1] for m in members]
    common = split[0]
    for parts in split[1:]:
        limit = min(len(common), len(parts))
        cut = limit
        for i in range(limit):
            if common[i] != parts[i]:
                cut = i
                break
        common = common[:cut]
    return "/".join(common)


class _Partitioner:
    def __init__(self, params: GroupingParams, layer_of_file: dict[str, str]):
        self.params = params
        self.layer_of_file = layer_of_file
        self.groups: list[ConceptGroup] = []

    def make(self, members: Sequence[str]) -> ConceptGroup:
        ordered = sorted(members)
        dirs = _dirs_of(ordered)
        # A group over the ceiling whose files all sit directly in one
        # directory has hit the floor of what a path-based rule can do: there
        # is no sub-path to split on. Chunking such a directory by filename
        # order would split it, but the chunk boundaries would move whenever a
        # file was added, and every chunk's identity hashes its members — so a
        # one-file commit would remint every page in the directory. Stability
        # is a correctness requirement here and page size is a quality target,
        # so the group stays whole and says that it is oversized.
        oversized = len(ordered) > self.params.max_files and len(dirs) == 1
        return ConceptGroup(
            members=ordered,
            dirs=dirs,
            target_path=_target_path(ordered, dirs),
            dominant_layer=_dominant(ordered, self.layer_of_file),
            oversized=oversized,
        )

    def _same_layer(self, left: Sequence[str], right: Sequence[str]) -> bool:
        """Whether two pending runs may be merged across a layer boundary.

        A merge that straddles two architectural layers produces a page about
        no single thing, which is the failure the layer hint exists to prevent.
        The exception is a run too thin to stand alone: a three-file group with
        a title is worse documentation than a slightly impure eight-file one.
        """
        if len(left) + len(right) < self.params.min_files:
            return True
        a = _dominant(left, self.layer_of_file)
        b = _dominant(right, self.layer_of_file)
        return not a or not b or a == b

    def visit(self, node: _Node) -> list[str]:
        """Emit groups for *node*'s subtree; return the files left for its parent.

        The returned files are ones that did not amount to a group on their
        own. The parent either merges them with an adjacent sibling's leftovers
        or, at the top, flushes them as their own group.
        """
        if node.subtree <= self.params.max_files:
            return _subtree_files(node)

        pending: list[str] = list(node.own)
        for _name, child in sorted(node.children.items()):
            left = self.visit(child)
            if not left:
                continue
            if len(pending) + len(left) <= self.params.max_files and self._same_layer(
                pending, left
            ):
                pending.extend(left)
                continue
            if pending:
                self.groups.append(self.make(pending))
            # A child only returns files when its whole subtree fits under the
            # ceiling, so this can never itself overflow. An over-ceiling
            # single directory is emitted by the recursion below, not here.
            pending = list(left)

        # A thin remainder is folded back into the group before it rather than
        # left as a two-file page, but only when they share a layer and the
        # result still fits.
        if pending and len(pending) < self.params.min_files and self.groups:
            last = self.groups[-1]
            if len(last.members) + len(pending) <= self.params.max_files and self._same_layer(
                last.members, pending
            ):
                merged = self.make(last.members + pending)
                self.groups[-1] = merged
                pending = []
        if pending:
            self.groups.append(self.make(pending))
        return []


def _absorb_thin(
    groups: list[ConceptGroup], part: _Partitioner
) -> list[ConceptGroup]:
    """Fold groups too small to deserve a page into their closest neighbour.

    The recursion flushes a remainder wherever a subtree boundary falls, so a
    directory holding two files next to one holding twenty-nine can leave a
    two-file group standing on its own. A page about two files is not a
    concept, it is a directory listing with a title.

    Neighbours are considered in path order and the one sharing the longer
    prefix wins, so a thin group joins the thing it is actually near rather
    than whichever side happened to be checked first. A group with no legal
    neighbour — the merge would breach the ceiling, or cross a layer — stays,
    because a slightly thin page is better than a wrong one.
    """
    params = part.params
    working = sorted(groups, key=lambda g: g.target_path)
    changed = True
    while changed:
        changed = False
        for i, group in enumerate(working):
            if group.file_count >= params.min_files or len(working) == 1:
                continue
            best: int | None = None
            best_rank = (-1, -1)
            for j in (i - 1, i + 1):
                if j < 0 or j >= len(working):
                    continue
                other = working[j]
                if other.file_count + group.file_count > params.max_files:
                    continue
                # A same-layer neighbour is preferred, but a thin group takes
                # a cross-layer neighbour over standing alone. The layer gate
                # exists to stop a page spanning two subsystems; it should not
                # produce a one-file page, which is a worse outcome by every
                # measure the outline is judged on.
                same = part._same_layer(other.members, group.members)
                # Depth of the directory the two would share. ``"".split("/")``
                # and ``"src".split("/")`` are both length one, so measuring by
                # segment count rated "shares nothing" equal to "shares a
                # top-level directory" and let the tie fall to whichever side
                # was examined first.
                common = _target_path(group.members + other.members, [])
                shared = 0 if not common else common.count("/") + 1
                rank = (1 if same else 0, shared)
                if rank > best_rank:
                    best_rank = rank
                    best = j
            if best is None:
                continue
            merged = part.make(working[best].members + group.members)
            working[best] = merged
            del working[i]
            changed = True
            break
    return working


def _assign_targets(groups: list[ConceptGroup]) -> None:
    """Give every group a distinct ``target_path`` that is still a real directory.

    A group whose shallowest common directory also physically holds some of its
    files keeps that directory: it is the one that owns it. A group covering
    only subdirectories of that directory takes its own first directory
    instead, which is real, is inside the group, and cannot be claimed by
    anyone else because the partition never splits one directory's files across
    two groups.

    That argument is sound but it is an argument, and the cost of it being
    wrong is two pages silently sharing a row, so the loop below checks the
    result and walks to the next candidate rather than trusting it.
    """
    taken: set[str] = set()
    # Largest first so the group with the strongest claim to a directory keeps
    # the shortest name; ties break on the key so the order never depends on
    # the traversal.
    for group in sorted(groups, key=lambda g: (-g.file_count, g.structural_key)):
        owned = _target_path(group.members, group.dirs)
        candidates = [owned] if owned in group.dirs else []
        candidates.extend(d for d in group.dirs if d and d not in candidates)
        if owned and owned not in candidates:
            candidates.append(owned)
        # ``None`` rather than "" for "nothing chosen yet": the repository root
        # is a real, legitimate directory whose path *is* the empty string, and
        # a falsy check cannot tell it apart from failure. It could not, once —
        # a group holding root-level files (``setup.py``, ``manage.py``,
        # ``index.js``) fell through to the hash fallback and was titled after
        # its own digest.
        chosen: str | None = None
        for candidate in candidates:
            if candidate not in taken:
                chosen = candidate
                break
        if chosen is None:
            # Every real directory is spoken for. Fall back to the members'
            # common prefix qualified by the group's identity, which is stable
            # and unique even though it is no longer only a directory.
            chosen = f"{owned}#{group.structural_key.rsplit('-', 1)[-1]}"
        group.target_path = chosen
        taken.add(chosen)


def group_files(
    paths: Iterable[str],
    *,
    layer_of_file: dict[str, str] | None = None,
    params: GroupingParams | None = None,
) -> list[ConceptGroup]:
    """Partition *paths* into bounded, path-local concept groups.

    *paths* must already exclude test files (D8) — this function does not
    filter, so a caller that wants tests in gets them in and the count reflects
    it. *layer_of_file* maps a file to its curated layer id and only affects
    whether two adjacent runs may be merged; grouping works without it.

    Groups come back in path order, which is also the order their members were
    walked, so the sequence itself is stable.
    """
    files = sorted({p.replace("\\", "/") for p in paths if p})
    if not files:
        return []
    tree = _build_tree(files)
    resolved = params or params_for(len(files))
    part = _Partitioner(resolved, layer_of_file or {})
    leftover = part.visit(tree)
    if leftover:
        # The whole repository fits under the ceiling: one group, which is the
        # honest answer for a tiny repository.
        part.groups.append(part.make(leftover))
    final = _absorb_thin(part.groups, part)
    _assign_targets(final)
    final.sort(key=lambda g: (g.target_path, g.structural_key))
    return final
