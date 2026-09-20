"""Remix provider contracts, from the convention the graph already recognised.

The graph side knew a file under ``routes/`` was loaded without an import, and
stopped there: nothing read the name as a path or the exports as verbs, so a
Remix repo published no endpoints at all. The claims under test are that every
rule of the file-name grammar reaches the contract, that ``loader`` and
``action`` become the verbs Remix actually answers, that the graph consumer
still forms its edge from the same recognition, and that a ``routes/`` folder
belonging to some other framework stays out.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import networkx as nx
import pytest

from repowise.core.ingestion.framework_edges import add_framework_edges
from repowise.core.ingestion.framework_routes import (
    remix_route_file,
    remix_route_path,
    remix_route_verbs,
)
from repowise.core.ingestion.models import FileInfo
from repowise.core.ingestion.parser import ASTParser
from repowise.core.ingestion.resolvers.context import ResolverContext
from repowise.core.workspace.extractors.http.express import ExpressDialect
from repowise.core.workspace.extractors.http.next_app import NextAppDialect
from repowise.core.workspace.extractors.http.remix import RemixDialect
from repowise.core.workspace.extractors.http_extractor import HttpExtractor

NOTE_ROUTE_TSX = """\
import type { ActionFunctionArgs, LoaderFunctionArgs } from "@remix-run/node";
import { useLoaderData } from "@remix-run/react";

export const loader = async ({ params }: LoaderFunctionArgs) => {
  return json({ note: params.noteId });
};

export const action = async ({ request }: ActionFunctionArgs) => {
  return redirect("/notes");
};

export default function NoteDetail() {
  return useLoaderData<typeof loader>().note;
}
"""

HEALTHCHECK_TSX = """\
import type { LoaderFunctionArgs } from "@remix-run/node";

export async function loader({ request }: LoaderFunctionArgs) {
  return new Response("OK");
}
"""

EXPRESS_ROUTE_JS = """\
const express = require("express");
const router = express.Router();
router.get("/reports", listReports);
module.exports = router;
"""

NEXT_ROUTE_TS = """\
export async function GET(request: Request) {
  return Response.json({});
}
"""


class _Extractor(HttpExtractor):
    """The three filesystem-and-router dialects that could collide, together."""

    provider_dialects = (NextAppDialect(), ExpressDialect(), RemixDialect())
    consumer_dialects = ()


def _providers(repo: Path, alias: str = "web") -> dict[str, Any]:
    return {c.contract_id: c for c in _Extractor().extract(repo, alias) if c.role == "provider"}


def _write(repo: Path, rel: str, content: str) -> None:
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestRemixRecognition:
    @pytest.mark.parametrize(
        ("rel_path", "expected"),
        [
            # A dot separates segments and `$name` is a parameter.
            ("app/routes/notes.$noteId.tsx", "/notes/:noteId"),
            # `_index` is the index of what precedes it, and names nothing.
            ("app/routes/notes._index.tsx", "/notes"),
            ("app/routes/_index.tsx", "/"),
            # A leading underscore is a pathless layout.
            ("app/routes/_auth.login.tsx", "/login"),
            # A trailing underscore leaves a layout, not the URL.
            ("app/routes/app.projects_.$id.tsx", "/app/projects/:id"),
            # An optional segment, recorded in its present form.
            ("app/routes/($lang).about.tsx", "/:lang/about"),
            ("app/routes/(admin).users.tsx", "/admin/users"),
            # Brackets escape: the dot is served, not a separator.
            ("app/routes/sitemap[.]xml.tsx", "/sitemap.xml"),
            # The escape covers the bracketed characters and no more, so the
            # parameter beside one is still a parameter.
            ("app/routes/notes.$id[.]json.tsx", "/notes/:id.json"),
            # A bare `$` is the splat.
            ("app/routes/files.$.tsx", "/files/*"),
            # Directories separate segments exactly as dots do.
            ("app/routes/users/$id.tsx", "/users/:id"),
            ("app/routes/notes.$noteId/route.tsx", "/notes/:noteId"),
            ("routes/reports/index.jsx", "/reports"),
            # The app directory is configurable, `routes/` is not.
            ("src/app/routes/healthcheck.ts", "/healthcheck"),
        ],
    )
    def test_the_path_comes_from_the_file_name(self, rel_path: str, expected: str) -> None:
        assert remix_route_path(rel_path) == expected

    def test_only_files_under_routes_are_routes(self) -> None:
        assert remix_route_file("app/routes/notes.tsx")
        assert not remix_route_file("app/models/note.server.ts")
        assert not remix_route_file("app/routes/notes.test.tsx")
        assert not remix_route_file("app/routes/notes.spec.ts")
        assert not remix_route_file("app/routes/styles.css")
        assert remix_route_path("app/models/note.server.ts") is None

    @pytest.mark.parametrize(
        "source",
        [
            "export const loader = async () => null;\n",
            "export let loader = () => null;\n",
            "export function loader() {}\n",
            "export async function loader({ request }) {}\n",
        ],
    )
    def test_every_loader_spelling_answers_a_get(self, source: str) -> None:
        assert [verb for verb, _off in remix_route_verbs(source)] == ["GET"]

    def test_an_action_answers_an_unnamed_verb(self) -> None:
        # Remix routes every non-GET request to the one `action`, and the file
        # never says which of them it serves.
        assert [v for v, _off in remix_route_verbs(NOTE_ROUTE_TSX)] == ["GET", "*"]

    def test_an_export_that_does_not_open_its_line_is_not_a_handler(self) -> None:
        # A commented-out `export const loader` declares nothing, and reading
        # it publishes an endpoint the route does not answer.
        assert remix_route_verbs("// export const loader = () => null;\n") == []

    def test_the_exports_are_read_in_declaration_order(self) -> None:
        source = "export const action = () => null;\nexport const loader = () => null;\n"
        assert [v for v, _off in remix_route_verbs(source)] == ["*", "GET"]

    def test_an_offset_lands_on_its_own_export(self) -> None:
        offsets = [off for _v, off in remix_route_verbs(NOTE_ROUTE_TSX)]
        assert [NOTE_ROUTE_TSX[o:].startswith("export") for o in offsets] == [True, True]


class TestRemixContracts:
    def test_a_loader_and_an_action_are_two_contracts(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/notes.$noteId.tsx", NOTE_ROUTE_TSX)
        ids = _providers(tmp_path)
        assert set(ids) == {"http::GET::/notes/{param}", "http::*::/notes/{param}"}
        assert ids["http::GET::/notes/{param}"].meta["framework"] == "remix"

    def test_each_verb_names_the_export_it_came_from(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/notes.$noteId.tsx", NOTE_ROUTE_TSX)
        ids = _providers(tmp_path)
        assert ids["http::GET::/notes/{param}"].meta["handler"] == "loader"
        assert ids["http::*::/notes/{param}"].meta["handler"] == "action"

    def test_a_contract_points_at_its_own_export(self, tmp_path: Path) -> None:
        # Both exports live in one file, so the line is the only thing that can
        # tell the two contracts apart when they are bound to symbols.
        _write(tmp_path, "app/routes/notes.$noteId.tsx", NOTE_ROUTE_TSX)
        ids = _providers(tmp_path)
        assert ids["http::GET::/notes/{param}"].line == 4
        assert ids["http::*::/notes/{param}"].line == 8

    def test_a_route_with_only_a_loader_is_one_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/healthcheck.tsx", HEALTHCHECK_TSX)
        assert set(_providers(tmp_path)) == {"http::GET::/healthcheck"}

    def test_a_nested_route_directory_reaches_the_contract(self, tmp_path: Path) -> None:
        # Directories separate segments exactly as dots do, and the flat form
        # is already covered, so this is the other half of the same grammar.
        _write(tmp_path, "app/routes/users/$id.tsx", HEALTHCHECK_TSX)
        assert set(_providers(tmp_path)) == {"http::GET::/users/{param}"}

    def test_the_file_name_grammar_reaches_the_contract(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/_auth.($lang).users.$id.tsx", HEALTHCHECK_TSX)
        assert set(_providers(tmp_path)) == {"http::GET::/{param}/users/{param}"}


class TestRemixGraphConsumer:
    def test_a_route_file_still_links_to_its_helper(self, tmp_path: Path) -> None:
        # The convention test moved onto the shared recogniser; the edge that
        # gives an imported helper its reachability has to survive the move.
        _write(tmp_path, "app/utils/auth.ts", "export const auth = {};\n")
        _write(
            tmp_path,
            "app/routes/notes.$noteId.tsx",
            "import { auth } from '../utils/auth';\nexport function loader() { return auth; }\n",
        )
        graph = _graph(tmp_path, "*.ts*", "typescript", ["remix"])
        assert graph.has_edge("app/routes/notes.$noteId.tsx", "app/utils/auth.ts")


class TestRemixRefusals:
    def test_an_express_routes_folder_yields_no_remix_row(self, tmp_path: Path) -> None:
        # `routes/` is an ordinary directory name, so without the framework
        # import every Express router file gained a fabricated file-name route.
        _write(tmp_path, "routes/reports.js", EXPRESS_ROUTE_JS)
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"express"}

    def test_a_next_route_is_not_a_remix_route(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/api/posts/route.ts", NEXT_ROUTE_TS)
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"next-app"}

    def test_a_remix_route_is_not_a_next_route(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/healthcheck.tsx", HEALTHCHECK_TSX)
        frameworks = {c.meta["framework"] for c in _providers(tmp_path).values()}
        assert frameworks == {"remix"}

    def test_a_test_beside_a_route_is_not_a_route(self, tmp_path: Path) -> None:
        _write(tmp_path, "app/routes/notes.test.tsx", NOTE_ROUTE_TSX)
        assert _providers(tmp_path) == {}

    def test_a_route_file_exporting_no_handler_serves_no_endpoint(self, tmp_path: Path) -> None:
        # A page renders through its parent's loader; it publishes no endpoint
        # of its own.
        _write(
            tmp_path,
            "app/routes/about.tsx",
            'import { Link } from "@remix-run/react";\nexport default function About() {}\n',
        )
        assert _providers(tmp_path) == {}

    def test_a_client_side_router_publishes_no_endpoint(self, tmp_path: Path) -> None:
        # `react-router-dom` is the single-page router: its `routes/` folder
        # holds components, and the app it belongs to serves nothing.
        _write(
            tmp_path,
            "src/routes/notes.tsx",
            'import { useParams } from "react-router-dom";\nexport const loader = () => null;\n',
        )
        assert _providers(tmp_path) == {}

    def test_the_root_route_is_not_a_contract(self, tmp_path: Path) -> None:
        # A stated ceiling: a path that normalises to bare `/` carries nothing
        # to match on, and the shared builder drops it for every framework.
        _write(tmp_path, "app/routes/_index.tsx", HEALTHCHECK_TSX)
        assert _providers(tmp_path) == {}


# ---------------------------------------------------------------------------
# Fixtures built from real parser output, never hand-written ids
# ---------------------------------------------------------------------------


def _parse_repo(repo: Path, glob: str, language: str) -> dict[str, Any]:
    parser = ASTParser()
    out: dict[str, Any] = {}
    for src in repo.rglob(glob):
        rel = src.resolve().relative_to(repo.resolve()).as_posix()
        fi = FileInfo(
            path=rel,
            abs_path=str(src.resolve()),
            language=language,
            size_bytes=src.stat().st_size,
            git_hash="",
            last_modified=datetime.now(),
            is_test=False,
            is_config=False,
            is_api_contract=False,
            is_entry_point=False,
        )
        out[rel] = parser.parse_file(fi, src.read_bytes())
    return out


def _graph(repo: Path, glob: str, language: str, stack: list[str]) -> nx.DiGraph:
    parsed = _parse_repo(repo, glob, language)
    graph = nx.DiGraph()
    for path in parsed:
        graph.add_node(path)
    ctx = ResolverContext(
        path_set=set(parsed),
        stem_map={Path(p).stem.lower(): [p] for p in parsed},
        graph=graph,
        repo_path=repo,
    )
    add_framework_edges(graph, parsed, ctx, stack)
    return graph
