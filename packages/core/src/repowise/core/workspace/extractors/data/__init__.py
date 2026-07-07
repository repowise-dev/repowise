"""App-to-database contract extraction.

Scans source files for table *providers* (DDL, migrations, ORM entities) and
table *consumers* (SQL string literals in app code). Same architecture as the
HTTP extractor: each recogniser is an independent dialect module registered in
:data:`PROVIDER_DIALECTS` / :data:`CONSUMER_DIALECTS`; the :class:`DataExtractor`
orchestrator owns only the file walk and dispatch. Matching happens downstream
in ``contracts.match_contracts`` on the normalized ``data::<table>`` id, and a
matched link renders as a ``db`` edge on the Live System Map.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..base import ScanContext, iter_source_files
from .ddl import DdlDialect
from .dialect import DataDialect
from .names import normalize_table_name
from .orm_models import (
    ActiveRecordDialect,
    EfCoreDialect,
    EloquentDialect,
    JpaDialect,
    SqlAlchemyDjangoDialect,
)
from .sql_strings import SqlStringsDialect

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from repowise.core.workspace.contracts import Contract

# Table-ownership recognisers (DDL + one per ORM family).
PROVIDER_DIALECTS: tuple[DataDialect, ...] = (
    DdlDialect(),
    SqlAlchemyDjangoDialect(),
    JpaDialect(),
    EfCoreDialect(),
    ActiveRecordDialect(),
    EloquentDialect(),
)

# Table-access recognisers.
CONSUMER_DIALECTS: tuple[DataDialect, ...] = (SqlStringsDialect(),)


def _union_extensions(dialects: tuple[DataDialect, ...]) -> frozenset[str]:
    out: set[str] = set()
    for d in dialects:
        out |= d.extensions
    return frozenset(out)


class DataExtractor:
    """Extract table contracts from source files via registered dialects."""

    provider_dialects: tuple[DataDialect, ...] = PROVIDER_DIALECTS
    consumer_dialects: tuple[DataDialect, ...] = CONSUMER_DIALECTS

    def extract(
        self,
        repo_path: Path,
        repo_alias: str = "",
        exclude: Callable[[str], bool] | None = None,
    ) -> list[Contract]:
        """Scan all source files in *repo_path* and return Contract instances."""
        all_exts = _union_extensions(self.provider_dialects) | _union_extensions(
            self.consumer_dialects
        )
        contracts: list[Contract] = []
        for rel_path, suffix, content in iter_source_files(repo_path, all_exts, exclude):
            ctx = ScanContext(repo_alias, rel_path, suffix, content)
            for dialect in self.provider_dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
            for dialect in self.consumer_dialects:
                if suffix in dialect.extensions:
                    contracts.extend(dialect.extract(ctx))
        return contracts


__all__ = [
    "CONSUMER_DIALECTS",
    "PROVIDER_DIALECTS",
    "DataExtractor",
    "normalize_table_name",
]
