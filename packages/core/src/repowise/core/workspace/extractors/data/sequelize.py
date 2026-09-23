"""Sequelize models: ``sequelize.define``, ``Model.init`` and sequelize-typescript ``@Table``.

A ``tableName`` option names the table. Without one Sequelize derives it
from the model name (``define``'s first argument, ``modelName``, else the
class): kept as is under ``freezeTableName``, otherwise pluralized (a name
already ending in ``s`` is left alone) and snake-cased when ``underscored``
is set. ``define`` counts only on a Sequelize connection: a receiver named
for it, or one the file binds to ``new Sequelize(...)``.

Ceiling: a connection-wide ``define: { freezeTableName: true }`` set in
another file is not seen, so those models read as pluralized.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..base import ScanContext, line_at
from ..calls import Call, call_sites, decorated_member, file_strings
from ..langs import JS_TS
from ..strings import Arg, call_arguments, match_paren, on_comment_line, select_argument
from .dialect import CONVENTION_CONFIDENCE, EXPLICIT_CONFIDENCE, dedup_emit
from .names import pluralize, snake_case

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..calls import FileStrings

_SEQUELIZE = ("sequelize",)
_DEFINE = Call(re.compile(r"\.\s*define\s*\("), JS_TS, _SEQUELIZE)
# `User.init(attributes, { sequelize, ... })`: the options name the connection.
_INIT = Call(re.compile(r"\.\s*init\s*\("), JS_TS, _SEQUELIZE)
# The receiver a `.define(` / `.init(` is called on, read back from the dot.
_RECEIVER_RE = re.compile(r"(?<![\w$.])(?:this\s*\.\s*)?(?P<name>[A-Za-z_$][\w$]*)\s*$")
_CONNECTION_RE = re.compile(r"(?<![\w$])sequelize(?![\w$])")
_BOUND_RE = re.compile(r"(?P<name>[A-Za-z_$][\w$]*)\s*=\s*new\s+Sequelize\s*\(")
_CLASS_RE = re.compile(r"\bclass\s+(?P<name>[A-Za-z_$][\w$]*)")
# sequelize-typescript: `@Table` bare or with options, above the model class.
_TABLE_DECORATOR_RE = re.compile(r"@Table\b\s*(?P<paren>\()?")
_FIRST = Arg(pos=0)


def _flag(options: list[str], key: str) -> bool:
    return select_argument(options, Arg(keys=(key,))) == ["true"]


def _table(model: str, options: list[str], strings: FileStrings) -> tuple[str, float] | None:
    """The table a model's options give it, with how sure; ``None`` when unreadable."""
    names, refused = strings.resolve(options, Arg(keys=("tableName",)))
    if refused:
        return None
    if names:
        return names[0], EXPLICIT_CONFIDENCE
    modelled, refused = strings.resolve(options, Arg(keys=("modelName",)))
    if refused:
        return None
    name = modelled[0] if modelled else model
    if not _flag(options, "freezeTableName"):
        if not name.endswith("s"):
            name = pluralize(name)
        if _flag(options, "underscored"):
            name = snake_case(name)
    return name, CONVENTION_CONFIDENCE


def _receiver(content: str, dot: int) -> str | None:
    m = _RECEIVER_RE.search(content, max(0, dot - 80), dot)
    return m.group("name") if m else None


class SequelizeDialect:
    name = "sequelize"
    extensions = JS_TS

    def extract(self, ctx: ScanContext) -> list[Contract]:
        content = ctx.content
        if _SEQUELIZE[0] not in content:
            return []
        connections = {"sequelize", *(m.group("name") for m in _BOUND_RE.finditer(content))}
        found: list[tuple[str, float, int]] = []
        for call, args, m, strings in call_sites(ctx, (_DEFINE, _INIT)):
            receiver = _receiver(content, m.start())
            if receiver is None:
                continue
            if call is _DEFINE:
                if receiver not in connections:
                    continue  # `customElements.define`, an i18n `define`
                models, refused = strings.resolve(args[:1], _FIRST)
                if refused or not models:
                    continue
                table = _table(models[0], args[2:3], strings)
            else:
                if len(args) < 2 or not _CONNECTION_RE.search(args[1]):
                    continue  # some other `init`
                if receiver in ("super", "this"):
                    # `static init(s) { return super.init(attrs, { sequelize: s }) }`
                    classes = list(_CLASS_RE.finditer(content, 0, m.start()))
                    if not classes:
                        continue
                    receiver = classes[-1].group("name")
                table = _table(receiver, args[1:2], strings)
            if table is not None:
                found.append((*table, line_at(content, m.start())))
        if "sequelize-typescript" in content:
            found.extend(self._decorated(ctx))
        return dedup_emit(ctx, self.name, found)

    def _decorated(self, ctx: ScanContext) -> list[tuple[str, float, int]]:
        content = ctx.content
        strings = file_strings(ctx)
        out: list[tuple[str, float, int]] = []
        for m in _TABLE_DECORATOR_RE.finditer(content):
            member = decorated_member(content, m.start())
            if member is None or member.group("cls") is None or strings is None:
                continue
            if on_comment_line(content, m.start()):
                continue
            options: list[str] = []
            if m.group("paren"):
                close = match_paren(content, m.start("paren"))
                options = (call_arguments(content, m.start("paren"), close) or [])[:1] if close >= 0 else []
            table = _table(member.group("name"), options, strings)
            if table is not None:
                out.append((*table, line_at(content, m.start())))
        return out
