"""Contract origins and link strategies: one closed vocabulary, one confidence per value."""

from __future__ import annotations

from pathlib import Path

import pytest

from repowise.core.workspace.config import ContractConfig, ManualContractLink
from repowise.core.workspace.contracts import (
    Contract,
    ContractLink,
    _build_manual_links,
    _enabled_extractors,
)
from repowise.core.workspace.extractors import grpc, socket, topic
from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.dialect import build_contract
from repowise.core.workspace.extractors.topic.nestjs import _HANDLERS
from repowise.core.workspace.matching import annotate_consumer_targets, match_contracts
from repowise.core.workspace.origins import (
    ORIGIN_CONFIDENCE,
    STRATEGY_CONFIDENCE,
    ContractOrigin,
    LinkStrategy,
    link_confidence,
    match_type_for,
)
from repowise.core.workspace.system_graph import SystemEdge


def test_every_value_carries_exactly_one_confidence() -> None:
    assert set(ORIGIN_CONFIDENCE) == set(ContractOrigin)
    assert set(STRATEGY_CONFIDENCE) == set(LinkStrategy)
    assert all(0 < v <= 1 for v in [*ORIGIN_CONFIDENCE.values(), *STRATEGY_CONFIDENCE.values()])


def _declared_rows() -> list[object]:
    rows: list[object] = list(_HANDLERS)
    for dialect in (*topic.DIALECTS, *socket.DIALECTS):
        rows.extend(getattr(dialect, "calls", ()))
        rows.extend(getattr(dialect, "receivers", ()))
    for lang in grpc.LANGUAGES:
        rows.extend(lang.rules)
    return rows


def test_every_registered_row_declares_an_origin_from_the_vocabulary() -> None:
    rows = _declared_rows()
    assert len(rows) > 40  # the registries were actually walked
    for row in rows:
        assert isinstance(row.origin, ContractOrigin), row


def test_a_contract_outside_the_vocabulary_cannot_be_built() -> None:
    ctx = ScanContext("a", "x.py", ".py", "")
    with pytest.raises(ValueError):
        build_contract(
            ctx,
            contract_type="http",
            contract_id="http::GET::/x",
            role="consumer",
            symbol_name="x",
            origin="guessed",  # type: ignore[arg-type]
            line=1,
            meta={},
        )


_FILES = {
    "server.js": "const app = require('express')();\napp.get('/users', (req, res) => {});\n",
    "client.js": "fetch('/orders');\n",
    "api.ts": (
        "import axios from 'axios';\n"
        "const api = axios.create({ baseURL: 'http://billing/api' });\n"
        "api.get('/invoices');\n"
    ),
    "schema.sql": "CREATE TABLE orders (id int);\n",
    "repo.py": 'cur.execute("SELECT * FROM orders")\n',
    "worker.py": "from kafka import KafkaConsumer\nKafkaConsumer('orders')\n",
    "svc.proto": (
        "syntax = \"proto3\";\npackage shop;\n"
        "service Cart { rpc Add (Req) returns (Res); }\n"
        "message Req {}\nmessage Res {}\n"
    ),
}


def test_every_extracted_contract_is_stamped_with_its_origin(tmp_path: Path) -> None:
    for name, text in _FILES.items():
        (tmp_path / name).write_text(text, encoding="utf-8")
    contracts: list[Contract] = []
    for extractor in _enabled_extractors(ContractConfig()):
        contracts.extend(extractor.extract(tmp_path, "svc"))
    origins = {c.origin for c in contracts}
    assert {
        ContractOrigin.ROUTE_REGISTRATION,
        ContractOrigin.CLIENT_CALL,
        ContractOrigin.INSTANCE_BASE,
        ContractOrigin.DDL,
        ContractOrigin.SQL_STRING,
        ContractOrigin.IDL,
    } <= origins
    for c in contracts:
        assert c.confidence == ORIGIN_CONFIDENCE[ContractOrigin(c.origin)], c
        assert Contract.from_dict(c.to_dict()).origin == c.origin


def _http(repo: str, role: str, path: str, origin: ContractOrigin, **meta) -> Contract:
    return Contract(
        repo=repo,
        contract_id=f"http::GET::{path}",
        contract_type="http",
        role=role,
        file_path=f"{repo}.ts",
        symbol_name=path,
        confidence=ORIGIN_CONFIDENCE[origin],
        meta={"method": "GET", "path": path, **meta},
        origin=origin,
    )


def _provider(repo: str, path: str) -> Contract:
    return _http(repo, "provider", path, ContractOrigin.ROUTE_REGISTRATION)


def _consumer(path: str, **meta) -> Contract:
    return _http("web", "consumer", path, ContractOrigin.NAME_HEURISTIC, **meta)


@pytest.mark.parametrize(
    ("contracts", "service_bases", "strategy"),
    [
        ([_provider("users", "/users"), _consumer("/users")], {}, LinkStrategy.EXACT_ID),
        ([_provider("users", "/api/v1/users"), _consumer("/users")], {}, LinkStrategy.PREFIX_STRIPPED),
        (
            [_provider("users", "/users"), _consumer("/users", base_stripped=True)],
            {},
            LinkStrategy.UNIQUE_PATH,
        ),
        (
            [
                _provider("users", "/users"),
                _provider("admin", "/users"),
                _consumer("/users", base_stripped=True, base_token="users_api"),
            ],
            {"USERS_API": "users"},
            LinkStrategy.SERVICE_MAPPED,
        ),
        (
            [
                _provider("users", "/users"),
                _provider("admin", "/users"),
                _consumer("/users", base_stripped=True),
            ],
            {},
            LinkStrategy.AMBIGUOUS_PATH,
        ),
    ],
)
def test_each_http_link_names_the_rule_that_made_it(
    contracts: list[Contract], service_bases: dict[str, str], strategy: LinkStrategy
) -> None:
    annotate_consumer_targets(contracts, service_bases)
    links = match_contracts(contracts)
    assert links
    for link in links:
        assert link.strategy == strategy
        assert link.match_type == match_type_for(strategy)
        # The weaker end (the 0.65 consumer) scaled by the strategy's factor.
        assert link.confidence == link_confidence(strategy, 0.85, 0.65)
        assert ContractLink.from_dict(link.to_dict()).strategy == strategy


def test_a_manual_link_is_its_own_strategy() -> None:
    manual = ManualContractLink(
        from_repo="web", to_repo="users", contract_id="http::GET::/x", contract_type="http"
    )
    [link] = _build_manual_links([manual])
    assert (link.strategy, link.match_type) == (LinkStrategy.MANUAL, "manual")


@pytest.mark.parametrize(
    ("match_type", "kind", "strategy"),
    [
        ("exact", "http", LinkStrategy.EXACT_ID),
        ("candidate", "http", LinkStrategy.PREFIX_STRIPPED),
        ("manual", "grpc", LinkStrategy.MANUAL),
        ("exact", "package", LinkStrategy.MANIFEST),
        ("inferred", "co_change", None),
    ],
)
def test_a_stored_artifact_without_strategies_still_reads_one(
    match_type: str, kind: str, strategy: LinkStrategy | None
) -> None:
    edge = SystemEdge.from_dict(
        {"id": "a->b", "source": "a", "target": "b", "kind": kind, "match_type": match_type}
    )
    assert edge.strategy == strategy
    if kind != "co_change":
        link = ContractLink.from_dict(
            {
                "contract_id": "x",
                "contract_type": "http",
                "match_type": match_type,
                "provider_repo": "b",
                "provider_file": "b.ts",
                "consumer_repo": "a",
                "consumer_file": "a.ts",
            }
        )
        assert link.strategy == (strategy if kind != "package" else LinkStrategy.EXACT_ID)
