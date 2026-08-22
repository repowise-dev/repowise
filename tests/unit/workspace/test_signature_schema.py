"""Request schemas recovered from a handler's parsed signature.

``Contract.schema`` was proto-only, so the three field-level rules in
:mod:`~repowise.core.workspace.breaking_change` could only ever fire on gRPC.
A provider knows its handler symbol, and that handler's parameter list is the
request shape — which is what turns those rules on for every contract type that
binds to a callable.

Two claims are under test. The first is that the mapper reads a *language*, not
a framework: the same code path serves FastAPI and ASP.NET, and the headline
test drives both through the real parser rather than a hand-written signature.
The second is that it is additive and quiet where it cannot see: a handler it
cannot read keeps its contract and gains no schema, and never a schema of zero
fields, which would read as "this endpoint takes nothing".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion import ASTParser, FileTraverser
from repowise.core.workspace.breaking_change import detect_breaking_changes
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contract_schema import ContractSchema, SchemaField
from repowise.core.workspace.contracts import Contract, ContractStore, run_contract_extraction
from repowise.core.workspace.repo_index import WorkspaceIndex
from repowise.core.workspace.signature_schema import (
    SCHEMA_SOURCE,
    attach_signature_schemas,
    schema_from_signature,
)

from ._repo_index import make_repo_index

# ---------------------------------------------------------------------------
# The mapper, per language
# ---------------------------------------------------------------------------


def _fields(
    signature: str, language: str, path_names: frozenset[str] = frozenset()
) -> list[tuple[str, str, bool]] | None:
    schema = schema_from_signature(signature, language, path_names)
    if schema is None:
        return None
    assert schema.source == SCHEMA_SOURCE
    assert not schema.response_fields, "a return type is one type, not a field set"
    return [(f.name, f.type, f.required) for f in schema.request_fields]


class TestParameterGrammars:
    """One mapper, several parameter orders — the only axis read is language."""

    def test_python_reads_name_type_and_default(self):
        assert _fields("async def h(period: str, limit: int = 50) -> dict", "python") == [
            ("period", "str", True),
            ("limit", "int", False),
        ]

    def test_csharp_reads_the_type_first_order_past_its_attributes(self):
        assert _fields(
            "GetSummary([FromQuery] string period, [FromQuery] int limit = 50) -> IActionResult",
            "csharp",
        ) == [("period", "string", True), ("limit", "int", False)]

    def test_typescript_optional_marker_is_optional_not_a_name(self):
        assert _fields("function h(id: string, cursor?: string | null) -> void", "typescript") == [
            ("id", "string", True),
            ("cursor", "string | null", False),
        ]

    def test_an_unlisted_language_yields_nothing_rather_than_a_guess(self):
        # Java's stored signature carries no parameter list at all.
        assert _fields("getReports -> List<String>", "java") is None
        assert _fields("function F(w http.ResponseWriter) ", "go") is None

    def test_a_csharp_keyword_is_not_stripped_from_a_python_parameter(self):
        # `out`, `params` and `ref` are C# modifiers and ordinary Python names;
        # one shared modifier set would silently delete all three fields.
        assert _fields("def g(out: list, params: dict, ref: str | None = None)", "python") == [
            ("out", "list", True),
            ("params", "dict", True),
            ("ref", "str | None", False),
        ]


class TestParametersThatAreNotFields:
    """What the parameter list carries that the caller never supplies."""

    def test_receivers_and_separators_are_dropped(self):
        assert _fields("def h(self, *, name: str, limit: int = 10)", "python") == [
            ("name", "str", True),
            ("limit", "int", False),
        ]

    def test_variadics_widen_the_surface_instead_of_naming_a_field(self):
        assert _fields("def h(a: int, *args, **kwargs)", "python") == [("a", "int", True)]

    def test_an_ellipsis_default_means_no_default(self):
        # `Query(...)` / `Field(...)` is the ecosystem-wide "required" placeholder.
        # Reading it as a real default is what makes a required parameter look
        # optional, which is the whole point of the required-tightened rule.
        assert _fields("def h(q: str = Query(...), page: int = Query(1))", "python") == [
            ("q", "str", True),
            ("page", "int", False),
        ]

    def test_the_placeholder_is_read_however_it_is_spelled(self):
        # `Query(...)` -> `Query(default=...)` is a style edit. Reading only the
        # first spelling flips `required` and reports a breaking change for it.
        assert _fields("def h(a = ..., b = Ellipsis, c = Q(default=...), d = Q(1))", "python") == [
            ("a", "", True),
            ("b", "", True),
            ("c", "", True),
            ("d", "", False),
        ]


class TestAmbientParameters:
    """The framework's injected objects are not fields a caller supplies."""

    def test_the_request_and_response_objects_are_dropped(self):
        # Renaming one of these is a no-op on the wire; counting it as a
        # required field reports that rename as a breaking change.
        assert _fields(
            "async def h(request: Request, response: Response, q: str)", "python"
        ) == [("q", "str", True)]
        assert _fields("Get(HttpContext ctx, [FromQuery] string q)", "csharp") == [
            ("q", "string", True)
        ]

    def test_the_match_is_the_whole_type_never_a_suffix(self):
        # A request *body* is the most caller-visible field there is, and it is
        # conventionally typed `SomethingRequest`. A suffix match eats it.
        assert _fields("async def h(body: BlastRadiusRequest, q: str)", "python") == [
            ("body", "BlastRadiusRequest", True),
            ("q", "str", True),
        ]

    def test_a_name_the_route_binds_is_never_dropped(self):
        # The interlock: caller-visible by construction, whatever it is typed as.
        assert _fields("async def h(request: Request)", "python", frozenset({"request"})) == [
            ("request", "Request", True)
        ]

    def test_a_handler_taking_only_ambient_objects_yields_no_schema(self):
        assert schema_from_signature("async def h(request: Request)", "python") is None


class TestRefusals:
    """Where the grammar's assumption fails, refuse rather than under-report."""

    def test_a_truncated_parameter_list_is_refused_whole(self):
        # A shortened field set would read as a removed field on the next diff.
        assert _fields("def h(a: int, b: str", "python") is None

    def test_a_comparison_in_a_default_is_not_a_type_argument(self):
        # Two comparisons balance as if they were `<...>`, and every parameter
        # between them is swallowed with no error — a wrong field set, which is
        # the one failure mode worse than refusing.
        assert _fields("Get(int lo = a < b, string name, int hi = c > d)", "csharp") == [
            ("lo", "int", False),
            ("name", "string", True),
            ("hi", "int", False),
        ]
        assert _fields("function h(a = x < 1, b, c = y > 2, d)", "javascript") == [
            ("a", "", False),
            ("b", "", True),
            ("c", "", False),
            ("d", "", True),
        ]

    def test_a_real_type_argument_still_nests(self):
        assert _fields("GetAll<T>(List<int> ids, Dictionary<string, int> m)", "csharp") == [
            ("ids", "List<int>", True),
            ("m", "Dictionary<string, int>", True),
        ]

    def test_a_destructured_parameter_refuses_the_whole_signature(self):
        assert _fields("function C({ children }: { children: ReactNode })", "typescript") is None

    def test_a_handler_taking_nothing_yields_no_schema(self):
        # Indistinguishable from "could not be read", and the honest answer to
        # the second is nothing at all.
        assert schema_from_signature("def create_app() -> FastAPI", "python") is None

    def test_a_comment_inside_the_list_does_not_open_a_string(self):
        signature = (
            "def h(\n"
            "    a: int,\n"
            "    # the caller's page size, not the server's\n"
            "    b: str = 'x,y',\n"
            ") -> None"
        )
        assert _fields(signature, "python") == [("a", "int", True), ("b", "str", False)]

    def test_a_block_comment_apostrophe_does_not_swallow_the_list(self):
        signature = (
            "function h(\n"
            "  a: string,\n"
            "  /** Aborts the caller's read loop. */\n"
            "  signal?: AbortSignal,\n"
            ") -> void"
        )
        assert _fields(signature, "typescript") == [
            ("a", "string", True),
            ("signal", "AbortSignal", False),
        ]


# ---------------------------------------------------------------------------
# The attach pass
# ---------------------------------------------------------------------------


def _provider(symbol_id: str | None, *, contract_id: str = "http::GET::/a") -> Contract:
    return Contract(
        repo="alpha",
        contract_id=contract_id,
        contract_type="http",
        role="provider",
        file_path="app/api.py",
        symbol_name="fastapi:GET /a",
        confidence=0.85,
        line=5,
        symbol_id=symbol_id,
    )


async def _index_for(tmp_path: Path, symbols) -> object:
    return await make_repo_index(tmp_path / "alpha", {"app/api.py": symbols}, alias="alpha")


def _symbol(name: str, signature: str, *, kind="function", language="python", line=6):
    from repowise.core.ingestion.models import Symbol

    return Symbol(
        id=f"app/api.py::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=signature,
        start_line=line,
        end_line=line + 1,
        docstring=None,
        visibility="public",
        language=language,
    )


class TestAttachPass:
    async def test_the_route_template_protects_its_own_parameters(self, tmp_path: Path):
        index = await _index_for(
            tmp_path, [_symbol("h", "def h(request: Request, repo_id: str)")]
        )
        contract = _provider("app/api.py::h")
        contract.symbol_name = "fastapi:GET /repos/{request}"
        try:
            attach_signature_schemas([contract], index)
        finally:
            await index.close()
        # `request` is bound by the path here, so the ambient list must not
        # reach it; `repo_id` is an ordinary field either way.
        assert [f.name for f in contract.schema.request_fields] == ["request", "repo_id"]

    async def test_a_bound_provider_gains_its_handler_schema(self, tmp_path: Path):
        index = await _index_for(tmp_path, [_symbol("h", "def h(q: str, page: int = 1)")])
        contracts = [_provider("app/api.py::h")]
        try:
            attach_signature_schemas(contracts, index)
        finally:
            await index.close()
        assert contracts[0].schema is not None
        assert [f.name for f in contracts[0].schema.request_fields] == ["q", "page"]

    async def test_a_consumer_never_gains_one(self, tmp_path: Path):
        index = await _index_for(tmp_path, [_symbol("h", "def h(q: str)")])
        consumer = _provider("app/api.py::h")
        consumer.role = "consumer"
        try:
            attach_signature_schemas([consumer], index)
        finally:
            await index.close()
        # The function making a call says nothing about the request it sends.
        assert consumer.schema is None

    async def test_a_symbol_carrying_several_providers_is_a_registration_site(
        self, tmp_path: Path
    ):
        index = await _index_for(
            tmp_path, [_symbol("create_app", "def create_app(settings: Settings) -> FastAPI")]
        )
        contracts = [
            _provider("app/api.py::create_app", contract_id="http::GET::/a"),
            _provider("app/api.py::create_app", contract_id="http::GET::/b"),
        ]
        try:
            counts = attach_signature_schemas(contracts, index)
        finally:
            await index.close()
        # `settings` describes neither route; a schema here would be wrong,
        # not merely absent.
        assert all(c.schema is None for c in contracts)
        assert counts["schema_shared_symbol_provider"] == 2

    async def test_an_unsupported_language_is_counted_not_guessed(self, tmp_path: Path):
        index = await _index_for(
            tmp_path, [_symbol("h", "h -> List<String>", language="java", kind="method")]
        )
        contracts = [_provider("app/api.py::h")]
        try:
            counts = attach_signature_schemas(contracts, index)
        finally:
            await index.close()
        assert contracts[0].schema is None
        assert counts["schema_unsupported_lang_provider"] == 1

    async def test_a_proto_schema_is_left_alone(self, tmp_path: Path):
        index = await _index_for(tmp_path, [_symbol("h", "def h(q: str)")])
        contract = _provider("app/api.py::h")
        original = ContractSchema(source="proto", request_fields=[SchemaField("id", "string")])
        contract.schema = original
        try:
            attach_signature_schemas([contract], index)
        finally:
            await index.close()
        assert contract.schema is original
        assert [f.name for f in contract.schema.request_fields] == ["id"]

    async def test_a_non_callable_symbol_is_counted_not_read(self, tmp_path: Path):
        index = await _index_for(
            tmp_path, [_symbol("Order", "class Order", kind="class", line=6)]
        )
        contracts = [_provider("app/api.py::Order")]
        try:
            counts = attach_signature_schemas(contracts, index)
        finally:
            await index.close()
        assert contracts[0].schema is None
        assert counts["schema_non_callable_provider"] == 1

    async def test_an_unbound_provider_is_untouched(self, tmp_path: Path):
        index = await _index_for(tmp_path, [_symbol("h", "def h(q: str)")])
        contracts = [_provider(None)]
        try:
            attach_signature_schemas(contracts, index)
        finally:
            await index.close()
        assert contracts[0].schema is None

    def test_no_index_is_not_an_error(self):
        contracts = [_provider("app/api.py::h")]
        assert attach_signature_schemas(contracts, None) == {}
        assert contracts[0].schema is None


class TestFidelityIsNotMixed:
    """Two sources describe the same endpoint at different fidelities."""

    def test_a_proto_shape_is_never_diffed_against_a_signature_one(self):
        from repowise.core.workspace.breaking_change import _diff_schemas

        proto = ContractSchema(source="proto", request_fields=[SchemaField("user_id", "string")])
        signature = ContractSchema(
            source=SCHEMA_SOURCE, request_fields=[SchemaField("user_id", "int", required=True)]
        )
        # The fields differ in both type and requiredness, so a source-blind
        # diff reports two breaking changes for a change of parser.
        assert _diff_schemas(proto, signature)
        assert _report_kinds(proto, signature) == []

    def test_two_signature_shapes_are_diffed(self):
        before = ContractSchema(source=SCHEMA_SOURCE, request_fields=[SchemaField("a", "str")])
        after = ContractSchema(
            source=SCHEMA_SOURCE,
            request_fields=[SchemaField("a", "str"), SchemaField("b", "str", required=True)],
        )
        assert _report_kinds(before, after) == ["field_required"]


def _report_kinds(prev_schema, curr_schema) -> list[str]:
    """Kinds `detect_breaking_changes` reports for one contract's two shapes."""

    def store(schema):
        contract = _provider("app/api.py::h")
        contract.schema = schema
        return ContractStore(contracts=[contract])

    return [c.kind for c in detect_breaking_changes(store(prev_schema), store(curr_schema)).changes]


# ---------------------------------------------------------------------------
# The gate: a required parameter added to a real route, in two frameworks
# ---------------------------------------------------------------------------

FASTAPI_BEFORE = (
    "from fastapi import APIRouter\n"
    "\n"
    "router = APIRouter()\n"
    "\n"
    "\n"
    '@router.get("/reports/summary")\n'
    "async def get_summary(period: str) -> dict:\n"
    "    return {}\n"
)
FASTAPI_AFTER = FASTAPI_BEFORE.replace(
    "async def get_summary(period: str)", "async def get_summary(period: str, tenant: str)"
)

ASPNET_BEFORE = (
    "using Microsoft.AspNetCore.Mvc;\n"
    "\n"
    "[ApiController]\n"
    '[Route("reports")]\n'
    "public class ReportsController : ControllerBase\n"
    "{\n"
    '    [HttpGet("summary")]\n'
    "    public IActionResult GetSummary([FromQuery] string period)\n"
    "    {\n"
    "        return Ok();\n"
    "    }\n"
    "}\n"
)
ASPNET_AFTER = ASPNET_BEFORE.replace(
    "GetSummary([FromQuery] string period)",
    "GetSummary([FromQuery] string period, [FromQuery] string tenant)",
)


def _parse(repo: Path) -> dict[str, list]:
    """Real ingestion output for *repo*, so the test reads real signatures."""
    parser = ASTParser()
    out: dict[str, list] = {}
    for file_info in FileTraverser(repo).traverse():
        parsed = parser.parse_file(file_info, (repo / file_info.path).read_bytes())
        if parsed.symbols:
            out[file_info.path] = list(parsed.symbols)
    return out


#: The consuming repo, so the diff has a cross-repo consumer to name. Its
#: language is irrelevant to the provider under test, which is the point.
CONSUMER = 'import axios from "axios";\n\naxios.get("/reports/summary");\n'


async def _extract(tmp_path: Path, filename: str, source: str, monkeypatch) -> ContractStore:
    """Extract a two-repo workspace: alpha provides the route, beta calls it."""
    from repowise.core.workspace import contracts as contracts_mod

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    repo = tmp_path / "alpha"
    (repo / "src").mkdir(parents=True, exist_ok=True)
    (repo / "src" / filename).write_text(source, encoding="utf-8")
    (repo / ".repowise").mkdir(exist_ok=True)

    beta = tmp_path / "beta"
    (beta / "src").mkdir(parents=True, exist_ok=True)
    (beta / "src" / "client.ts").write_text(CONSUMER, encoding="utf-8")
    (beta / ".repowise").mkdir(exist_ok=True)

    index = await make_repo_index(repo, _parse(repo), alias="alpha")
    config = WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        contracts=ContractConfig(),
    )
    try:
        return await run_contract_extraction(
            config, tmp_path, [], workspace_index=WorkspaceIndex({"alpha": index})
        )
    finally:
        await index.close()


@pytest.mark.parametrize(
    ("filename", "before", "after"),
    [
        ("reports.py", FASTAPI_BEFORE, FASTAPI_AFTER),
        ("ReportsController.cs", ASPNET_BEFORE, ASPNET_AFTER),
    ],
    ids=["fastapi", "aspnet"],
)
async def test_adding_a_required_parameter_is_a_breaking_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, filename: str, before: str, after: str
):
    """The reported test, end to end, through the real parser and no new rule."""
    previous = await _extract(tmp_path / "a", filename, before, monkeypatch)
    current = await _extract(tmp_path / "b", filename, after, monkeypatch)

    providers = [c for c in previous.contracts if c.repo == "alpha" and c.role == "provider"]
    assert providers and all(c.schema is not None for c in providers), providers
    assert previous.contract_links, "the consumer must be matched for impact to resolve"

    report = detect_breaking_changes(previous, current)
    tightened = [c for c in report.changes if c.kind == "field_required"]
    assert [c.field_name for c in tightened] == ["tenant"], report.to_dict()
    change = tightened[0]
    assert change.severity == "breaking"
    # The identity W2 gave the contract survives onto the change itself.
    assert change.provider_symbol_id
    assert [i.repo for i in change.impacted_consumers] == ["beta"]
    # Exactly one change: adding an optional parameter or renaming an existing
    # one would show up here, and neither happened.
    assert len(report.changes) == 1, report.to_dict()
