"""GraphQL resolver dialects: code that serves a root field.

Each reads the field a server registers, as a provider at route-registration
confidence. Only root fields (``Query``, ``Mutation``, ``Subscription``) are
contracts; a field of ``Book`` is the server's business, not a client's.

* Spring for GraphQL: ``@QueryMapping``, ``@MutationMapping``,
  ``@SubscriptionMapping`` and ``@SchemaMapping(typeName = "Query", ...)``.
* graphql-java: ``newTypeWiring("Query").dataFetcher("bookById", ...)`` and
  ``.type("Query", b -> b.dataFetcher(...))``.
* NestJS: ``@Query()``, ``@Mutation()``, ``@Subscription()`` of
  ``@nestjs/graphql``, in a file that imports it (``@Query('id')`` of
  ``@nestjs/common`` is an HTTP parameter).

Not read yet: Apollo resolver maps, HotChocolate, gqlgen (whose schema is the
SDL, read by :mod:`.schema`).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..calls import Call, call_chain, call_sites, code_matches, decorated_member, file_strings
from ..dialect import build_contract
from ..langs import JAVA, JS_TS, KOTLIN
from ..strings import match_paren
from .parse import ROOT_TYPES
from .schema import graphql_id

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext

_ROOTS = "|".join(ROOT_TYPES)


def _provider(ctx: ScanContext, root: str, field: str, offset: int, source: str) -> Contract:
    return build_contract(
        ctx,
        contract_type="graphql",
        contract_id=graphql_id(root, field),
        role="provider",
        symbol_name=f"{root}.{field}",
        origin=ContractOrigin.ROUTE_REGISTRATION,
        line=line_at(ctx.content, offset),
        meta={"root": root, "field": field, "source": source},
    )


# ---------------------------------------------------------------------------
# Spring for GraphQL
# ---------------------------------------------------------------------------

_SPRING_MAPPING = re.compile(rf"@(?P<kind>{_ROOTS}|Schema)Mapping\b(?:\s*\((?P<args>[^)]*)\))?")
# What follows an annotation: further annotations, modifiers and the return
# type, then the method's name at its opening parenthesis (`fun`, in Kotlin).
_ANNOTATION_ARGS = r"\((?:[^()]|\([^()]*\))*\)"  # one level of nested parentheses
_METHOD_NAME = re.compile(
    rf"(?:\s*@[\w.]+(?:\s*{_ANNOTATION_ARGS})?)*[^;{{}}()@]*?\b(?P<name>[A-Za-z_]\w*)\s*\("
)
_STRING = re.compile(r"\"([^\"]*)\"")


def _attribute(args: str, *keys: str) -> str | None:
    """The string a ``key = "value"`` attribute holds, for the first of *keys* present."""
    for key in keys:
        m = re.search(rf"\b{key}\s*=\s*\"([^\"]*)\"", args)
        if m:
            return m.group(1)
    return None


def _spring_field(args: str, has_type: bool, method_end: int, content: str) -> str | None:
    named = _attribute(args, "field", "name", "value")
    if named:
        return named
    positional = _STRING.match(args.strip()) if not has_type else None
    if positional and positional.group(1):
        return positional.group(1)
    m = _METHOD_NAME.match(content, method_end)
    return m.group("name") if m else None


class SpringDialect:
    name = "spring-graphql"
    extensions = JAVA | KOTLIN
    #: The package every Spring GraphQL annotation is imported from.
    requires = ("graphql.data.method.annotation",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        for m in code_matches(ctx, _SPRING_MAPPING):
            args = m.group("args") or ""
            if m.group("kind") == "Schema":
                root = _attribute(args, "typeName")
                if root not in ROOT_TYPES:
                    continue  # a field of another type, or a type read from the parameter
            else:
                root = m.group("kind")
            field = _spring_field(args, m.group("kind") == "Schema", m.end(), ctx.content)
            if field:
                out.append(_provider(ctx, root, field, m.start(), "spring"))
        return out


# ---------------------------------------------------------------------------
# graphql-java
# ---------------------------------------------------------------------------

_TYPE_WIRING = (Call(head=re.compile(r"\bnewTypeWiring\s*\("), extensions=JAVA | KOTLIN),)
_TYPE_LAMBDA = re.compile(rf"\.type\s*\(\s*\"(?P<root>{_ROOTS})\"\s*,\s*\(?\s*\w+\s*\)?\s*->\s*\w+")


class GraphqlJavaDialect:
    name = "graphql-java"
    extensions = JAVA | KOTLIN
    requires = ("graphql.schema.idl",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        content = ctx.content
        for _row, args, m, strings in call_sites(ctx, _TYPE_WIRING):
            root = strings.text(args[0]) if args else None
            if root not in ROOT_TYPES:
                continue
            close = match_paren(content, m.end() - 1)
            out.extend(self._fetchers(ctx, strings, root, close))
        strings = None
        for m in code_matches(ctx, _TYPE_LAMBDA):
            strings = strings or file_strings(ctx)
            if strings is not None:
                out.extend(self._fetchers(ctx, strings, m.group("root"), m.end() - 1))
        return out

    @staticmethod
    def _fetchers(ctx: ScanContext, strings, root: str, close: int) -> list[Contract]:
        """The ``dataFetcher("name", ...)`` calls chained after *close*."""
        found: list[Contract] = []
        for link in call_chain(ctx.content, close):
            if link.name != "dataFetcher" or not link.args:
                continue
            field = strings.text(link.args[0])
            if field:
                found.append(_provider(ctx, root, field, link.offset, "graphql-java"))
        return found


# ---------------------------------------------------------------------------
# NestJS
# ---------------------------------------------------------------------------

_NEST_DECORATOR = re.compile(rf"@(?P<root>{_ROOTS})\s*\(")
_NAME_OPTION = re.compile(r"\bname\s*:\s*(['\"`])(?P<name>[^'\"`]+)\1")


def _starts_line(content: str, offset: int) -> bool:
    """Whether only whitespace precedes *offset* on its line: a method decorator, not a parameter's."""
    return not content[content.rfind("\n", 0, offset) + 1 : offset].strip()


def _is_method(content: str, name_end: int) -> bool:
    """Whether the member whose name ends at *name_end* is a method (its parameters follow)."""
    return content[name_end : name_end + 40].lstrip().startswith(("(", "<"))


class NestDialect:
    name = "nestjs-graphql"
    extensions = JS_TS
    requires = ("@nestjs/graphql",)

    def extract(self, ctx: ScanContext) -> list[Contract]:
        out: list[Contract] = []
        content = ctx.content
        for m in code_matches(ctx, _NEST_DECORATOR):
            if not _starts_line(content, m.start()):
                continue
            close = match_paren(content, m.end() - 1)
            if close < 0:
                continue
            argtext = content[m.end() : close]
            field = self._field(argtext)
            member = decorated_member(content, close + 1)
            # A method is followed by its parameters; a parameter (`@Query('p') p: string`,
            # on a line of its own once formatted) by a type.
            if member is None or member.group("cls") or not _is_method(content, member.end()):
                continue
            if field is None:
                field = member.group("name")
            if field:
                out.append(_provider(ctx, m.group("root"), field, m.start(), "nestjs"))
        return out

    @staticmethod
    def _field(argtext: str) -> str | None:
        """The name a ``{ name: 'x' }`` option or a schema-first ``'x'`` argument gives."""
        named = _NAME_OPTION.search(argtext)
        if named:
            return named.group("name")
        first = re.match(r"\s*(['\"])(?P<name>\w+)\1\s*(?:,|$)", argtext)
        return first.group("name") if first else None
