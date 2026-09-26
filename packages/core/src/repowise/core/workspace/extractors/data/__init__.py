"""App-to-database contract extraction.

Scans source files for table *providers* (DDL, migrations, ORM entities) and
table *consumers* (SQL string literals and query builders in app code). Each recogniser is an
independent dialect module registered in :data:`PROVIDER_DIALECTS` /
:data:`CONSUMER_DIALECTS`, run by the shared :class:`..dialect.DialectExtractor`.
Matching happens downstream in :mod:`repowise.core.workspace.matching` on the
normalized ``data::<table>`` id, and a matched link renders as a ``db`` edge on
the Live System Map.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor
from .ddl import DdlDialect
from .drizzle import DrizzleDialect
from .knex import KnexDialect
from .laravel import EloquentDialect, LaravelMigrationDialect, LaravelQueryDialect
from .names import normalize_table_name
from .orm_models import (
    ActiveRecordDialect,
    EfCoreDialect,
    JpaDialect,
    SqlAlchemyDjangoDialect,
)
from .prisma import PrismaDialect
from .sequelize import SequelizeDialect
from .sql_strings import SqlStringsDialect
from .typeorm import TypeOrmDialect

# Table-ownership recognisers (DDL + one per ORM family).
PROVIDER_DIALECTS: tuple[ContractDialect, ...] = (
    DdlDialect(),
    SqlAlchemyDjangoDialect(),
    JpaDialect(),
    EfCoreDialect(),
    ActiveRecordDialect(),
    EloquentDialect(),
    LaravelMigrationDialect(),
    PrismaDialect(),
    TypeOrmDialect(),
    SequelizeDialect(),
    DrizzleDialect(),
)

# Table-access recognisers.
# Knex reads both sides (its migrations and its query builder), so it is
# registered once, here.
CONSUMER_DIALECTS: tuple[ContractDialect, ...] = (
    SqlStringsDialect(),
    LaravelQueryDialect(),
    KnexDialect(),
)


class DataExtractor(DialectExtractor):
    """Extract table contracts from source files via registered dialects."""

    dialects = PROVIDER_DIALECTS + CONSUMER_DIALECTS


__all__ = [
    "CONSUMER_DIALECTS",
    "PROVIDER_DIALECTS",
    "DataExtractor",
    "normalize_table_name",
]
