"""GraphQL contracts: a schema, its resolvers and the documents that call them.

The contract is the root field, ``graphql::Query.bookById``, because every
GraphQL request is one ``POST /graphql`` that HTTP cannot tell apart.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.contracts import normalize_contract_id
from repowise.core.workspace.extractors import GraphqlExtractor
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.origins import ContractOrigin, LinkStrategy


def _extract(tmp_path: Path, files: dict[str, str], alias: str = "svc"):
    for rel, content in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return GraphqlExtractor().extract(tmp_path, alias)


def _ids(contracts, role: str) -> list[str]:
    return sorted(c.contract_id for c in contracts if c.role == role)


SCHEMA = """\
# The library.
type Query {
  "Find a book: by id"
  bookById(id: ID!): Book
  books(first: Int = 10, filter: Filter = {a: 1}): [Book!]! @deprecated(reason: "use search: x")
  # oldBooks: [Book]
  _service: String
}

extend type Query {
  search(text: String!): [Book!]!
}

type Mutation { addBook(title: String!): Book }

type Book {
  id: ID!
  author: Author
}
"""


class TestSchema:
    def test_root_fields_are_providers(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {"schema.graphqls": SCHEMA})
        assert _ids(rows, "provider") == [
            "graphql::Mutation.addBook",
            "graphql::Query.bookById",
            "graphql::Query.books",
            "graphql::Query.search",
        ]
        assert {c.contract_type for c in rows} == {"graphql"}

    def test_origin_is_idl(self, tmp_path: Path) -> None:
        c = _extract(tmp_path, {"schema.graphql": "type Query { me: User }"})[0]
        assert c.origin == ContractOrigin.IDL
        assert c.meta == {"root": "Query", "field": "me", "source": "sdl"}

    def test_line_is_the_fields(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {"schema.graphqls": SCHEMA})
        by_id = {c.contract_id: c for c in rows}
        assert by_id["graphql::Query.bookById"].line == 4
        assert by_id["graphql::Mutation.addBook"].line == 14

    def test_a_field_of_another_type_is_not_a_contract(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"s.graphql": "type Book { author: Author }"}) == []

    def test_schema_block_renames_the_roots(self, tmp_path: Path) -> None:
        sdl = (
            "schema { query: RootQuery mutation: RootMutation }\n"
            "type RootQuery { ping: String }\ntype RootMutation { pong: String }\n"
            "type Query { ignored: String }"
        )
        rows = _extract(tmp_path, {"s.graphql": sdl})
        assert _ids(rows, "provider") == ["graphql::Mutation.pong", "graphql::Query.ping"]

    def test_commented_out_type_is_not_read(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"s.graphql": "# type Query { gone: Int }\n"}) == []

    def test_gql_extension(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {"s.gql": "type Subscription { tick: Int }"})
        assert _ids(rows, "provider") == ["graphql::Subscription.tick"]


class TestOperationFiles:
    def test_query_file_is_a_consumer(self, tmp_path: Path) -> None:
        doc = "query Book($id: ID!) {\n  bookById(id: $id) { title }\n}\n"
        c = _extract(tmp_path, {"ops/book.graphql": doc})[0]
        assert (c.role, c.contract_id, c.line) == ("consumer", "graphql::Query.bookById", 2)
        assert c.origin == ContractOrigin.CLIENT_CALL
        assert c.meta["operation"] == "Book"

    def test_shorthand_is_a_query_and_mutation_is_a_mutation(self, tmp_path: Path) -> None:
        rows = _extract(
            tmp_path,
            {"a.graphql": "{ me { id } }", "b.graphql": 'mutation { addBook(title: "x") { id } }'},
        )
        assert _ids(rows, "consumer") == ["graphql::Mutation.addBook", "graphql::Query.me"]

    def test_alias_arguments_fragments_and_introspection(self, tmp_path: Path) -> None:
        doc = (
            "fragment F on Book { id }\n"
            "query Q($n: Int = 3) {\n"
            "  first: books(first: $n) { ...F author { name } }\n"
            "  ... on Query { me { id } }\n"
            "  ...RootFragment\n"
            "  __typename\n"
            "}\n"
        )
        rows = _extract(tmp_path, {"q.graphql": doc})
        assert _ids(rows, "consumer") == ["graphql::Query.books", "graphql::Query.me"]

    def test_a_schema_file_is_no_consumer(self, tmp_path: Path) -> None:
        rows = _extract(tmp_path, {"schema.graphql": SCHEMA})
        assert _ids(rows, "consumer") == []

    def test_a_commented_out_operation_is_not_read(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"q.graphql": "# query { me { id } }\n"}) == []


class TestTemplates:
    def test_gql_tag_in_typescript(self, tmp_path: Path) -> None:
        ts = (
            "import { gql } from '@apollo/client';\n"
            "const FRAG = gql`fragment B on Book { id }`;\n"
            "export const Q = gql`\n"
            "  query Book($id: ID!) {\n"
            "    bookById(id: $id) { ...B }\n"
            "  }\n"
            "  ${FRAG}\n"
            "`;\n"
        )
        rows = _extract(tmp_path, {"src/books.ts": ts})
        assert [(c.contract_id, c.line, c.role) for c in rows] == [
            ("graphql::Query.bookById", 5, "consumer")
        ]

    def test_gql_call_and_graphql_tag(self, tmp_path: Path) -> None:
        ts = (
            "const a = gql(`mutation { addBook(title: 'x') { id } }`);\n"
            "const b = graphql`query { me { id } }`;\n"
        )
        rows = _extract(tmp_path, {"a.js": ts})
        assert _ids(rows, "consumer") == ["graphql::Mutation.addBook", "graphql::Query.me"]

    def test_a_commented_out_template_is_not_read(self, tmp_path: Path) -> None:
        ts = "// const Q = gql`query { oldBooks { id } }`;\nconst x = 1; // gql\n"
        assert _extract(tmp_path, {"a.ts": ts}) == []

    def test_graphql_js_server_call_is_not_a_client(self, tmp_path: Path) -> None:
        ts = "import { graphql } from 'graphql';\nawait graphql(schema, '{ hello }');\n"
        assert _extract(tmp_path, {"a.ts": ts}) == []

    def test_python_gql_client(self, tmp_path: Path) -> None:
        py = 'from gql import gql\nQ = gql("""\n  query { me { id } }\n""")\n'
        rows = _extract(tmp_path, {"client.py": py})
        assert [(c.contract_id, c.line) for c in rows] == [("graphql::Query.me", 3)]

    def test_a_file_without_a_template_is_not_read(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"a.ts": "const gqlish = 'query { me }';\n"}) == []

    def test_one_field_selected_twice_is_one_contract(self, tmp_path: Path) -> None:
        ts = "const a = gql`query { me { id } }`;\nconst b = gql`query { me { name } }`;\n"
        assert _ids(_extract(tmp_path, {"a.ts": ts}), "consumer") == ["graphql::Query.me"]


class TestResolvers:
    def test_spring_mappings(self, tmp_path: Path) -> None:
        java = """\
import org.springframework.graphql.data.method.annotation.*;
class C {
  @QueryMapping
  public Book bookById(@Argument String id) { return null; }
  @QueryMapping("catalog")
  public List<Book> list() { return null; }
  @SchemaMapping(typeName = "Query", field = "topBook")
  public Book top() { return null; }
  @SchemaMapping(typeName = "Book")
  public Author author(Book b) { return null; }
  @MutationMapping
  @PreAuthorize("hasRole('X')")
  public Book addBook() { return null; }
  // @QueryMapping
  // public Book commentedOut() { return null; }
}
"""
        rows = _extract(tmp_path, {"C.java": java})
        assert _ids(rows, "provider") == [
            "graphql::Mutation.addBook",
            "graphql::Query.bookById",
            "graphql::Query.catalog",
            "graphql::Query.topBook",
        ]
        assert {c.origin for c in rows} == {ContractOrigin.ROUTE_REGISTRATION}

    def test_spring_annotation_needs_the_spring_import(self, tmp_path: Path) -> None:
        assert _extract(tmp_path, {"C.java": "@QueryMapping\nBook b() {}"}) == []

    def test_spring_in_kotlin(self, tmp_path: Path) -> None:
        kt = (
            "import org.springframework.graphql.data.method.annotation.QueryMapping\n"
            "class C {\n  @QueryMapping\n  suspend fun bookById(id: String): Book? = null\n}\n"
        )
        assert _ids(_extract(tmp_path, {"C.kt": kt}), "provider") == ["graphql::Query.bookById"]

    def test_graphql_java_type_wiring(self, tmp_path: Path) -> None:
        java = """\
import graphql.schema.idl.RuntimeWiring;
import static graphql.schema.idl.TypeRuntimeWiring.newTypeWiring;
class F {
  RuntimeWiring w() {
    return RuntimeWiring.newRuntimeWiring()
      .type(newTypeWiring("Query").dataFetcher("bookById", f.byId()).dataFetcher("books", f.all()))
      .type("Mutation", b -> b.dataFetcher("addBook", f.add()))
      .type(newTypeWiring("Book").dataFetcher("author", f.author()))
      .build();
  }
}
"""
        rows = _extract(tmp_path, {"F.java": java})
        assert _ids(rows, "provider") == [
            "graphql::Mutation.addBook",
            "graphql::Query.bookById",
            "graphql::Query.books",
        ]

    def test_nest_graphql_resolver(self, tmp_path: Path) -> None:
        ts = """\
import { Resolver, Query, Mutation } from '@nestjs/graphql';
@Resolver()
export class BooksResolver {
  @Query(() => Book)
  bookById(@Args('id') id: string) {}

  @Query(returns => [Book], { name: 'catalog' })
  async list() {}

  @Mutation('addBook')
  add() {}
}
"""
        assert _ids(_extract(tmp_path, {"b.resolver.ts": ts}), "provider") == [
            "graphql::Mutation.addBook",
            "graphql::Query.bookById",
            "graphql::Query.catalog",
        ]

    def test_nest_http_query_parameter_is_not_a_resolver(self, tmp_path: Path) -> None:
        ts = (
            "import { Controller, Get, Query } from '@nestjs/common';\n"
            "import { X } from '@nestjs/graphql';\n"
            "@Controller() class C {\n"
            "  @Get() find(\n    @Query('page') page: string) {}\n}\n"
        )
        assert _extract(tmp_path, {"c.controller.ts": ts}) == []


class TestMatching:
    def test_schema_links_to_a_document_across_repos(self, tmp_path: Path) -> None:
        books = _extract(
            tmp_path / "books",
            {"schema.graphqls": "type Query { bookById(id: ID!): Book }"},
            "books",
        )
        web = _extract(
            tmp_path / "web",
            {"src/books.ts": "const Q = gql`query { bookById(id: 1) { title } }`;"},
            "web",
        )
        links = match_contracts(books + web)
        assert len(links) == 1
        link = links[0]
        assert link.contract_id == "graphql::Query.bookById"
        assert (link.provider_repo, link.consumer_repo) == ("books", "web")
        assert link.strategy == LinkStrategy.EXACT_ID
        assert link.match_type == "exact"

    def test_a_field_nobody_serves_links_to_nothing(self, tmp_path: Path) -> None:
        books = _extract(tmp_path / "books", {"s.graphql": "type Query { a: Int }"}, "books")
        web = _extract(tmp_path / "web", {"a.ts": "gql`query { b }`"}, "web")
        assert match_contracts(books + web) == []

    def test_field_case_is_kept_and_the_root_is_not(self) -> None:
        assert normalize_contract_id("graphql::Query.bookById") == "graphql::query.bookById"
        assert normalize_contract_id("graphql::QUERY.bookById") == "graphql::query.bookById"
        assert normalize_contract_id("graphql::Query.bookbyid") != normalize_contract_id(
            "graphql::Query.bookById"
        )

    def test_http_graphql_endpoint_is_still_http(self, tmp_path: Path) -> None:
        from repowise.core.workspace.extractors.http import HttpExtractor

        ts = "app.post('/graphql', handler);\n"
        (tmp_path / "server.js").write_text(ts, encoding="utf-8")
        assert {c.contract_id for c in HttpExtractor().extract(tmp_path, "api")} == {
            "http::POST::/graphql"
        }


@pytest.mark.parametrize("name", ["schema.graphql", "schema.graphqls", "schema.gql"])
def test_every_schema_extension_is_walked(tmp_path: Path, name: str) -> None:
    assert _ids(_extract(tmp_path, {name: "type Query { me: Int }"}), "provider") == [
        "graphql::Query.me"
    ]
