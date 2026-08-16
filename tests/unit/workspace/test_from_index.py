"""Index-backed contract extraction: routes read from parsed decorators.

Two claims are under test here. The first is that reading the decorator a
parser already recorded removes a class of defect the regex could only chase:
a route-shaped string in a comment is not a decorator, and an empty path is
just a string argument, so neither can fabricate or lose a contract.

The second is the inherited-framework claim. Ingestion resolves Flask as well
as FastAPI, while the workspace regex layer was FastAPI-only. Flask arriving
here without a new dialect module is the evidence that the rest of the
framework list comes with the layer rather than being hand-written per
framework.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.models import FileInfo, ParsedFile, Symbol
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.from_index import (
    EXTRACTION_LAYER_KEY,
    LAYER_INDEX,
    extract_http_providers,
    load_repo_index,
)


def _symbol(name: str, decorators: list[str], line: int = 10) -> Symbol:
    return Symbol(
        id=f"m.py::{name}",
        name=name,
        qualified_name=name,
        kind="function",
        signature=f"def {name}()",
        start_line=line,
        end_line=line + 3,
        docstring=None,
        decorators=decorators,
        language="python",
    )


def _parsed(symbols: list[Symbol], rel_path: str = "app/routers/chat.py") -> ParsedFile:
    info = FileInfo(
        path=rel_path,
        abs_path=f"/repo/{rel_path}",
        language="python",
        size_bytes=100,
        git_hash="",
        last_modified=0.0,
        is_test=False,
        is_config=False,
        is_api_contract=True,
        is_entry_point=False,
    )
    return ParsedFile(file_info=info, symbols=symbols, imports=[], exports=[])


def _ctx(content: str, rel_path: str = "app/routers/chat.py") -> ScanContext:
    return ScanContext(
        repo_alias="backend", rel_path=rel_path, suffix=".py", content=content
    )


def _ids(contracts) -> set[str]:
    return {c.contract_id for c in contracts}


class TestFastApiFromDecorators:
    CONTENT = 'router = APIRouter(prefix="/snapshots/{snapshot_id}/chat")\n'

    def test_empty_path_route_resolves_to_the_prefix(self) -> None:
        parsed = _parsed([_symbol("chat_message", ['@router.post("")'])])
        ids = _ids(extract_http_providers(_ctx(self.CONTENT), parsed))
        assert ids == {"http::POST::/snapshots/{param}/chat"}

    def test_provenance_is_recorded(self) -> None:
        parsed = _parsed([_symbol("chat_message", ['@router.post("")'])])
        contract = extract_http_providers(_ctx(self.CONTENT), parsed)[0]
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_INDEX
        assert contract.meta["handler"] == "chat_message"

    def test_layer_key_does_not_collide_with_grpc_dialect_provenance(self) -> None:
        # The gRPC dialects already own meta["source"] for dialect provenance
        # ("py_servicer", "go_client", "proto"); the layer must not overwrite
        # it or the coverage metric mis-partitions every gRPC contract.
        assert EXTRACTION_LAYER_KEY != "source"
        parsed = _parsed([_symbol("chat", ['@router.post("")'])])
        contract = extract_http_providers(_ctx(self.CONTENT), parsed)[0]
        assert "source" not in contract.meta

    def test_duplicated_decorator_entries_yield_one_contract(self) -> None:
        # The parser can append the same decorator twice for one symbol.
        parsed = _parsed(
            [_symbol("chat", ['@router.post("")', '@router.post("")'])]
        )
        assert len(extract_http_providers(_ctx(self.CONTENT), parsed)) == 1

    def test_non_route_decorators_are_ignored(self) -> None:
        parsed = _parsed(
            [
                _symbol(
                    "chat",
                    ['@limiter.limit("240/minute")', "@dataclass", "@classmethod"],
                )
            ]
        )
        assert extract_http_providers(_ctx(self.CONTENT), parsed) == []

    def test_unknown_router_variable_is_not_a_route(self) -> None:
        parsed = _parsed([_symbol("cached", ['@cache.get("/x")'])])
        assert extract_http_providers(_ctx(self.CONTENT), parsed) == []


class TestCommentsCannotFabricateRoutes:
    """The self-extraction defect, structurally: a comment is not a decorator."""

    CONTENT = '''
router = APIRouter(prefix="/api")

# Recognises @app.get('/path') and @router.post('/orders') declarations.
EXAMPLE = "@router.delete('/fabricated')"


def documented() -> None:
    """Handles @router.put("/from-a-docstring") style routes."""
'''

    def test_route_shaped_text_outside_a_decorator_yields_nothing(self) -> None:
        # No symbol carries a route decorator, so there is no route — however
        # much route-shaped prose the file contains.
        parsed = _parsed([_symbol("documented", [])], rel_path="extractors/http.py")
        assert extract_http_providers(_ctx(self.CONTENT), parsed) == []

    def test_a_real_decorator_in_the_same_file_still_counts(self) -> None:
        parsed = _parsed(
            [_symbol("real", ['@router.get("/real")'])], rel_path="extractors/http.py"
        )
        ids = _ids(extract_http_providers(_ctx(self.CONTENT), parsed))
        assert ids == {"http::GET::/api/real"}


class TestFlaskIsInherited:
    """Flask providers via the index path — no Flask dialect module exists."""

    CONTENT = 'app = Flask(__name__)\nbp = Blueprint("api", __name__)\n'

    def test_route_decorator_defaults_to_get(self) -> None:
        parsed = _parsed([_symbol("index", ['@app.route("/health")'])])
        ids = _ids(extract_http_providers(_ctx(self.CONTENT), parsed))
        assert ids == {"http::GET::/health"}

    def test_methods_kwarg_yields_one_contract_per_verb(self) -> None:
        parsed = _parsed(
            [_symbol("users", ['@app.route("/users", methods=["POST", "PUT"])'])]
        )
        ids = _ids(extract_http_providers(_ctx(self.CONTENT), parsed))
        assert ids == {"http::POST::/users", "http::PUT::/users"}

    def test_framework_is_labelled_flask(self) -> None:
        parsed = _parsed([_symbol("index", ['@app.route("/health")'])])
        contract = extract_http_providers(_ctx(self.CONTENT), parsed)[0]
        assert contract.meta["framework"] == "flask"
        assert contract.meta[EXTRACTION_LAYER_KEY] == LAYER_INDEX

    def test_blueprint_url_prefix_is_stitched_on(self) -> None:
        # Flask spells it ``url_prefix=``, and the shared prefix pattern is not
        # left-anchored to a word boundary, so it matches that too and the
        # blueprint's mount lands on the route. Incidental, but correct — this
        # test pins the behaviour so a later tightening of the pattern has to
        # keep Flask working deliberately.
        content = 'bp = Blueprint("api", __name__, url_prefix="/api")\n'
        parsed = _parsed([_symbol("listing", ['@bp.route("/items")'])])
        ids = _ids(extract_http_providers(_ctx(content), parsed))
        assert ids == {"http::GET::/api/items"}

    def test_the_regex_layer_has_no_flask_dialect(self) -> None:
        # Guards the claim in this class's docstring: if a Flask dialect is
        # added later, this test should be deleted along with the claim.
        from repowise.core.workspace.extractors.http import PROVIDER_DIALECTS

        assert "flask" not in {d.name for d in PROVIDER_DIALECTS}


class TestLoadRepoIndex:
    def test_missing_cache_returns_none(self, tmp_path) -> None:
        assert load_repo_index(tmp_path) is None

    def test_unreadable_cache_returns_none(self, tmp_path) -> None:
        # A repo indexed by another repowise version fails the parser
        # fingerprint gate and must degrade to the regex path, not raise.
        (tmp_path / ".repowise").mkdir()
        (tmp_path / ".repowise" / "parse_cache.pkl").write_bytes(b"not a pickle")
        assert load_repo_index(tmp_path) is None


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


def _write_real_parse_cache(repo: Path) -> None:
    """Parse *repo*'s Python with the real ingestion parser and cache it.

    Uses ``ParseCache`` itself rather than a hand-built pickle, so the entry
    key, the ``parser_fingerprint`` gate and the content hash are all produced
    the way ingestion produces them.
    """
    from repowise.core.ingestion.models import compute_content_hash
    from repowise.core.ingestion.parse_cache import ParseCache
    from repowise.core.ingestion.parser import parse_file
    from repowise.core.ingestion.traverser import FileTraverser

    cache = ParseCache(repo / ".repowise")
    for info in FileTraverser(repo).traverse():
        if not info.path.endswith(".py"):
            continue
        source = Path(info.abs_path).read_bytes()
        cache.put(parse_file(info, source), compute_content_hash(source))
    cache.save()


class TestAgainstARealParseCache:
    """The plumbing, end to end: real parser, real cache, real hash check.

    Everything else here builds a ``ParsedFile`` by hand, which cannot catch
    the two agreements this path depends on: that the walk hashes bytes the
    same way ingestion does, and that a cached entry survives the round trip.
    """

    @pytest.fixture
    def repo(self, tmp_path: Path) -> Path:
        (tmp_path / ".repowise").mkdir()
        (tmp_path / "routers").mkdir()
        (tmp_path / "routers" / "chat.py").write_text(ROUTES_PY, encoding="utf-8")
        _write_real_parse_cache(tmp_path)
        return tmp_path

    def test_the_cache_round_trips(self, repo: Path) -> None:
        index = load_repo_index(repo)
        assert index is not None
        assert "routers/chat.py" in index

    def test_routes_come_from_the_index(self, repo: Path) -> None:
        from repowise.core.workspace.extractors import HttpExtractor
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        index = load_repo_index(repo)
        contracts = HttpExtractor().extract(
            repo, "backend", make_exclude_predicate(), None, index=index
        )
        providers = [c for c in contracts if c.role == "provider"]
        assert providers, "index path produced nothing"
        assert {c.meta[EXTRACTION_LAYER_KEY] for c in providers} == {LAYER_INDEX}

    def test_the_empty_path_route_survives_the_whole_path(self, repo: Path) -> None:
        from repowise.core.workspace.extractors import HttpExtractor
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        index = load_repo_index(repo)
        ids = _ids(
            HttpExtractor().extract(
                repo, "backend", make_exclude_predicate(), None, index=index
            )
        )
        assert "http::POST::/snapshots/{param}/chat" in ids

    def test_routes_in_comments_and_docstrings_are_not_extracted(
        self, repo: Path
    ) -> None:
        from repowise.core.workspace.extractors import HttpExtractor
        from repowise.core.workspace.extractors.base import make_exclude_predicate

        index = load_repo_index(repo)
        ids = _ids(
            HttpExtractor().extract(
                repo, "backend", make_exclude_predicate(), None, index=index
            )
        )
        for fabricated in (
            "http::GET::/snapshots/{param}/chat/fabricated",
            "http::DELETE::/snapshots/{param}/chat/also-fabricated",
            "http::PUT::/snapshots/{param}/chat/not-a-route",
        ):
            assert fabricated not in ids

    def test_an_edited_file_falls_back_to_the_regex(self, repo: Path) -> None:
        from repowise.core.workspace.extractors import HttpExtractor
        from repowise.core.workspace.extractors.base import make_exclude_predicate
        from repowise.core.workspace.extractors.from_index import parsed_for

        index = load_repo_index(repo)
        # Edit the file after it was cached: the entry now describes older
        # bytes, so it must be rejected rather than trusted.
        (repo / "routers" / "chat.py").write_text(
            ROUTES_PY + '\n\n@router.get("/added")\nasync def added() -> None: ...\n',
            encoding="utf-8",
        )
        contracts = HttpExtractor().extract(
            repo, "backend", make_exclude_predicate(), None, index=index
        )
        layers = {c.meta.get(EXTRACTION_LAYER_KEY) for c in contracts if c.role == "provider"}
        assert LAYER_INDEX not in layers  # stale entry refused
        assert parsed_for(index, "routers/chat.py", "deadbeef") is None
        # And the newly added route is still found, by the regex path.
        assert "http::GET::/snapshots/{param}/chat/added" in _ids(contracts)
