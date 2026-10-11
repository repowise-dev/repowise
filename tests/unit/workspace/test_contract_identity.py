"""Contract identity: the symbol id a contract belongs to.

A contract's ``symbol_name`` is a display label — ``"axios:GET /v1/orders"`` —
so it names nothing an agent can look up. ``symbol_id`` is the ingestion symbol
id, bound from the line the dialect matched on against the repo's symbol table.

Two claims are under test. The first is that binding is language-agnostic: it
reads a line and a symbol table, so a dialect that never heard of it gets
identity by reporting where it matched. The second is that it is additive
everywhere — a contract that cannot bind still matches, and a v1 artifact still
loads.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.ingestion.models import Symbol
from repowise.core.workspace.config import ContractConfig, RepoEntry, WorkspaceConfig
from repowise.core.workspace.contracts import (
    CONTRACTS_VERSION,
    Contract,
    ContractStore,
    bind_symbol_ids,
    run_contract_extraction,
)
from repowise.core.workspace.diagnostics import build_diagnostics

from ._repo_index import make_repo_index


def _symbol(name: str, start: int, end: int, *, path="app/api.py", kind="function"):
    return Symbol(
        id=f"{path}::{name}",
        name=name,
        qualified_name=name,
        kind=kind,
        signature=f"def {name}()",
        start_line=start,
        end_line=end,
        docstring=None,
        visibility="public",
    )


def _contract(line: int | None, *, role="provider", path="app/api.py") -> Contract:
    return Contract(
        repo="backend",
        contract_id="http::GET::/users",
        contract_type="http",
        role=role,
        file_path=path,
        symbol_name="fastapi:GET /users",
        confidence=0.85,
        line=line,
    )


# ---------------------------------------------------------------------------
# Binding by line
# ---------------------------------------------------------------------------


class TestDeclaredSymbolAt:
    """The lookup that turns a matched line into a symbol.

    ``symbol_at`` alone is not enough: a route decorator sits *above* its
    handler, outside the span, because the parser takes a symbol's extent from
    the ``def`` node rather than the decorated one.
    """

    async def test_a_decorator_binds_to_the_handler_below_it(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 11, 14)]})
        try:
            assert index.symbol_at("app/api.py", 10) is None  # outside the span
            found = index.declared_symbol_at("app/api.py", 10)
            assert found is not None and found.name == "users"
        finally:
            await index.close()

    async def test_a_decorated_method_wins_over_its_class(self, tmp_path: Path) -> None:
        index = await make_repo_index(
            tmp_path,
            {
                "app/api.py": [
                    _symbol("Views", 1, 20, kind="class"),
                    _symbol("get", 5, 8, kind="method"),
                ]
            },
        )
        try:
            assert index.symbol_at("app/api.py", 4).name == "Views"
            assert index.declared_symbol_at("app/api.py", 4).name == "get"
        finally:
            await index.close()

    async def test_a_call_inside_a_body_binds_to_that_body(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("fetch_all", 1, 9)]})
        try:
            assert index.declared_symbol_at("app/api.py", 5).name == "fetch_all"
        finally:
            await index.close()

    async def test_a_call_on_a_last_line_does_not_leak_to_the_next_symbol(
        self, tmp_path: Path
    ) -> None:
        """The nesting guard: the next definition down is not this one's."""
        index = await make_repo_index(
            tmp_path,
            {"app/api.py": [_symbol("first", 1, 5), _symbol("second", 7, 12)]},
        )
        try:
            assert index.declared_symbol_at("app/api.py", 5).name == "first"
        finally:
            await index.close()

    async def test_a_match_on_a_declaration_line_names_that_symbol(
        self, tmp_path: Path
    ) -> None:
        """A gRPC servicer class, not the first method inside it."""
        index = await make_repo_index(
            tmp_path,
            {
                "app/api.py": [
                    _symbol("OrderService", 2, 30, kind="class"),
                    _symbol("get_order", 5, 9, kind="method"),
                ]
            },
        )
        try:
            assert index.declared_symbol_at("app/api.py", 2).name == "OrderService"
        finally:
            await index.close()

    async def test_a_distant_definition_is_out_of_reach(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("late", 60, 70)]})
        try:
            assert index.declared_symbol_at("app/api.py", 2) is None
        finally:
            await index.close()


class TestBindSymbolIds:
    async def test_a_line_becomes_a_symbol_id(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 11, 14)]})
        try:
            contracts = [_contract(10)]
            bind_symbol_ids(contracts, index)
            assert contracts[0].symbol_id == "app/api.py::users"
            assert contracts[0].symbol_name == "fastapi:GET /users", "display label is untouched"
        finally:
            await index.close()

    async def test_an_already_bound_contract_is_left_alone(self, tmp_path: Path) -> None:
        """The index-backed dialects bind exactly; no line rule may overrule them."""
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 11, 14)]})
        try:
            contract = _contract(10)
            contract.symbol_id = "app/api.py::chosen_by_the_dialect"
            bind_symbol_ids([contract], index)
            assert contract.symbol_id == "app/api.py::chosen_by_the_dialect"
        finally:
            await index.close()

    async def test_a_contract_without_a_line_stays_unbound(self, tmp_path: Path) -> None:
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 1, 99)]})
        try:
            contracts = [_contract(None)]
            bind_symbol_ids(contracts, index)
            assert contracts[0].symbol_id is None
        finally:
            await index.close()

    def test_no_index_binds_nothing_and_counts_every_row_unindexed(self) -> None:
        contracts = [_contract(10), _contract(10, role="consumer")]
        counts = bind_symbol_ids(contracts, None)
        assert all(c.symbol_id is None for c in contracts)
        assert counts == {"identity_unindexed_provider": 1, "identity_unindexed_consumer": 1}

    async def test_a_file_the_parser_never_saw_is_counted_separately(
        self, tmp_path: Path
    ) -> None:
        """A ``.sql`` file has no symbols, so no binding rule can reach it."""
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 11, 14)]})
        try:
            contracts = [_contract(10), _contract(4, path="db/schema.sql")]
            counts = bind_symbol_ids(contracts, index)
            assert contracts[0].symbol_id == "app/api.py::users"
            assert contracts[1].symbol_id is None
            assert counts == {"identity_unindexed_provider": 1}
        finally:
            await index.close()

    async def test_a_table_declared_in_a_class_body_binds_to_the_class(
        self, tmp_path: Path
    ) -> None:
        """A ``__tablename__`` is a member, so the lookahead must not apply and
        take the first method under it."""
        index = await make_repo_index(
            tmp_path,
            {
                "app/models.py": [
                    _symbol("Order", 1, 20, path="app/models.py", kind="class"),
                    _symbol("label", 4, 6, path="app/models.py", kind="method"),
                ]
            },
        )
        try:
            contract = _contract(2, path="app/models.py")
            contract.contract_type = "data"
            contract.contract_id = "data::orders"
            bind_symbol_ids([contract], index)
            assert contract.symbol_id == "app/models.py::Order"
        finally:
            await index.close()

    async def test_a_module_level_call_does_not_claim_the_next_definition(
        self, tmp_path: Path
    ) -> None:
        """A consumer is a call, so nothing below the line can be its symbol."""
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("load_orders", 6, 9)]})
        try:
            contract = _contract(3, role="consumer")
            bind_symbol_ids([contract], index)
            assert contract.symbol_id is None
        finally:
            await index.close()

    async def test_an_indexed_file_with_nothing_at_the_line_is_not_called_unindexed(
        self, tmp_path: Path
    ) -> None:
        """Reported honestly as a binding miss, not blamed on a missing parse."""
        index = await make_repo_index(tmp_path, {"app/api.py": [_symbol("users", 60, 70)]})
        try:
            counts = bind_symbol_ids([_contract(2)], index)
            assert counts == {}
        finally:
            await index.close()


class TestReportedLines:
    r"""The line a dialect reports must be the declaration's own.

    Three ORM patterns open with ``^\s*`` under ``MULTILINE``, where ``^``
    matches at a preceding blank line and ``\s*`` eats the newlines. Taking
    the match's start rather than its group put the contract above its class.
    """

    def test_a_tablename_after_a_blank_line_reports_its_own_line(self) -> None:
        from repowise.core.workspace.extractors.base import ScanContext
        from repowise.core.workspace.extractors.data.orm_models import SqlAlchemyDjangoDialect

        content = 'class Order(Base):\n\n    __tablename__ = "orders"\n'
        ctx = ScanContext("backend", "app/models.py", ".py", content)
        contracts = SqlAlchemyDjangoDialect().extract(ctx)

        assert [c.line for c in contracts] == [3]

    def test_a_rails_model_after_blank_lines_reports_its_own_line(self) -> None:
        from repowise.core.workspace.extractors.base import ScanContext
        from repowise.core.workspace.extractors.data.orm_models import ActiveRecordDialect

        content = "module X\nend\n\n\nclass Order < ApplicationRecord\nend\n"
        ctx = ScanContext("backend", "app/models/order.rb", ".rb", content)
        contracts = ActiveRecordDialect().extract(ctx)

        assert [c.line for c in contracts] == [5]


# ---------------------------------------------------------------------------
# The artifact
# ---------------------------------------------------------------------------


class TestSerialization:
    def test_identity_round_trips(self) -> None:
        contract = _contract(10)
        contract.symbol_id = "app/api.py::users"
        restored = Contract.from_dict(contract.to_dict())
        assert restored.symbol_id == "app/api.py::users"
        assert restored.line == 10

    def test_an_unbound_contract_carries_no_empty_keys(self) -> None:
        d = _contract(None).to_dict()
        assert "symbol_id" not in d
        assert "line" not in d

    def test_a_v1_contract_still_loads(self) -> None:
        """The older shape: no line, no symbol id, and still a valid contract."""
        v1 = {
            "repo": "backend",
            "contract_id": "http::GET::/users",
            "contract_type": "http",
            "role": "provider",
            "file_path": "app/api.py",
            "symbol_name": "fastapi:GET /users",
            "confidence": 0.85,
        }
        contract = Contract.from_dict(v1)
        assert contract.symbol_id is None
        assert contract.line is None
        assert contract.contract_id == "http::GET::/users"

    def test_a_fresh_store_declares_the_identity_version(self) -> None:
        assert ContractStore().version == CONTRACTS_VERSION
        assert ContractStore.from_dict({}).version == 1, "an unversioned artifact is v1"


class TestDiagnostics:
    def test_the_ratio_is_reported_against_both_denominators(self) -> None:
        bound = _contract(10)
        bound.symbol_id = "app/api.py::users"
        contracts = [bound, _contract(4, path="db/schema.sql"), _contract(2)]
        stats = {"backend": {"identity_unindexed_provider": 1}}

        identity = build_diagnostics(contracts, [], stats).to_dict()["symbol_identity"]

        assert identity["provider"]["total"] == 3
        assert identity["provider"]["bound"] == 1
        assert identity["provider"]["unindexed_file"] == 1
        assert identity["provider"]["bound_ratio"] == pytest.approx(1 / 3)
        assert identity["provider"]["bound_ratio_indexed"] == pytest.approx(1 / 2)
        assert identity["consumer"]["bound_ratio"] is None, "0/0 is not 100%"


# ---------------------------------------------------------------------------
# End to end
# ---------------------------------------------------------------------------


ROUTE_SOURCE = (
    "from fastapi import APIRouter\n"
    "\n"
    "router = APIRouter()\n"
    "\n"
    '@router.get("/users")\n'
    "async def list_users():\n"
    "    return []\n"
)


@pytest.fixture
def two_repos(tmp_path: Path) -> WorkspaceConfig:
    for alias in ("alpha", "beta"):
        repo = tmp_path / alias
        (repo / "app").mkdir(parents=True)
        (repo / "app" / "api.py").write_text(ROUTE_SOURCE, encoding="utf-8")
        (repo / ".repowise").mkdir()
    return WorkspaceConfig(
        repos=[RepoEntry(path="alpha", alias="alpha"), RepoEntry(path="beta", alias="beta")],
        contracts=ContractConfig(),
    )


async def test_a_regex_extracted_route_binds_to_its_handler(
    two_repos: WorkspaceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The whole path, from a dialect's match offset to the persisted id."""
    from repowise.core.workspace import contracts as contracts_mod
    from repowise.core.workspace.repo_index import WorkspaceIndex

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    index = await make_repo_index(
        tmp_path / "alpha", {"app/api.py": [_symbol("list_users", 6, 7)]}, alias="alpha"
    )
    try:
        store = await run_contract_extraction(
            two_repos, tmp_path, [], workspace_index=WorkspaceIndex({"alpha": index})
        )
    finally:
        await index.close()

    alpha = [c for c in store.contracts if c.repo == "alpha" and c.role == "provider"]
    assert alpha
    assert all(c.symbol_id == "app/api.py::list_users" for c in alpha)
    beta = [c for c in store.contracts if c.repo == "beta"]
    assert beta
    assert all(c.symbol_id is None for c in beta), "no index, no identity, still a row"


async def test_a_v1_store_is_never_carried_forward(
    two_repos: WorkspaceConfig, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its rows have no identity and re-reading the source is the only fix."""
    from repowise.core.workspace import contracts as contracts_mod
    from repowise.core.workspace.extractors import base

    monkeypatch.setattr(contracts_mod, "save_contract_store", lambda store, root: root)
    walked: list[str] = []
    original = base.iter_source_files

    def recording(repo_path, wanted, exclude=None):
        walked.append(Path(repo_path).name)
        return original(repo_path, wanted, exclude)

    monkeypatch.setattr(base, "iter_source_files", recording)

    first = await run_contract_extraction(two_repos, tmp_path, [])
    first.version = 1
    walked.clear()

    await run_contract_extraction(two_repos, tmp_path, [], None, first)

    assert sorted(walked) == ["alpha", "beta"]
