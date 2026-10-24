"""GraphQL SDL provider dialect.

A ``type Query { bookById(id: ID!): Book }`` declares one provider per field of
a root type, ``graphql::Query.bookById``. ``extend type Query`` (federation,
schema stitching) adds to a root; ``schema { query: RootQuery }`` renames one.
A field starting with ``_`` (federation's ``_entities`` and ``_service``) is the
gateway's own and is not read.

The same ``.graphql`` file may also hold operations (a client's documents);
those are :mod:`.documents`'s, and a block whose header is not a ``type`` is
not read here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from repowise.core.workspace.origins import ContractOrigin

from ..base import line_at
from ..dialect import EVERY_FILE, build_contract
from ..langs import GRAPHQL
from .parse import (
    mask_document,
    schema_fields,
    schema_root_names,
    top_level_blocks,
    type_header,
)

if TYPE_CHECKING:
    from repowise.core.workspace.contracts import Contract

    from ..base import ScanContext


def graphql_id(root: str, field: str) -> str:
    """The contract id of root field *field* on *root*: ``graphql::Query.bookById``."""
    return f"graphql::{root}.{field}"


class SchemaDialect:
    name = "graphql-sdl"
    extensions = GRAPHQL
    requires = EVERY_FILE

    def extract(self, ctx: ScanContext) -> list[Contract]:
        doc = mask_document(ctx.content)
        blocks = top_level_blocks(doc)
        roots = schema_root_names(doc, blocks)
        out: list[Contract] = []
        for block in blocks:
            type_name = type_header(block.header)
            root = roots.get(type_name) if type_name else None
            if root is None:
                continue
            for field in schema_fields(doc, block):
                if field.name.startswith("_"):
                    continue
                out.append(
                    build_contract(
                        ctx,
                        contract_type="graphql",
                        contract_id=graphql_id(root, field.name),
                        role="provider",
                        symbol_name=f"{root}.{field.name}",
                        origin=ContractOrigin.IDL,
                        line=line_at(ctx.content, field.offset),
                        meta={"root": root, "field": field.name, "source": "sdl"},
                    )
                )
        return out
