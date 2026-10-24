"""GraphQL operation documents: the consumer side.

A client names the root field it uses inside a document, never in the URL (every
request is one ``POST /graphql``), so the contract is the root field:
``query { bookById(id: 1) { title } }`` reads ``graphql::Query.bookById``.

Documents come from two places:

* ``.graphql`` / ``.gql`` operation files (Apollo Kotlin, codegen inputs);
* ``gql`` / ``graphql`` template literals in JavaScript and TypeScript, and
  ``gql("...")`` in Python.

Ceiling: a ``...Fragment`` spread at the root, or a document assembled at run
time, names fields this scan cannot see; those are missed rather than guessed.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import code_matches
from ..dialect import EVERY_FILE, build_contract
from ..langs import GRAPHQL, JS_TS, PYTHON
from .parse import (
    INTROSPECTION,
    mask_document,
    operation_kind,
    operation_name,
    selected_fields,
    top_level_blocks,
)
from .schema import graphql_id

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def document_contracts(ctx: ScanContext, text: str, base: int, source: str) -> list[Contract]:
    """The consumer contracts of the operations in *text*, which starts at *base* in the file.

    *text* is the document with offsets kept, so a field's line is the line of
    the file it sits on.
    """
    doc = mask_document(text)
    out: list[Contract] = []
    for block in top_level_blocks(doc):
        root = operation_kind(block.header)
        if root is None:
            continue
        operation = operation_name(block.header)
        for field in selected_fields(doc, block.start, block.end):
            if field.name in INTROSPECTION:
                continue
            meta = {"root": root, "field": field.name, "source": source}
            if operation:
                meta["operation"] = operation
            out.append(
                build_contract(
                    ctx,
                    contract_type="graphql",
                    contract_id=graphql_id(root, field.name),
                    role="consumer",
                    symbol_name=f"{root}.{field.name}",
                    origin=ContractOrigin.CLIENT_CALL,
                    line=line_at(ctx.content, base + field.offset),
                    meta=meta,
                )
            )
    return out


class OperationFileDialect:
    """``.graphql`` / ``.gql`` files holding operations (``query``, ``mutation``, ``subscription``)."""

    name = "graphql-operation-file"
    extensions = GRAPHQL
    requires = EVERY_FILE

    def extract(self, ctx: ScanContext) -> list[Contract]:
        return document_contracts(ctx, ctx.content, 0, "operation-file")


# `gql`...``, `gql(`...`)`, `graphql(`...`)`: a tagged or called template. Not
# `graphql(schema, "...")`, graphql-js running a query on the server, whose
# first argument is no template.
_TEMPLATE_HEAD = re.compile(r"(?<![\w$.])(?:gql|graphql)\s*(?:\(\s*)?`")
# `gql("""...""")` and `gql('...')` in Python's gql client.
_PY_HEAD = re.compile(r"(?<![\w$.])gql\s*\(\s*r?(\"\"\"|'''|\"|')")


def _blank(text: str) -> str:
    """*text* with everything but newlines replaced by spaces, so offsets hold."""
    return re.sub(r"[^\n]", " ", text)


def template_body(content: str, start: int) -> tuple[str, int] | None:
    """The text of the template literal whose body starts at *start*, and where it ends.

    An interpolated ``${...}`` (a fragment spliced in) is blanked, since what
    it holds is code, not GraphQL.
    """
    out: list[str] = []
    i = start
    n = len(content)
    while i < n:
        ch = content[i]
        if ch == "\\":
            out.append("  ")
            i += 2
            continue
        if ch == "`":
            return "".join(out), i
        if content.startswith("${", i):
            depth = 0
            j = i + 1
            while j < n:
                if content[j] == "{":
                    depth += 1
                elif content[j] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                j += 1
            out.append(_blank(content[i : j + 1]))
            i = j + 1
            continue
        out.append(ch)
        i += 1
    return None


def python_body(content: str, start: int, quote: str) -> str | None:
    """The text of the Python string literal whose body starts at *start*."""
    end = content.find(quote, start)
    while end > start and content[end - 1] == "\\":
        end = content.find(quote, end + 1)
    if end < 0:
        return None
    body = content[start:end]
    return body if len(quote) == 3 or "\n" not in body else None


class TemplateDialect:
    """``gql`...``` templates in JS / TS and ``gql("...")`` strings in Python."""

    name = "graphql-template"
    extensions = JS_TS | PYTHON
    requires = ("gql", "graphql")

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        if ctx.suffix in JS_TS:
            for m in code_matches(ctx, _TEMPLATE_HEAD):
                found = template_body(ctx.content, m.end())
                if found is not None:
                    out.extend(document_contracts(ctx, found[0], m.end(), "template"))
        else:
            for m in code_matches(ctx, _PY_HEAD):
                body = python_body(ctx.content, m.end(), m.group(1))
                if body is not None:
                    out.extend(document_contracts(ctx, body, m.end(), "template"))
        return out
