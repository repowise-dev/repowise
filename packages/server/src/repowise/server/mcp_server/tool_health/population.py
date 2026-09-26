"""The file population one get_health call describes, and the targets within it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select

from repowise.core.analysis.health.counts import parse_counts
from repowise.core.analysis.health.counts import project as project_counts
from repowise.core.analysis.health.models import split_by_origin
from repowise.core.analysis.health.scope import parse_scope
from repowise.core.persistence.models import HealthFileMetric
from repowise.server.mcp_server._helpers import _get_exclude_spec, filter_rows_by_attr
from repowise.server.mcp_server.tool_health.request import HealthRequest
from repowise.server.mcp_server.tool_health.targets import _expand_module_targets


@dataclass
class Population:
    """Metric rows after exclude config, ``scope`` and ``counts``, plus targets."""

    all_metrics: list[HealthFileMetric]
    exclude_spec: Any
    excluded_paths: set[str]
    reported_scope: str
    scope_paths: set[str] | None
    reported_counts: str
    unscored_files: int
    unscored_paths: set[str]
    file_targets: list[str]
    matched_modules: set[str]
    scoped: bool
    effective_targets: list[str]

    @property
    def code_shape(self) -> bool:
        return self.reported_counts == "code_shape"

    @property
    def nothing_resolved(self) -> bool:
        return self.scoped and not self.effective_targets

    @property
    def target_paths(self) -> tuple[str, ...] | None:
        """The targets as a read filter; ``None`` means the whole repository."""
        return tuple(self.effective_targets) if self.scoped else None

    def in_scope_rows(self, rows: list, attr: str = "file_path") -> list:
        rows = filter_rows_by_attr(rows, attr, self.exclude_spec)
        if self.scope_paths is None:
            return rows
        return [r for r in rows if getattr(r, attr, None) in self.scope_paths]

    def in_counts_findings(self, rows: list) -> list:
        """A history finding cannot explain a score its half was taken out
        of, so it is not part of the code-shape reading."""
        return split_by_origin(rows)[0] if self.code_shape else rows


async def load_population(
    session: Any, repository: Any, repo_path: Any, req: HealthRequest
) -> Population:
    """Read the metric rows and narrow them the way every block will see them."""
    all_metrics_q = select(HealthFileMetric).where(
        HealthFileMetric.repository_id == repository.id
    )
    exclude_spec = _get_exclude_spec(repo_path)
    indexed_rows = list((await session.execute(all_metrics_q)).scalars().all())
    all_metrics = filter_rows_by_attr(indexed_rows, "file_path", exclude_spec)
    # Paths the index knows about but the exclude config drops. Kept so an
    # unresolved target can report "excluded" (a config decision) rather
    # than "no_such_path" (a typo) — the two need different responses.
    # Computed before ``scope`` narrows the list, or a test file would be
    # reported as dropped by a config that says nothing about it.
    excluded_paths = {m.file_path for m in indexed_rows} - {m.file_path for m in all_metrics}

    # Narrowing to production is the same shape of question as the exclude
    # config: both drop whole files from every block at once. Folding it
    # into one filter is what keeps a scoped dashboard from ranking a
    # finding on a file its own file list no longer contains.
    reported_scope = parse_scope(req.scope)
    scope_paths: set[str] | None = None
    if reported_scope == "production":
        all_metrics = [m for m in all_metrics if not m.is_test]
        scope_paths = {m.file_path for m in all_metrics}

    # Composes with the scope: a re-read off the stored structure/history
    # split, not a rescore. Findings narrow by origin, not by path, so a row
    # it cannot read lands in ``unscored_files`` with its findings intact.
    reported_counts = parse_counts(req.counts)
    unscored_files = 0
    unscored_paths: set[str] = set()
    if reported_counts == "code_shape":
        before = {m.file_path for m in all_metrics}
        all_metrics, unscored_files = project_counts(req.counts, all_metrics)
        unscored_paths = before - {m.file_path for m in all_metrics}

    file_targets, matched_modules = _expand_module_targets(
        all_metrics, req.module_targets, req.file_targets
    )
    # A non-empty ``targets`` means the caller asked for a scope, and that
    # holds even when nothing resolves. Keying the mode off the *resolved*
    # paths let ``targets=["module:typo"]`` fall through to dashboard mode
    # and answer a module-scoped question with repo-wide numbers — an
    # answer that reads as scoped and is not.
    scoped = bool(req.raw_targets)
    return Population(
        all_metrics=all_metrics,
        exclude_spec=exclude_spec,
        excluded_paths=excluded_paths,
        reported_scope=reported_scope,
        scope_paths=scope_paths,
        reported_counts=reported_counts,
        unscored_files=unscored_files,
        unscored_paths=unscored_paths,
        file_targets=file_targets,
        matched_modules=matched_modules,
        scoped=scoped,
        effective_targets=file_targets if scoped else [],
    )
