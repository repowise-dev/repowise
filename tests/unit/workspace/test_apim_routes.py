"""Azure API Management APIs as gateway routes in the service-name map."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.matching import run_matching
from repowise.core.workspace.origins import LinkStrategy
from repowise.core.workspace.service_names import ServiceNameMap, apim, build_service_name_map
from repowise.core.workspace.service_names.base import Route, SourceFile


def _read(text: str, name: str) -> list[tuple[str, Route]]:
    return [(c.name, c.route) for c in apim.read(SourceFile(Path(name), text)) if c.route]


def _routes(m: ServiceNameMap) -> set[tuple[str, str, str | None, str]]:
    return {(r.gateway, r.prefix, r.upstream, r.repo) for r in m.routes}


BICEP = """
param backendUrl string

resource service 'Microsoft.ApiManagement/service@2023-05-01-preview' existing = {
  name: 'apim'
}

resource orders 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'apim/orders'
  properties: {
    displayName: 'Orders {v1}'
    path: 'orders'
    serviceUrl: 'http://orders-service:8080/api'
    protocols: [
      'https'
    ]
  }
  resource getOrder 'operations' = {
    name: 'get-order'
    properties: {
      method: 'GET'
      urlTemplate: '/{id}'
    }
  }
}

resource catalog 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  parent: service
  name: 'catalog'
  properties: {
    path: 'shop/catalog'
    serviceUrl: 'https://catalog-api/'
  }
}

resource dynamic 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'apim/dynamic'
  properties: {
    path: 'dynamic'
    serviceUrl: backendUrl
  }
}

resource interpolated 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'apim/interpolated'
  properties: {
    path: 'inter'
    serviceUrl: '${backendUrl}/api'
  }
}

resource root 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  name: 'apim/root'
  properties: {
    path: ''
    serviceUrl: 'http://everything'
  }
}

resource svc 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: 'apim2'
  resource payments 'apis' = {
    name: 'payments'
    properties: {
      path: 'pay'
      serviceUrl: 'http://payment-api:80'
    }
  }
}
"""


def test_bicep_api_shapes() -> None:
    assert _read(BICEP, "main.bicep") == [
        ("orders-service", Route("/orders", "/api")),
        ("catalog-api", Route("/shop/catalog", "")),
        ("", Route("/dynamic", None)),  # a parameter: no target, still a blocker
        ("", Route("/inter", None)),  # an interpolation
        # path '' is a catch-all and is skipped
        ("payment-api", Route("/pay", "")),  # a child ``apis`` resource
    ]


def test_arm_template_api_shapes() -> None:
    text = """{
      "$schema": "https://schema.management.azure.com/schemas/2019-04-01/deploymentTemplate.json#",
      "resources": [
        {
          "type": "Microsoft.ApiManagement/service",
          "name": "apim",
          "resources": [
            {"type": "Microsoft.ApiManagement/service/apis", "name": "apim/orders",
             "properties": {"path": "orders", "serviceUrl": "http://orders-service:8080/api"}}
          ]
        },
        {"type": "Microsoft.ApiManagement/service/apis", "name": "apim/expr",
         "properties": {"path": "expr", "serviceUrl": "[parameters('backend')]"}},
        {"type": "Microsoft.ApiManagement/service/apis", "name": "apim/nourl",
         "properties": {"path": "nourl"}},
        {"type": "Microsoft.Web/sites", "name": "site",
         "properties": {"path": "nope", "serviceUrl": "http://not-apim"}}
      ]
    }"""
    assert _read(text, "azuredeploy.json") == [
        ("orders-service", Route("/orders", "/api")),
        ("", Route("/expr", None)),
        ("", Route("/nourl", None)),
    ]


def test_apiops_api_information() -> None:
    text = """{
      "properties": {
        "displayName": "Orders",
        "path": "orders/v2",
        "serviceUrl": "http://orders-service/api/v2",
        "protocols": ["https"]
      }
    }"""
    assert _read(text, "apiInformation.json") == [
        ("orders-service", Route("/orders/v2", "/api/v2"))
    ]
    # The same shape in another JSON file is not an APIM API.
    assert _read(text, "settings.json") == []


def test_apim_route_links_the_gateway_to_the_service_behind_it(tmp_path: Path) -> None:
    infra = tmp_path / "apim" / "infra"
    infra.mkdir(parents=True)
    (infra / "apim.bicep").write_text(
        "resource orders 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {\n"
        "  name: 'apim/orders'\n"
        "  properties: {\n"
        "    path: 'orders'\n"
        "    serviceUrl: 'http://orders-service:8080/api'\n"
        "  }\n"
        "}\n"
    )
    res = tmp_path / "orders" / "src" / "main" / "resources"
    res.mkdir(parents=True)
    (res / "application.yml").write_text("spring:\n  application:\n    name: orders-service\n")

    m = build_service_name_map({n: tmp_path / n for n in ("apim", "orders")})
    assert _routes(m) == {("apim", "/orders", "/api", "orders")}
    [route] = m.routes
    assert (route.name, route.source) == ("orders-service", "apim")
    found = m.route_for("apim", "/orders/1")
    assert found is not None and found[0].repo == "orders"
    assert [
        (lk.consumer_repo, lk.provider_repo, lk.strategy)
        for lk in run_matching([], m.routes).service_links
    ] == [("apim", "orders", LinkStrategy.GATEWAY_ROUTE)]
