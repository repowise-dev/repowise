"""Consumer dialect: MyBatis XML mappers and array-form annotations."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import ScanContext, line_at
from ..langs import JAVA
from .dialect import build_table_consumer
from .sql_strings import SqlStringsDialect

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

_XML_GATES = ("mybatis-3-mapper", "<mapper")
_JAVA_GATES = ("@Select", "@Insert", "@Update", "@Delete")

# XML statement elements
_XML_STATEMENT_RE = re.compile(
    r"<(?P<tag>select|insert|update|delete)\b[^>]*>(?P<body>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)

# Dynamic tags, CDATA, comments and MyBatis params
_CDATA_RE = re.compile(r"<!\[CDATA\[(.*?)\]\]>", re.DOTALL)
_TAG_STRIP_RE = re.compile(r"<[^>]+>")
_PARAM_RE = re.compile(r"#\{[^}]*\}")

# Array-form MyBatis annotations: @Select({"...", "..."}), @Update({...}), etc.
_ARRAY_ANN_RE = re.compile(
    r"@(?P<ann>Select|Insert|Update|Delete)\s*\(\s*(?:value\s*=\s*)?\{(?P<items>.*?)\}\s*\)",
    re.IGNORECASE | re.DOTALL,
)
_STRING_LITERAL_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')


class MyBatisDialect:
    """Consumers from MyBatis XML mapper files and array-form Java annotations."""

    name = "mybatis"
    extensions = frozenset({".xml"}) | JAVA
    requires = _XML_GATES + _JAVA_GATES

    def extract(self, ctx: ScanContext) -> list[Contract]:
        if ctx.suffix == ".xml":
            return self._extract_xml(ctx)
        if ctx.suffix in JAVA:
            return self._extract_java(ctx)
        return []

    def _extract_xml(self, ctx: ScanContext) -> list[Contract]:
        if not (ctx.has_any((_XML_GATES[0],)) and ctx.has_any((_XML_GATES[1],))):
            return []
        out: list[Contract] = []
        sql_strings = SqlStringsDialect()
        seen: set[str] = set()

        for m in _XML_STATEMENT_RE.finditer(ctx.content):
            if ctx.in_comment(m.start()):
                continue
            body = m.group("body")
            body = _CDATA_RE.sub(r"\1", body)
            body = _TAG_STRIP_RE.sub(" ", body)
            body = _PARAM_RE.sub("?", body)
            line = line_at(ctx.content, m.start())
            tag_verb = m.group("tag").lower()

            for table_raw, verb in sql_strings._tables_in(body):
                key = table_raw.lower()
                if key in seen:
                    continue
                seen.add(key)
                contract = build_table_consumer(
                    ctx,
                    table_raw=table_raw,
                    verb=verb or tag_verb,
                    client=self.name,
                    line=line,
                    origin=ContractOrigin.SQL_STRING,
                )
                if contract is not None:
                    out.append(contract)
        return out

    def _extract_java(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        sql_strings = SqlStringsDialect()
        seen: set[str] = set()

        for m in _ARRAY_ANN_RE.finditer(ctx.content):
            if ctx.in_comment(m.start()):
                continue
            items = m.group("items")
            parts = _STRING_LITERAL_RE.findall(items)
            if not parts:
                continue
            query = " ".join(parts)
            query = _PARAM_RE.sub("?", query)
            line = line_at(ctx.content, m.start())
            ann_verb = m.group("ann").lower()

            for table_raw, verb in sql_strings._tables_in(query):
                key = table_raw.lower()
                if key in seen:
                    continue
                seen.add(key)
                contract = build_table_consumer(
                    ctx,
                    table_raw=table_raw,
                    verb=verb or ann_verb,
                    client=self.name,
                    line=line,
                    origin=ContractOrigin.SQL_STRING,
                )
                if contract is not None:
                    out.append(contract)
        return out
