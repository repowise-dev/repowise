"""Prisma schema models: ``model User { ... }`` in a ``.prisma`` file.

A model's table is its ``@@map("users")`` name, else the model's own name
(Prisma does not pluralize); ``@@schema("x")`` qualifies it. Views, enums and
types declare no table. The schema models its tables: whether it also creates
them (``prisma migrate``) or reads a database another service migrated
(``prisma db pull``) is not in the file, and the migration SQL Prisma writes
is the DDL dialect's.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from .dialect import EXPLICIT_CONFIDENCE, dedup_emit

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_MODEL_RE = re.compile(r"^[ \t]*model[ \t]+(\w+)[ \t]*\{", re.MULTILINE)
# A block closes on its own line; attribute arguments hold no braces.
_BLOCK_END_RE = re.compile(r"^[ \t]*\}", re.MULTILINE)
# Block attributes open their own line, so a commented-out one does not count.
_MAP_RE = re.compile(r"""^[ \t]*@@map\(\s*(?:name\s*:\s*)?"([^"]+)"\s*\)""", re.MULTILINE)
_SCHEMA_RE = re.compile(r"""^[ \t]*@@schema\(\s*"([^"]+)"\s*\)""", re.MULTILINE)


class PrismaDialect:
    name = "prisma"
    extensions = frozenset({".prisma"})

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        found: list[tuple[str, float, int]] = []
        for m in _MODEL_RE.finditer(content):
            end = _BLOCK_END_RE.search(content, m.end())
            body = content[m.end() : end.start() if end else len(content)]
            mapped = _MAP_RE.search(body)
            schema = _SCHEMA_RE.search(body)
            table = mapped.group(1) if mapped else m.group(1)
            if schema:
                table = f"{schema.group(1)}.{table}"
            found.append((table, EXPLICIT_CONFIDENCE, line_at(content, m.start(1))))
        return dedup_emit(ctx, self.name, found)
