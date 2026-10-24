"""GraphQL contract extraction.

A GraphQL API is one ``POST /graphql``, so HTTP cannot say which field a client
uses; the contract is the root field, ``graphql::Query.bookById``. Providers are
the schema (SDL) and the resolvers that serve a root field; consumers are
operation documents (``.graphql`` files and ``gql`` templates). Both ends name
the same id, so the shared exact pass links them. :class:`GraphqlExtractor` is
the registry on the shared :class:`..dialect.DialectExtractor`.
"""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor, file_identity
from .documents import OperationFileDialect, TemplateDialect
from .resolvers import GraphqlJavaDialect, NestDialect, SpringDialect
from .schema import SchemaDialect

DIALECTS: tuple[ContractDialect, ...] = (
    SchemaDialect(),
    OperationFileDialect(),
    TemplateDialect(),
    SpringDialect(),
    GraphqlJavaDialect(),
    NestDialect(),
)


class GraphqlExtractor(DialectExtractor):
    """Extract GraphQL root-field contracts from schemas, resolvers and operations."""

    dialects = DIALECTS
    #: A field selected twice in one file, or declared by two resolvers, is one fact.
    identity = staticmethod(file_identity)


__all__ = ["DIALECTS", "GraphqlExtractor"]
