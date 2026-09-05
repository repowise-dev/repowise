"""Split a diff into groups of changed files that the index does not connect.

Grouping is connected components over index edges, stored co-change pairs and
the files each commit of the range touched. Not Leiden or Louvain: a diff is
tens of files and the question is plain connectivity. Only source files a
resolver can link are grouped; docs, config, tests and files the index has
never linked are reported rather than claimed as changes of their own.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import networkx as nx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from repowise.core.analysis.graph_view import can_carry_dependency
from repowise.core.co_change import parse_partners
from repowise.core.persistence.batches import chunked
from repowise.core.persistence.models import GitMetadata, GraphEdge, GraphNode

# Under three files a group has no articulation point worth naming: removing
# either end of a pair leaves one file, which is not a split.
_MIN_FILES_FOR_BRIDGE = 3

# Node-id prefix of a dependency outside the repository. An edge to one is not
# a link to another file of this change.
_EXTERNAL_PREFIX = "external:"

# What was actually checked, which is not the same sentence when the commits of
# the range were available to check.
_BASIS = (
    "no import, call, type reference or co-change pair in the index links "
    "one group to another"
)
_BASIS_WITH_COMMITS = (
    "no import, call, type reference, co-change pair or shared commit in the "
    "index and this range links one group to another"
)

_Pairs = set[tuple[str, str]]


@dataclass(frozen=True)
class ChangeGroup:
    """One set of changed files that the index links together."""

    files: tuple[str, ...]
    bridging_files: tuple[str, ...]


@dataclass(frozen=True)
class IndependentChanges:
    """A diff seen as several changes that share no link in the index."""

    groups: tuple[ChangeGroup, ...]
    ungrouped_files: tuple[str, ...]
    # Whether the commits of the range were available, which the basis states.
    commits_known: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "count": len(self.groups),
            "groups": [
                {"files": list(g.files), "bridging_files": list(g.bridging_files)}
                for g in self.groups
            ],
            "ungrouped_files": list(self.ungrouped_files),
            "basis": _BASIS_WITH_COMMITS if self.commits_known else _BASIS,
            "summary": self._summary(),
        }

    def _summary(self) -> str:
        """One sentence per claim: the split, then what the index could not see."""
        sizes = _join([_files(len(g.files)) for g in self.groups])
        text = f"This diff is {len(self.groups)} independent changes: {sizes}."
        loose = len(self.ungrouped_files)
        if loose:
            subject = "1 changed file is" if loose == 1 else f"{loose} changed files are"
            text += (
                f" {subject} left out of the grouping: docs, config, tests, "
                "files not in the index, or files it has never linked."
            )
        return text


def _files(n: int) -> str:
    return "1 file" if n == 1 else f"{n} files"


def _join(parts: list[str]) -> str:
    """``"a, b and c"``: the last two joined by "and", the rest by commas."""
    if len(parts) < 2:
        return "".join(parts)
    return f"{', '.join(parts[:-1])} and {parts[-1]}"


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


async def _read_nodes(
    session: AsyncSession, repo_id: str, paths: list[str]
) -> tuple[dict[str, str], set[str], set[str]]:
    """``(node id -> owning changed file, indexed files, files that can be grouped)``.

    Two seeks rather than one ``OR``: a file node is found by ``node_id`` and
    the symbols it owns by ``file_path``, and each has its own index.
    """
    rows: list[tuple[str, str, str | None, str | None, bool]] = []
    for column in (GraphNode.node_id, GraphNode.file_path):
        for chunk in chunked(paths):
            result = await session.execute(
                select(
                    GraphNode.node_id,
                    GraphNode.node_type,
                    GraphNode.file_path,
                    GraphNode.language,
                    GraphNode.is_test,
                ).where(GraphNode.repository_id == repo_id, column.in_(chunk))
            )
            rows.extend(result.all())

    changed = set(paths)
    files = [
        (n, lang, is_test)
        for n, node_type, _fp, lang, is_test in rows
        if node_type == "file" and n in changed
    ]
    indexed = {n for n, _lang, _test in files}
    # A test attaches to whatever shared harness the diff holds rather than to
    # the code it exercises, and a page co-changes with whatever shipped beside
    # it, so neither can be the file a group rests on.
    groupable = {n for n, lang, is_test in files if not is_test and can_carry_dependency(n, lang)}
    owner: dict[str, str] = {}
    for node_id, node_type, file_path, _lang, _is_test in rows:
        own = node_id if node_type == "file" else file_path
        if own in indexed:
            owner[node_id] = own
    return owner, indexed, groupable


async def _read_pairs(
    session: AsyncSession, repo_id: str, owner: dict[str, str]
) -> tuple[_Pairs, set[str]]:
    """``(file pairs inside the diff, changed files with a link leaving them)``.

    Distinct ends rather than every edge: a hot file carries thousands of call
    edges to a handful of neighbours. Same-file ends are dropped, which removes
    the containment types (``defines``, ``has_method``) without listing them.
    """
    pairs: _Pairs = set()
    linked: set[str] = set()
    for chunk in chunked(sorted(owner)):
        result = await session.execute(
            select(GraphEdge.source_node_id, GraphEdge.target_node_id)
            .distinct()
            .where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.source_node_id.in_(chunk),
                GraphEdge.target_node_id.not_like(f"{_EXTERNAL_PREFIX}%"),
            )
        )
        for source, target in result.all():
            near, far = owner[source], owner.get(target, target)
            if near == far:
                continue
            linked.add(near)
            if target in owner:
                pairs.add(_pair(near, far))
    return pairs, linked


async def _read_inbound_links(
    session: AsyncSession, repo_id: str, owner: dict[str, str]
) -> set[str]:
    """Changed files that a file outside the diff links to.

    Only the arrows coming in: the pairs read already answers the outward
    direction, and a far end that is one of our own nodes is either a pair or
    one of a file's own symbols.
    """
    node_ids = sorted(owner)
    not_ours = [GraphEdge.source_node_id.not_in(piece) for piece in chunked(node_ids)]
    linked: set[str] = set()
    for chunk in chunked(node_ids):
        result = await session.execute(
            select(GraphEdge.target_node_id)
            .distinct()
            .where(
                GraphEdge.repository_id == repo_id,
                GraphEdge.target_node_id.in_(chunk),
                GraphEdge.source_node_id.not_like(f"{_EXTERNAL_PREFIX}%"),
                *not_ours,
            )
        )
        linked.update(owner[node_id] for (node_id,) in result.all())
    return linked


async def _read_co_change(
    session: AsyncSession, repo_id: str, indexed: set[str]
) -> tuple[_Pairs, set[str]]:
    """``(pairs where both ends changed, changed files carrying any partner)``."""
    pairs: _Pairs = set()
    partnered: set[str] = set()
    for chunk in chunked(sorted(indexed)):
        result = await session.execute(
            select(GitMetadata.file_path, GitMetadata.co_change_partners_json).where(
                GitMetadata.repository_id == repo_id,
                GitMetadata.file_path.in_(chunk),
            )
        )
        for file_path, raw in result.all():
            partners = parse_partners(raw)
            if partners:
                partnered.add(file_path)
            for partner in partners:
                if partner.file_path in indexed and partner.file_path != file_path:
                    pairs.add(_pair(file_path, partner.file_path))
    return pairs, partnered


def _commit_pairs(commit_sets: Iterable[Iterable[str]], groupable: set[str]) -> _Pairs:
    """Pairs of groupable files one commit of this range touched.

    An author putting two files in one commit says more than any edge. A set
    covering every groupable file is the diff restated and says nothing about
    which files belong together, so it is skipped.
    """
    pairs: _Pairs = set()
    for touched in commit_sets:
        members = groupable.intersection(touched)
        if len(members) == len(groupable):
            continue
        ordered = sorted(members)
        for i, first in enumerate(ordered):
            for second in ordered[i + 1 :]:
                pairs.add((first, second))
    return pairs


def _partition(
    groupable: set[str], linked: set[str], pairs: _Pairs
) -> tuple[list[ChangeGroup], list[str]]:
    """``(groups largest first, files left ungrouped)``.

    A component has to carry one file something has in fact linked, because
    elsewhere an absent edge is not evidence of independence.
    """
    graph = nx.Graph()
    graph.add_nodes_from(sorted(groupable))
    graph.add_edges_from(sorted(pairs))

    groups: list[ChangeGroup] = []
    ungrouped: list[str] = []
    for component in nx.connected_components(graph):
        files = sorted(component)
        if not linked.intersection(files):
            ungrouped.extend(files)
            continue
        bridging: tuple[str, ...] = ()
        if len(files) >= _MIN_FILES_FOR_BRIDGE:
            bridging = tuple(sorted(nx.articulation_points(graph.subgraph(files))))
        groups.append(ChangeGroup(files=tuple(files), bridging_files=bridging))
    groups.sort(key=lambda g: (-len(g.files), g.files[0]))
    return groups, ungrouped


async def independent_changes(
    session: AsyncSession,
    repo_id: str,
    changed_files: Iterable[str],
    *,
    commit_sets: Iterable[Iterable[str]] = (),
) -> IndependentChanges | None:
    """The changed files split into groups nothing connects.

    *commit_sets* is the files each commit of the range touched. ``None`` when
    fewer than two groups survive: one change gets no report.
    """
    paths = sorted({p for p in changed_files if p})
    if len(paths) < 2:
        return None
    # Materialised once: the caller may hand over a generator, and the basis
    # states whether there was anything to check.
    known_commits = [list(touched) for touched in commit_sets]

    owner, indexed, groupable = await _read_nodes(session, repo_id, paths)
    if len(groupable) < 2:
        return None

    pairs, linked = await _read_pairs(session, repo_id, owner)
    linked |= await _read_inbound_links(session, repo_id, owner)
    linked |= {f for pair in pairs for f in pair}
    co_pairs, partnered = await _read_co_change(session, repo_id, indexed)
    commit_linked = _commit_pairs(known_commits, groupable)
    pairs |= co_pairs | commit_linked
    linked |= partnered | {f for pair in commit_linked for f in pair}

    # Only groupable files are graph nodes, so an edge touching a doc or a test
    # must not survive to bridge two groups through it.
    pairs = {(a, b) for a, b in pairs if a in groupable and b in groupable}

    groups, ungrouped = _partition(groupable, linked, pairs)
    if len(groups) < 2:
        return None

    ungrouped.extend(p for p in paths if p not in groupable)
    return IndependentChanges(
        groups=tuple(groups),
        ungrouped_files=tuple(sorted(ungrouped)),
        commits_known=bool(known_commits),
    )
