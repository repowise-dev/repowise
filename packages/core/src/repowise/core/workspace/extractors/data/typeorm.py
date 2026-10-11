"""TypeORM entities: ``@Entity('users')``, ``@Entity({ name, schema })`` or ``@Entity()``.

With no name the table is TypeORM's default naming, its ``snakeCase`` of the
class name (``PhotoMetadata`` -> ``photo_metadata``, ``APIKey`` -> ``api_key``).
A ``schema`` option qualifies it; one this cannot read refuses the entity.
``@ViewEntity`` is a view and ``@ChildEntity`` shares its parent's table, so
neither is read.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..calls import Call, call_sites, decorated_member
from ..langs import JS_TS
from ..strings import Arg, on_comment_line
from .dialect import CONVENTION_CONFIDENCE, EXPLICIT_CONFIDENCE, dedup_emit
from .names import snake_case

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_ENTITY = Call(re.compile(r"@Entity\s*\("), JS_TS, ("typeorm",))
_NAME = Arg(keys=("name",), pos=0)
_OPTION_NAME = Arg(keys=("name",))
_SCHEMA = Arg(keys=("schema",))
# TypeORM splits an acronym from the word after it before the camel case split.
_ACRONYM_RE = re.compile(r"([A-Z])([A-Z])([a-z])")


def _default_table(cls: str) -> str:
    return snake_case(_ACRONYM_RE.sub(r"\1_\2\3", cls))


class TypeOrmDialect:
    name = "typeorm"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if "@Entity" not in content:
            return []
        found: list[tuple[str, float, int]] = []
        for _shape, args, m, strings in call_sites(ctx, (_ENTITY,), empty=True):
            member = decorated_member(content, m.start())
            if member is None or member.group("cls") is None or on_comment_line(content, m.start()):
                continue
            # `@Entity('users', { schema })` or `@Entity({ name, schema })`.
            options = args[:1] if args[:1] and args[0].startswith("{") else args[1:2]
            names, refused = strings.resolve(args[:1], _OPTION_NAME if options == args[:1] else _NAME)
            schemas, schema_refused = strings.resolve(options, _SCHEMA)
            if refused or schema_refused:
                continue  # a name this file cannot read
            table = names[0] if names else _default_table(member.group("name"))
            if schemas:
                table = f"{schemas[0]}.{table}"
            confidence = EXPLICIT_CONFIDENCE if names else CONVENTION_CONFIDENCE
            found.append((table, confidence, line_at(content, m.start())))
        return dedup_emit(ctx, self.name, found)
