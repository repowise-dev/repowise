"""A package-less stub meets its package-qualified proto; a vendored proto copy serves nothing.

Generated stubs name a service without its proto package (``CurrencyService``),
the ``.proto`` names it with one (``shop.CurrencyService``). Services also keep
copies of the protos they call, so once the two ids meet, every copy would read
as a server. Only the repo that registers the service in code serves it, and a
.NET project that compiles a proto with ``GrpcServices="Client"`` serves none of it.
"""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.contracts import Contract
from repowise.core.workspace.diagnostics import build_diagnostics
from repowise.core.workspace.extractors.grpc import GrpcExtractor
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.matching.common import find_matching_keys

_PROTO = """\
syntax = "proto3";
package shop;

service CurrencyService {
  rpc Convert(ConvertRequest) returns (Money) {}
}

service PaymentService {
  rpc Charge(ChargeRequest) returns (ChargeResponse) {}
}
"""

_NODE_SERVER = """\
const grpc = require('@grpc/grpc-js');
const server = new grpc.Server();
server.addService(shopPackage.CurrencyService.service, {convert});
"""

_GO_CLIENT = """\
package main

import pb "example.com/shop/genproto"

func dial(conn *grpc.ClientConn) {
	c := pb.NewCurrencyServiceClient(conn)
	p := pb.NewPaymentServiceClient(conn)
	_, _ = c, p
}
"""

_NEST_SERVER = """\
@Controller()
export class PaymentController {
  @GrpcMethod('PaymentService', 'Charge')
  charge(req) {}
}
"""


def _repo(root: Path, alias: str, files: dict[str, str]) -> list[Contract]:
    repo = root / alias
    for rel, text in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    return GrpcExtractor().extract(repo, alias)


def _pairs(contracts: list[Contract]) -> set[tuple[str, str, str]]:
    return {(lk.consumer_repo, lk.provider_repo, lk.provider_file) for lk in match_contracts(contracts)}


def _contract(repo: str, contract_id: str, role: str, source: str) -> Contract:
    return Contract(
        repo=repo,
        contract_id=contract_id,
        contract_type="grpc",
        role=role,
        file_path=f"{source}.x",
        symbol_name=contract_id,
        confidence=0.85,
        meta={"source": source},
    )


class TestQualifiedIds:
    def test_a_package_less_stub_reaches_the_only_proto(self) -> None:
        contracts = [
            _contract("currency", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("frontend", "grpc::CurrencyService/*", "consumer", "go_client"),
        ]
        links = match_contracts(contracts)
        assert [(lk.provider_repo, lk.consumer_contract_id) for lk in links] == [
            ("currency", "grpc::CurrencyService/*")
        ]

    def test_a_qualified_stub_reaches_a_package_less_server(self) -> None:
        index = {"grpc::currencyservice/*": []}
        assert find_matching_keys("grpc::shop.CurrencyService/*", index) == ["grpc::currencyservice/*"]

    def test_two_packages_are_two_services(self) -> None:
        index = {"grpc::billing.currencyservice/Convert": []}
        assert find_matching_keys("grpc::shop.CurrencyService/*", index) == []

    def test_a_named_method_still_needs_that_method(self) -> None:
        index = {"grpc::shop.currencyservice/Convert": [], "grpc::shop.currencyservice/List": []}
        assert find_matching_keys("grpc::CurrencyService/List", index) == [
            "grpc::shop.currencyservice/List"
        ]

    def test_http_and_topic_keys_are_unchanged(self) -> None:
        index = {
            "http::GET::/api/currency": [],
            "topic::orders/created": [],
            "grpc::shop.orders/Create": [],
        }
        assert find_matching_keys("http::*::/api/currency", index) == ["http::GET::/api/currency"]
        assert find_matching_keys("topic::orders/*", index) == ["topic::orders/created"]
        assert find_matching_keys("http::GET::/orders", index) == []


class TestVendoredProtos:
    def test_only_the_repo_that_serves_the_service_links(self, tmp_path: Path) -> None:
        # Four repos vendor one proto; `currency` serves one service and
        # `payment` the other, by method, so the stub reaches the proto ids too.
        contracts = [
            *_repo(tmp_path, "currency", {"proto/shop.proto": _PROTO, "server.js": _NODE_SERVER}),
            *_repo(tmp_path, "payment", {"proto/shop.proto": _PROTO, "app.controller.ts": _NEST_SERVER}),
            *_repo(tmp_path, "frontend", {"proto/shop.proto": _PROTO, "rpc.go": _GO_CLIENT}),
            *_repo(tmp_path, "checkout", {"proto/shop.proto": _PROTO, "main.go": _GO_CLIENT}),
        ]
        assert _pairs(contracts) == {
            ("frontend", "currency", "server.js"),
            ("checkout", "currency", "server.js"),
            ("frontend", "payment", "app.controller.ts"),
            ("frontend", "payment", "proto/shop.proto"),
            ("checkout", "payment", "app.controller.ts"),
            ("checkout", "payment", "proto/shop.proto"),
        }

    def test_with_no_server_the_one_copy_outside_its_callers_stands(self) -> None:
        contracts = [
            _contract("idl", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("frontend", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("frontend", "grpc::CurrencyService/*", "consumer", "go_client"),
        ]
        assert {(c, p) for c, p, _ in _pairs(contracts)} == {("frontend", "idl")}

    def test_with_no_server_and_two_candidates_no_copy_is_trusted(self) -> None:
        contracts = [
            _contract("ads", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("payment", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("frontend", "grpc::CurrencyService/*", "consumer", "go_client"),
        ]
        assert match_contracts(contracts) == []

    def test_vendored_copies_are_not_orphans(self) -> None:
        contracts = [
            _contract("currency", "grpc::CurrencyService/*", "provider", "js_add_service"),
            _contract("currency", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
            _contract("payment", "grpc::shop.CurrencyService/Convert", "provider", "proto"),
        ]
        orphans = build_diagnostics(contracts, []).orphan_providers
        assert sorted(o.repo for o in orphans) == ["currency", "currency"]


class TestProjectGrpcServices:
    _CLIENT_PROJECT = (
        "<Project>\n  <ItemGroup>\n"
        '    <Protobuf Include="Protos\\shop.proto" OutputDir="" GrpcServices="Client">\n'
        "    </Protobuf>\n  </ItemGroup>\n</Project>\n"
    )

    def test_a_client_only_proto_provides_nothing(self, tmp_path: Path) -> None:
        contracts = _repo(
            tmp_path, "clientapp", {"ClientApp.csproj": self._CLIENT_PROJECT, "Protos/shop.proto": _PROTO}
        )
        assert contracts == []

    def test_a_server_proto_still_provides(self, tmp_path: Path) -> None:
        project = '<Project><ItemGroup><Protobuf Include="Proto\\shop.proto" GrpcServices="Server" /></ItemGroup></Project>'
        contracts = _repo(tmp_path, "basket", {"Basket.csproj": project, "Proto/shop.proto": _PROTO})
        assert sorted(c.contract_id for c in contracts) == [
            "grpc::shop.CurrencyService/Convert",
            "grpc::shop.PaymentService/Charge",
        ]

    def test_a_commented_out_item_is_not_read(self, tmp_path: Path) -> None:
        project = f"<!--{self._CLIENT_PROJECT}-->"
        contracts = _repo(tmp_path, "app", {"App.csproj": project, "Protos/shop.proto": _PROTO})
        assert len(contracts) == 2

    def test_paths_resolve_from_the_project_and_globs_match(self, tmp_path: Path) -> None:
        project = (
            '<Project><ItemGroup><Protobuf Update="..\\shared\\*.proto" GrpcServices="None" />'
            "</ItemGroup></Project>"
        )
        contracts = _repo(
            tmp_path,
            "web",
            {"src/Web/Web.csproj": project, "src/shared/shop.proto": _PROTO, "api/own.proto": _PROTO},
        )
        assert {c.file_path for c in contracts} == {"api/own.proto"}


class TestCSharpClients:
    def test_di_and_dotted_clients_reach_the_package_qualified_proto(self, tmp_path: Path) -> None:
        # eShop split per project: the server's proto has a package, the clients do not.
        proto = 'syntax = "proto3";\npackage BasketApi;\nservice Basket {\n  rpc GetBasket(Req) returns (Res) {}\n}\n'
        web = {
            "Extensions.cs": "builder.Services.AddGrpcClient<Basket.BasketClient>(o => o.Address = new(\"http://basket-api\"));\n",
        }
        app = {
            "BasketService.cs": (
                "using Grpc.Net.Client;\n"
                "var client = new BasketGrpcClient.Basket.BasketClient(GrpcChannel.ForAddress(url));\n"
            ),
        }
        contracts = [
            *_repo(tmp_path, "basket", {"Proto/basket.proto": proto}),
            *_repo(tmp_path, "webapp", web),
            *_repo(tmp_path, "clientapp", app),
        ]
        assert _pairs(contracts) == {
            ("webapp", "basket", "Proto/basket.proto"),
            ("clientapp", "basket", "Proto/basket.proto"),
        }
