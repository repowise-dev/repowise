"""Pruning of rows for files that left the repository or the index scope."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import structlog

# Patchable steps are looked up on the package at call time, so a patch on
# ``repowise.core.pipeline.persist`` reaches this module too.
from repowise.core.pipeline import persist as _persist

logger = structlog.get_logger("repowise.core.pipeline.persist")


# Chunk size for IN (...) deletes — stays under SQLite's host-parameter limit.
_PRUNE_CHUNK = 500


async def _prune_stale_file_rows(
    session: Any,
    repo_id: str,
    current_graph_file_paths: set[str],
    current_git_file_paths: set[str],
    *,
    authoritative_empty: bool = False,
) -> None:
    """Delete file-scoped rows for files absent from the latest full pipeline run.

    The parser and git indexer disagree on the file set — a file can be
    git-tracked yet absent from ``parsed_files`` (parse failure, unparsed
    extension, skipped) — so the tables use different sources of truth.
    *current_graph_file_paths* (from ``parsed_files``) governs graph/analysis
    tables; *current_git_file_paths* (from ``git_metadata_list``) governs
    ``git_metadata`` only. Each set independently no-ops when empty to avoid
    wiping rows on a broken run, unless ``authoritative_empty`` confirms that a
    successful full traversal really produced an empty scope.

    Full runs only. The authority here is "absent from this run's output",
    which is only safe because a full run rebuilds every table it prunes from
    in the same transaction. Incremental runs use
    :func:`prune_deleted_file_rows`, whose authority is the filesystem and git
    rather than the parse — see its docstring for why the two differ.
    """
    from sqlalchemy import delete, or_, select

    from repowise.core.persistence.models import (
        CoverageFile,
        DeadCodeFinding,
        GitFunctionBlame,
        GitMetadata,
        GraphEdge,
        GraphMetric,
        GraphNode,
        HealthFileMetric,
        HealthFinding,
        SecurityFinding,
        WikiSymbol,
    )

    async def _delete_stale_by_paths(
        model: Any,
        column: Any,
        current: set[str],
        *,
        allow_empty: bool | None = None,
    ) -> None:
        # Diff persisted paths against *current* in Python so the IN (...) is
        # bounded by the stale set, not the whole repo (SQLite param limit).
        empty_is_authoritative = authoritative_empty if allow_empty is None else allow_empty
        if not current and not empty_is_authoritative:
            return
        existing = set(
            (await session.execute(select(column).where(model.repository_id == repo_id).distinct()))
            .scalars()
            .all()
        )
        stale = [p for p in existing if p not in current]
        for i in range(0, len(stale), _PRUNE_CHUNK):
            await session.execute(
                delete(model).where(
                    model.repository_id == repo_id,
                    column.in_(stale[i : i + _PRUNE_CHUNK]),
                )
            )

    # ---- Graph nodes + edges -------------------------------------------------
    # File nodes key on node_id; symbol nodes on file_path. Delete edges before
    # nodes (no FK cascade between the tables).
    if current_graph_file_paths or authoritative_empty:
        node_rows = (
            await session.execute(
                select(GraphNode.node_id, GraphNode.node_type, GraphNode.file_path).where(
                    GraphNode.repository_id == repo_id
                )
            )
        ).all()
        stale_node_ids = [
            node_id
            for node_id, node_type, file_path in node_rows
            if (node_type == "file" and node_id not in current_graph_file_paths)
            or (
                node_type != "file"
                and file_path is not None
                and file_path not in current_graph_file_paths
            )
        ]
        for i in range(0, len(stale_node_ids), _PRUNE_CHUNK):
            batch = stale_node_ids[i : i + _PRUNE_CHUNK]
            await session.execute(
                delete(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    or_(
                        GraphEdge.source_node_id.in_(batch),
                        GraphEdge.target_node_id.in_(batch),
                    ),
                )
            )
            await session.execute(
                delete(GraphNode).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_id.in_(batch),
                )
            )

    # GraphMetric is file-level only (node_id == path).
    await _delete_stale_by_paths(GraphMetric, GraphMetric.node_id, current_graph_file_paths)
    await _delete_stale_by_paths(WikiSymbol, WikiSymbol.file_path, current_graph_file_paths)
    await _delete_stale_by_paths(
        SecurityFinding, SecurityFinding.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(
        DeadCodeFinding, DeadCodeFinding.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(
        HealthFileMetric, HealthFileMetric.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(HealthFinding, HealthFinding.file_path, current_graph_file_paths)
    await _delete_stale_by_paths(CoverageFile, CoverageFile.file_path, current_graph_file_paths)
    await _delete_stale_by_paths(
        GitFunctionBlame, GitFunctionBlame.file_path, current_graph_file_paths
    )
    await _delete_stale_by_paths(
        GitMetadata,
        GitMetadata.file_path,
        current_git_file_paths,
        # An empty Git result is authoritative only when the successful source
        # traversal is empty too. Otherwise it may be a degraded Git walk.
        allow_empty=authoritative_empty and not current_graph_file_paths,
    )


# A prune that would take more than this share of a table is read as a broken
# run rather than a large commit, and refused. The asymmetry is the whole
# argument: refusing leaves stale rows, which are visible in the UI and cleared
# by a reindex, where proceeding deletes live ones, which are invisible until
# someone notices the symbol is missing.
_PRUNE_MAX_FRACTION = 0.5
# ...but a small repo can legitimately lose half its files in one commit, so the
# fraction only applies once the *deletion* is big enough for the ratio to mean
# something. Measured on the deletion rather than the table so that a 25-row
# store losing 20 rows still prunes: at that size "most of it went" is an
# ordinary commit, not evidence of a broken run.
_PRUNE_FLOOR_MIN_ROWS = 20


def _git_tracked_paths(root: Path) -> frozenset[str]:
    """Every path git tracks at HEAD, POSIX-relative, or an empty set on failure.

    Empty is indistinguishable from "git failed" on purpose: both mean this
    witness has nothing to say, and the caller treats silence as "no opinion"
    rather than as "nothing is tracked".
    """
    from repowise.core.git_refs import tracked_paths

    return tracked_paths(str(root))


class _FileLiveness:
    """Is this path still a real file, judged without consulting this run's parser.

    Two witnesses, neither of which is the parse: the file is present on disk,
    or git still tracks it. A path has to fail both to count as deleted.

    That split is the point. Deriving deletions from ``parsed_files`` alone
    makes every transient read or parse failure look like a deletion, and on
    Windows a file lock or an antivirus scan produces exactly that: the file is
    there, the run could not read it, and the naive prune deletes its rows.
    The disk check answers for that case; git covers the narrower one where the
    stat itself fails (``Path.exists()`` reports False on a permission error).

    The deliberate cost of taking the union: a file that a config change has
    newly *excluded* still exists and is still tracked, so its rows survive an
    incremental update. That matches the behaviour before this guard existed
    (which pruned nothing at all on this path), and a full reindex still clears
    it, so nothing regresses.

    ``git ls-files`` is spawned lazily, on the first path that is not on disk,
    so an update with no deletions never pays for it.
    """

    def __init__(self, repo_path: Any, *, git_root_keyed: bool = False) -> None:
        self._root = Path(repo_path)
        self._tracked: frozenset[str] | None = None
        # Rows written by the git indexer are keyed relative to the git root,
        # which is not ``_root`` when the index covers a subdirectory. Every
        # other table is keyed relative to ``_root``, so only a git-keyed
        # instance resolves paths against the toplevel.
        self._git_root_keyed = git_root_keyed
        self._toplevel: Path | bool | None = False
        # The same path is asked about once per table it has rows in, and a
        # stat under an antivirus scanner is not free.
        self._memo: dict[str, bool] = {}

    def is_live(self, path: str) -> bool:
        answer = self._memo.get(path)
        if answer is None:
            answer = self._is_live(path)
            self._memo[path] = answer
        return answer

    def _git_toplevel(self) -> Path | None:
        if self._toplevel is False:
            from repowise.core.git_refs import toplevel

            top = toplevel(str(self._root))
            resolved = Path(top).resolve() if top else None
            self._toplevel = resolved if resolved and resolved != self._root.resolve() else None
        return self._toplevel  # type: ignore[return-value]

    def _is_live(self, path: str) -> bool:
        root = (self._git_toplevel() or self._root) if self._git_root_keyed else self._root
        if (root / path).exists():
            return True
        if self._tracked is None:
            self._tracked = _git_tracked_paths(root)
        return path in self._tracked


def path_liveness(repo_path: Any) -> Callable[[str], bool] | None:
    """Whether a repo-relative path is still on disk or tracked by git; ``None``
    without a checkout to ask, so no caller reads a path as deleted on no evidence."""
    return _FileLiveness(repo_path).is_live if repo_path else None


async def _resolve_plans_of_deleted_files(
    session: Any, repo_id: str, dead: Callable[[set[str], str], list[str]], liveness: Any
) -> None:
    from sqlalchemy import select

    from repowise.core.persistence.crud import PayoffContext, upsert_refactoring_suggestions
    from repowise.core.persistence.models import RefactoringSuggestion

    live_plan_paths = await session.execute(
        select(RefactoringSuggestion.file_path)
        .where(
            RefactoringSuggestion.repository_id == repo_id,
            RefactoringSuggestion.status.in_(("open", "acknowledged")),
        )
        .distinct()
    )
    gone = dead(set(live_plan_paths.scalars().all()), "refactoring_suggestions")
    if gone:
        payoff = PayoffContext(
            fact_rows=None,
            is_live=liveness.is_live,
            commit=await _persist._analyzed_commit(session, repo_id),
        )
        await upsert_refactoring_suggestions(session, repo_id, [], file_paths=gone, payoff=payoff)


async def prune_deleted_file_rows(
    session: Any,
    repo_id: str,
    repo_path: Any,
    *,
    live_hint: set[str] | None = None,
) -> tuple[int, list[str]]:
    """Delete file-scoped rows for files that are gone, on an incremental update.

    The incremental path never pruned anything: deleting a file tombstoned its
    wiki page and left its graph nodes, edges, metrics, symbols, health rows and
    git metadata behind, so MCP, search and every health aggregate kept counting
    a file that no longer exists until someone ran a full reindex.

    The authority is deliberately *not* the one :func:`_prune_stale_file_rows`
    uses. A full run rebuilds every table it prunes from, so "absent from this
    run's output" is a safe question there. An incremental run rebuilds nothing:
    a row deleted here is not rewritten, so the question has to be "is the file
    gone", answered by :class:`_FileLiveness` from the filesystem and git rather
    than from the parse. They differ exactly in the transient-failure case, and
    that case is the one that loses data.

    *live_hint* is what this run already knows to be live: the paths that
    parsed, plus every file node the rebuilt graph holds. The first half is
    purely an optimisation, since anything in it would pass the liveness test
    anyway and the stat is skipped. The second half is load-bearing: a node
    like ``external:...`` or Spring's ``META-INF/services/<iface>`` names no
    file, so it fails every liveness test there is, and only the fact that the
    graph build just re-minted it says it is not a deletion.

    Returns ``(deleted_path_count, refusals)``, where a refusal is a one-line
    explanation of a floor guard that fired, for the caller's degraded report.
    """
    from sqlalchemy import delete, or_, select

    # Not every ``node_type == "file"`` row names a file. ``external:`` and
    # ``framework:`` nodes are minted for third-party imports and for
    # convention-based loading, they are stored as file nodes, and they answer
    # "no" to every liveness question there is because no such file was ever
    # meant to exist. Without this they read as a mass deletion: 223 of hugo's
    # 2,356 file nodes, 590 of react's 3,353, and 70% of spring-petclinic's,
    # which is past the floor guard and would have been reported as a refusal
    # rather than as the bug it is.
    #
    # Imported rather than re-listed: a prefix added to one copy and not the
    # other is exactly the bug above. Costs nothing on this path, where the
    # partial dead-code analysis has already loaded the module (13.7 ms when
    # it has not, measured cold after the CLI modules are up).
    from repowise.core.analysis.dead_code.analyzer import _is_synthetic_node
    from repowise.core.persistence.crud.analysis.function_facts import delete_file_rows
    from repowise.core.persistence.models import (
        DeadCodeFinding,
        DocDriftFinding,
        DocDriftReference,
        GitMetadata,
        GraphEdge,
        GraphMetric,
        GraphNode,
        HealthFileMetric,
        HealthFinding,
        SecurityFinding,
        WikiSymbol,
    )

    hint = live_hint or set()
    liveness = _FileLiveness(repo_path)
    git_liveness = _FileLiveness(repo_path, git_root_keyed=True)
    refusals: list[str] = []
    deleted: set[str] = set()

    def _dead(persisted: set[str], label: str, live: _FileLiveness = liveness) -> list[str]:
        """Paths in *persisted* that no longer exist, or [] if the floor fired."""
        persisted = {p for p in persisted if not _is_synthetic_node(p)}
        dead = [p for p in persisted if p not in hint and not live.is_live(p)]
        if (
            len(dead) > _PRUNE_FLOOR_MIN_ROWS
            and persisted
            and len(dead) > _PRUNE_MAX_FRACTION * len(persisted)
        ):
            refusals.append(
                f"Deleted-file prune refused for {label}: {len(dead)} of "
                f"{len(persisted)} paths looked deleted, which reads as a broken "
                "run rather than a commit. Run a full reindex to clear them."
            )
            return []
        deleted.update(dead)
        return dead

    async def _prune_table(
        model: Any, column: Any, label: str, live: _FileLiveness = liveness
    ) -> None:
        persisted = set(
            (await session.execute(select(column).where(model.repository_id == repo_id).distinct()))
            .scalars()
            .all()
        )
        persisted.discard(None)
        dead = _dead(persisted, label, live)
        for i in range(0, len(dead), _PRUNE_CHUNK):
            await session.execute(
                delete(model).where(
                    model.repository_id == repo_id,
                    column.in_(dead[i : i + _PRUNE_CHUNK]),
                )
            )

    # ---- Graph nodes + edges -------------------------------------------------
    # File nodes key on node_id, symbol nodes on file_path, and edges have no FK
    # cascade from either, so edges go first.
    node_rows = (
        await session.execute(
            select(GraphNode.node_id, GraphNode.node_type, GraphNode.file_path).where(
                GraphNode.repository_id == repo_id
            )
        )
    ).all()
    node_paths = {
        (node_id if node_type == "file" else file_path)
        for node_id, node_type, file_path in node_rows
        if node_type == "file" or file_path
    }
    dead_paths = set(_dead(node_paths, "graph_nodes"))
    if dead_paths:
        stale_node_ids = [
            node_id
            for node_id, node_type, file_path in node_rows
            if (node_id if node_type == "file" else file_path) in dead_paths
        ]
        for i in range(0, len(stale_node_ids), _PRUNE_CHUNK):
            batch = stale_node_ids[i : i + _PRUNE_CHUNK]
            await session.execute(
                delete(GraphEdge).where(
                    GraphEdge.repository_id == repo_id,
                    or_(
                        GraphEdge.source_node_id.in_(batch),
                        GraphEdge.target_node_id.in_(batch),
                    ),
                )
            )
            await session.execute(
                delete(GraphNode).where(
                    GraphNode.repository_id == repo_id,
                    GraphNode.node_id.in_(batch),
                )
            )

    await _prune_table(GraphMetric, GraphMetric.node_id, "graph_metrics")
    await _prune_table(WikiSymbol, WikiSymbol.file_path, "wiki_symbols")
    await _prune_table(SecurityFinding, SecurityFinding.file_path, "security_findings")
    await _prune_table(DeadCodeFinding, DeadCodeFinding.file_path, "dead_code_findings")
    # Plans are resolved, not deleted, so a held id keeps answering. A scoped
    # run never names a deleted file (or a renamed file's old path) again, so
    # this is the one place they resolve. Before the facts below go: those are
    # the target's measures the payoff keeps.
    await _resolve_plans_of_deleted_files(session, repo_id, _dead, liveness)
    # Keyed on the symbol: a deleted file's rows are one key range of the id.
    await delete_file_rows(session, repo_id, dead_paths)
    # Keyed on the DOCUMENT. The incremental drift pass scopes its write to the
    # documents it read, so a deleted one is never in scope and its rows would
    # outlive the file without this. ``_FileLiveness`` asks disk and
    # ``git ls-files`` rather than the parse, so a ``.md`` path is judged
    # correctly here.
    await _prune_table(DocDriftFinding, DocDriftFinding.file_path, "doc_drift_findings")
    # On the document only. A reference to a deleted *target* is drift, not a
    # dead row: the next pass re-resolves it into a finding, and pruning by
    # target here would delete the evidence before anything reported it.
    await _prune_table(
        DocDriftReference, DocDriftReference.document_path, "doc_drift_references"
    )
    await _prune_table(HealthFileMetric, HealthFileMetric.file_path, "health_file_metrics")
    await _prune_table(HealthFinding, HealthFinding.file_path, "health_findings")
    # git_metadata is keyed off the git indexer on a full run, but an
    # incremental run only indexes the changed files, so the same liveness
    # question is the only authority available here too.
    await _prune_table(GitMetadata, GitMetadata.file_path, "git_metadata", git_liveness)

    return len(deleted), refusals
