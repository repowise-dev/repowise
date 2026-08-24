"""Index-backed contract extraction: routes read at indexed handler symbols.

Three claims are under test here. The first is that anchoring a route to the
declaration directly above an indexed symbol removes a class of defect the
regex could only chase: a route-shaped string in a comment or a docstring is
not a declaration above a handler, and an empty path is just a string argument,
so neither can fabricate or lose a contract.

The second is the inherited-framework claim. Ingestion resolves Flask as well
as FastAPI, while the workspace regex layer was FastAPI-only. Flask arriving
here without a new dialect module is the evidence that the rest of the
framework list comes with the layer rather than being hand-written per
framework.

The third is that the substrate is the repo's ``wiki.db``. The parse cache this
module used to read loaded zero of three repos on a live workspace, so the
end-to-end class below writes real rows and reads them back through the real
query path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo, Symbol
from repowise.core.ingestion.parser import ASTParser
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.from_index import (
    EXTRACTION_LAYER_KEY,
    LAYER_INDEX,
    extract_http_providers,
    symbols_for_content,
)
from repowise.core.workspace.repo_index import IndexedSymbol

from ._repo_index import make_repo_index

REL_PATH = "app/routers/chat.py"


def _case(
    prelude: str, *handlers: tuple[str, list[str]]
) -> tuple[ScanContext, list[IndexedSymbol], list[str]]:
    """Assemble file text plus the symbol rows the index would hold for it.

    Each handler contributes its decorator lines and a two-line ``def``. The
    symbol's ``start_line`` is the ``def`` line, which is where the parser puts
    it — decorators live above the span, never inside it.
    """
    lines = prelude.split("\n")
    symbols: list[IndexedSymbol] = []
    for name, decorators in handlers:
        lines.append("")
        lines.extend(decorators)
        start = len(lines) + 1
        lines.append(f"async def {name}() -> None:")
        lines.append("    ...")
        symbols.append(
            IndexedSymbol(
                symbol_id=f"{REL_PATH}::{name}",
                name=name,
                qualified_name=name,
                kind="function",
                signature=f"def {name}()",
                file_path=REL_PATH,
                start_line=start,
                end_line=start + 1,
                visibility="public",
            )
        )
    content = "\n".join(lines)
    ctx = ScanContext("backend", REL_PATH, ".py", content)
    return ctx, symbols, lines


def _extract(prelude: str, *handlers: tuple[str, list[str]]):
    ctx, symbols, lines = _case(prelude, *handlers)
    return extract_http_providers(ctx, symbols, lines)


def _ids(contracts) -> set[str]:
    return {c.contract_id for c in contracts}


class TestFastApiFromDecorators:
    PRELUDE = 'router = APIRouter(prefix="/snapshots/{snapshot_id}/chat")'

    def test_empty_path_route_resolves_to_the_prefix(self) -> None:
        ids = _ids(_extract(self.PRELUDE, ("chat_message", ['@router.post("")'])))
        assert ids == {"http::POST::/snapshots/{param}/chat"}

    def test_provenance_is_recorded(self) -> None:
        contract = _extract(self.PRELUDE, ("chat_message", ['@router.post("")']))[0]
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_INDEX
        assert contract.meta["handler"] == "chat_message"

    def test_layer_key_does_not_collide_with_grpc_dialect_provenance(self) -> None:
        # The gRPC dialects already own meta["source"] for dialect provenance
        # ("py_servicer", "go_client", "proto"); the layer must not overwrite
        # it or the coverage metric mis-partitions every gRPC contract.
        assert EXTRACTION_LAYER_KEY != "source"
        contract = _extract(self.PRELUDE, ("chat", ['@router.post("")']))[0]
        assert "source" not in contract.meta

    def test_a_multi_line_decorator_is_read_whole(self) -> None:
        ids = _ids(
            _extract(
                self.PRELUDE,
                (
                    "chat",
                    ["@router.post(", '    "/messages",', "    status_code=201,", ")"],
                ),
            )
        )
        assert ids == {"http::POST::/snapshots/{param}/chat/messages"}

    def test_a_stacked_decorator_does_not_hide_the_route(self) -> None:
        ids = _ids(
            _extract(
                self.PRELUDE,
                ("chat", ['@limiter.limit("120/minute")', '@router.get("/history")']),
            )
        )
        assert ids == {"http::GET::/snapshots/{param}/chat/history"}

    def test_non_route_decorators_are_ignored(self) -> None:
        assert (
            _extract(
                self.PRELUDE,
                ("chat", ['@limiter.limit("240/minute")', "@dataclass", "@classmethod"]),
            )
            == []
        )

    def test_unknown_router_variable_is_not_a_route(self) -> None:
        assert _extract(self.PRELUDE, ("cached", ['@cache.get("/x")'])) == []

    def test_the_same_route_twice_yields_one_contract(self) -> None:
        contracts = _extract(
            self.PRELUDE,
            ("chat", ['@router.post("")']),
            ("chat_again", ['@router.post("")']),
        )
        assert len(contracts) == 1

    def test_a_decorated_class_does_not_lend_its_route_to_a_method(self) -> None:
        # PEP 8 asks for a blank line here and nothing enforces one. Without a
        # stop at ordinary code the walk climbs past ``class Foo:`` and binds
        # the class's route to the first method under it.
        content = '\n'.join(
            [
                'app = Flask(__name__)',
                '@app.route("/items")',
                "class Foo:",
                "    def get(self) -> None:",
                "        ...",
            ]
        )
        ctx = ScanContext("backend", REL_PATH, ".py", content)
        method = IndexedSymbol(
            symbol_id=f"{REL_PATH}::Foo::get",
            name="get",
            qualified_name="Foo.get",
            kind="method",
            signature="def get(self)",
            file_path=REL_PATH,
            start_line=4,
            end_line=5,
            visibility="public",
        )
        assert extract_http_providers(ctx, [method], content.split("\n")) == []

    def test_a_neighbouring_handler_does_not_lend_its_route(self) -> None:
        content = '\n'.join(
            [
                'router = APIRouter()',
                '@router.get("/first")',
                "async def first() -> None:",
                "    ...",
                "async def second() -> None:",
                "    ...",
            ]
        )
        ctx = ScanContext("backend", REL_PATH, ".py", content)
        second = IndexedSymbol(
            symbol_id=f"{REL_PATH}::second",
            name="second",
            qualified_name="second",
            kind="function",
            signature="def second()",
            file_path=REL_PATH,
            start_line=5,
            end_line=6,
            visibility="public",
        )
        assert extract_http_providers(ctx, [second], content.split("\n")) == []

    def test_a_non_handler_symbol_carries_no_route(self) -> None:
        # A route decorator can only sit above a callable; a module constant
        # that happens to follow one must not become a contract.
        ctx, symbols, lines = _case(self.PRELUDE, ("chat", ['@router.post("/x")']))
        constants = [
            IndexedSymbol(
                symbol_id=f"{REL_PATH}::EXAMPLE",
                name="EXAMPLE",
                qualified_name="EXAMPLE",
                kind="constant",
                signature="",
                file_path=REL_PATH,
                start_line=symbols[0].start_line,
                end_line=symbols[0].start_line,
                visibility="public",
            )
        ]
        assert extract_http_providers(ctx, constants, lines) == []


class TestCommentsCannotFabricateRoutes:
    """The self-extraction defect, structurally: prose is not a declaration."""

    PRELUDE = '''router = APIRouter(prefix="/api")

# Recognises @app.get('/path') and @router.post('/orders') declarations.
EXAMPLE = "@router.delete('/fabricated')"'''

    def test_route_shaped_text_above_no_handler_yields_nothing(self) -> None:
        assert _extract(self.PRELUDE, ("documented", [])) == []

    def test_a_real_decorator_in_the_same_file_still_counts(self) -> None:
        ids = _ids(_extract(self.PRELUDE, ("real", ['@router.get("/real")'])))
        assert ids == {"http::GET::/api/real"}

    def test_a_commented_out_decorator_is_not_a_route(self) -> None:
        # The comment stop is what keeps the walk out of the previous symbol's
        # body, and it makes a commented-out decorator inert on the way.
        ids = _ids(_extract(self.PRELUDE, ("plain", ['# @router.get("/commented")'])))
        assert ids == set()


class TestFlaskIsInherited:
    """Flask providers via the index path — no Flask dialect module exists."""

    PRELUDE = 'app = Flask(__name__)\nbp = Blueprint("api", __name__)'

    def test_route_decorator_defaults_to_get(self) -> None:
        ids = _ids(_extract(self.PRELUDE, ("index", ['@app.route("/health")'])))
        assert ids == {"http::GET::/health"}

    def test_methods_kwarg_yields_one_contract_per_verb(self) -> None:
        ids = _ids(
            _extract(
                self.PRELUDE,
                ("users", ['@app.route("/users", methods=["POST", "PUT"])']),
            )
        )
        assert ids == {"http::POST::/users", "http::PUT::/users"}

    def test_framework_is_labelled_flask(self) -> None:
        contract = _extract(self.PRELUDE, ("index", ['@app.route("/health")']))[0]
        assert contract.meta["framework"] == "flask"
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_INDEX

    def test_blueprint_url_prefix_is_stitched_on(self) -> None:
        # Flask spells it ``url_prefix=``, and the shared prefix pattern is not
        # left-anchored to a word boundary, so it matches that too and the
        # blueprint's mount lands on the route. Incidental, but correct — this
        # test pins the behaviour so a later tightening of the pattern has to
        # keep Flask working deliberately.
        prelude = 'bp = Blueprint("api", __name__, url_prefix="/api")'
        ids = _ids(_extract(prelude, ("listing", ['@bp.route("/items")'])))
        assert ids == {"http::GET::/api/items"}

    def test_the_regex_layer_has_no_flask_dialect(self) -> None:
        # Guards the claim in this class's docstring: if a Flask dialect is
        # added later, this test should be deleted along with the claim.
        from repowise.core.workspace.extractors.http import PROVIDER_DIALECTS

        assert "flask" not in {d.name for d in PROVIDER_DIALECTS}


def test_no_index_on_the_context_means_no_symbols() -> None:
    ctx = ScanContext("backend", REL_PATH, ".py", "x = 1\n")
    assert symbols_for_content(ctx, 1) == []


ROUTES_PY = '''\
from fastapi import APIRouter

router = APIRouter(prefix="/snapshots/{snapshot_id}/chat")


# Documents the @router.get("/fabricated") shape this module handles.
EXAMPLE = "@router.delete('/also-fabricated')"


@router.post("")
async def chat_message() -> None:
    """Sends a message. See @router.put("/not-a-route") for the old shape."""


@router.get("/conversations")
async def list_conversations() -> None:
    ...
'''


def _parse_symbols(rel_path: str, source: str) -> list[Symbol]:
    """Symbols for *source* from the real parser, as ingestion records them."""
    info = FileInfo(
        path=rel_path,
        abs_path=f"/repo/{rel_path}",
        language="python",
        size_bytes=len(source),
        git_hash="",
        last_modified=0.0,
        is_test=False,
        is_config=False,
        is_api_contract=True,
        is_entry_point=False,
    )
    return ASTParser().parse_file(info, source.encode()).symbols


class TestAgainstARealIndex:
    """The plumbing, end to end: real parser, real wiki.db, real query path.

    Everything above builds symbol rows by hand, which cannot catch the two
    agreements this path depends on — that the parser's line numbers leave a
    decorator above the span, and that the rows survive the database round trip
    keyed by the same relative path the walk yields.
    """

    @pytest.fixture
    async def repo(self, tmp_path: Path):
        (tmp_path / "routers").mkdir()
        (tmp_path / "routers" / "chat.py").write_text(ROUTES_PY, encoding="utf-8")
        rel = "routers/chat.py"
        index = await make_repo_index(tmp_path, {rel: _parse_symbols(rel, ROUTES_PY)})
        yield tmp_path, index
        await index.close()

    def _extract_all(self, repo: Path, index):
        from repowise.core.workspace.extractors import HttpExtractor
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        return HttpExtractor().extract(
            repo, "backend", make_exclude_predicate(), None, index
        )

    async def test_the_rows_round_trip(self, repo) -> None:
        _path, index = repo
        assert index.symbols_for_file("routers/chat.py")

    async def test_routes_come_from_the_index(self, repo) -> None:
        path, index = repo
        providers = [c for c in self._extract_all(path, index) if c.role == "provider"]
        assert providers, "index path produced nothing"
        assert {c.meta[EXTRACTION_LAYER_KEY] for c in providers} == {LAYER_INDEX}

    async def test_the_empty_path_route_survives_the_whole_path(self, repo) -> None:
        path, index = repo
        ids = _ids(self._extract_all(path, index))
        assert "http::POST::/snapshots/{param}/chat" in ids

    async def test_routes_in_comments_and_docstrings_are_not_extracted(
        self, repo
    ) -> None:
        path, index = repo
        ids = _ids(self._extract_all(path, index))
        for fabricated in (
            "http::GET::/snapshots/{param}/chat/fabricated",
            "http::DELETE::/snapshots/{param}/chat/also-fabricated",
            "http::PUT::/snapshots/{param}/chat/not-a-route",
        ):
            assert fabricated not in ids

    async def test_a_truncated_file_falls_back_to_the_regex(self, repo) -> None:
        path, index = repo
        # Cut the file down after it was indexed: the spans now overrun it, so
        # they must be refused rather than used to slice the wrong lines.
        (path / "routers" / "chat.py").write_text(
            'from fastapi import APIRouter\n\nrouter = APIRouter(prefix="/api")\n',
            encoding="utf-8",
        )
        contracts = self._extract_all(path, index)
        layers = {
            c.meta.get(EXTRACTION_LAYER_KEY) for c in contracts if c.role == "provider"
        }
        assert LAYER_INDEX not in layers

    async def test_a_route_added_after_indexing_is_still_found(self, repo) -> None:
        path, index = repo
        (path / "routers" / "chat.py").write_text(
            ROUTES_PY + '\n\n@router.get("/added")\nasync def added() -> None: ...\n',
            encoding="utf-8",
        )
        # The appended route sits outside every indexed span, so the index
        # cannot see it; the regex dialect still can, and both run for a file
        # the index pass produced no routes for.
        ids = _ids(self._extract_all(path, index))
        assert "http::GET::/snapshots/{param}/chat/added" in ids
