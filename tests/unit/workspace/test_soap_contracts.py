"""SOAP contract extraction — WSDL, JAX-WS, and WCF."""

from __future__ import annotations

from pathlib import Path

from repowise.core.workspace.extractors.base import ScanContext
from repowise.core.workspace.extractors.soap import SoapExtractor
from repowise.core.workspace.extractors.soap.jaxws import JaxWsDialect
from repowise.core.workspace.extractors.soap.wcf import WcfDialect
from repowise.core.workspace.extractors.soap.wsdl import WsdlDialect
from repowise.core.workspace.matching import match_contracts
from repowise.core.workspace.origins import ContractOrigin

WSDL_SOURCE = """<?xml version="1.0" encoding="UTF-8"?>
<definitions xmlns="http://schemas.xmlsoap.org/wsdl/"
             xmlns:soap="http://schemas.xmlsoap.org/wsdl/soap/"
             xmlns:tns="http://shop.com/stock"
             targetNamespace="http://shop.com/stock"
             name="StockService">
    <portType name="StockPort">
        <operation name="GetStock">
            <input message="tns:GetStockRequest"/>
            <output message="tns:GetStockResponse"/>
        </operation>
        <operation name="UpdateStock">
            <input message="tns:UpdateStockRequest"/>
            <output message="tns:UpdateStockResponse"/>
        </operation>
    </portType>
</definitions>
"""

JAXWS_ENDPOINT_SOURCE = """package com.shop.stock;

import javax.jws.WebService;
import javax.jws.WebMethod;

@WebService(targetNamespace = "http://shop.com/stock")
public class StockEndpoint {
    @WebMethod
    public int getStock(String sku) {
        return 42;
    }

    @WebMethod(operationName = "UpdateStock")
    public void setStock(String sku, int count) {
    }
}
"""

WSIMPORT_CLIENT_SOURCE = """package com.shop.client;

import javax.xml.ws.WebServiceClient;
import com.shop.stock.StockService;
import com.shop.stock.StockPort;

public class PriceLookup {
    @WebServiceClient(name = "StockService", targetNamespace = "http://shop.com/stock")
    class StockService {}

    public void query() {
        StockPort port = service.getStockPort();
        port.getStock("SKU123");
    }
}
"""

WCF_SERVICE_SOURCE = """using System.ServiceModel;

namespace StockService
{
    [ServiceContract(Namespace = "http://shop.com/stock")]
    public interface IStockService
    {
        [OperationContract]
        int GetStock(string sku);

        // [OperationContract]
        // int CommentedOp(string sku);
    }
}
"""

WCF_CLIENT_SOURCE = """using System.ServiceModel;

namespace Shop.Client
{
    [ServiceContract(Namespace = "http://shop.com/stock")]
    public interface IStockService {}

    public class PriceClient
    {
        public void CallService()
        {
            var factory = new ChannelFactory<IStockService>("*");
            var client = factory.CreateChannel();
            client.GetStock("SKU1");
        }
    }
}
"""


def _ctx(rel_path: str, content: str, repo: str = "repo") -> ScanContext:
    suffix = "." + rel_path.rsplit(".", 1)[-1]
    return ScanContext(repo, rel_path, suffix, content, {}, None)


class TestWsdlDialect:
    def test_extract_wsdl_operations(self) -> None:
        ctx = _ctx("StockService.wsdl", WSDL_SOURCE, "soap_stock")
        contracts = WsdlDialect().extract(ctx)
        assert len(contracts) == 2

        c_get = next(c for c in contracts if "getstock" in c.contract_id)
        assert c_get.contract_id == "soap::http://shop.com/stock/getstock"
        assert c_get.contract_type == "soap"
        assert c_get.role == "provider"
        assert c_get.origin == ContractOrigin.IDL
        assert c_get.line is not None

        c_upd = next(c for c in contracts if "updatestock" in c.contract_id)
        assert c_upd.contract_id == "soap::http://shop.com/stock/updatestock"
        assert c_upd.role == "provider"


class TestJaxWsDialect:
    def test_extract_jaxws_endpoint(self) -> None:
        ctx = _ctx("src/main/java/com/shop/stock/StockEndpoint.java", JAXWS_ENDPOINT_SOURCE, "soap_stock")
        contracts = JaxWsDialect().extract(ctx)
        assert len(contracts) == 2

        c_get = next(c for c in contracts if "getstock" in c.contract_id)
        assert c_get.contract_id == "soap::http://shop.com/stock/getstock"
        assert c_get.role == "provider"
        assert c_get.origin == ContractOrigin.ROUTE_REGISTRATION

        c_upd = next(c for c in contracts if "updatestock" in c.contract_id)
        assert c_upd.contract_id == "soap::http://shop.com/stock/updatestock"

    def test_extract_wsimport_client(self) -> None:
        ctx = _ctx("src/main/java/com/shop/client/PriceLookup.java", WSIMPORT_CLIENT_SOURCE, "soap_client")
        contracts = JaxWsDialect().extract(ctx)
        assert len(contracts) == 1
        assert contracts[0].contract_id == "soap::http://shop.com/stock/getstock"
        assert contracts[0].role == "consumer"
        assert contracts[0].origin == ContractOrigin.CLIENT_CALL

    def test_extract_jaxws_interface_bare_methods(self) -> None:
        source = """package com.shop.stock;

import javax.jws.WebService;

@WebService(targetNamespace = "http://shop.com/stock")
public interface StockService {
    int getStock(String sku);
}
"""
        ctx = _ctx("src/main/java/com/shop/stock/StockService.java", source, "soap_stock")
        contracts = JaxWsDialect().extract(ctx)
        assert len(contracts) == 1
        assert contracts[0].contract_id == "soap::http://shop.com/stock/getstock"
        assert contracts[0].role == "provider"
        assert contracts[0].origin == ContractOrigin.ROUTE_REGISTRATION

    def test_extract_jaxws_interface_annotated_methods_skips_fallback(self) -> None:
        source = """package com.shop.stock;

import javax.jws.WebService;
import javax.jws.WebMethod;

@WebService(targetNamespace = "http://shop.com/stock")
public interface StockService {
    @WebMethod
    int getStock(String sku);
}
"""
        ctx = _ctx("src/main/java/com/shop/stock/StockService.java", source, "soap_stock")
        contracts = JaxWsDialect().extract(ctx)
        assert len(contracts) == 1
        assert contracts[0].contract_id == "soap::http://shop.com/stock/getstock"
        assert contracts[0].role == "provider"
        assert contracts[0].origin == ContractOrigin.ROUTE_REGISTRATION


class TestWcfDialect:
    def test_extract_wcf_service_skips_commented_operation(self) -> None:
        ctx = _ctx("StockService/IStockService.cs", WCF_SERVICE_SOURCE, "wcf_svc")
        contracts = WcfDialect().extract(ctx)
        assert len(contracts) == 1
        assert contracts[0].contract_id == "soap::http://shop.com/stock/getstock"
        assert contracts[0].role == "provider"
        assert contracts[0].origin == ContractOrigin.ROUTE_REGISTRATION
        assert not any("commented" in c.contract_id for c in contracts)

    def test_extract_wcf_channelfactory_client(self) -> None:
        ctx = _ctx("src/PriceClient.cs", WCF_CLIENT_SOURCE, "wcf_client")
        contracts = WcfDialect().extract(ctx)
        assert len(contracts) == 1
        assert contracts[0].contract_id == "soap::http://shop.com/stock/getstock"
        assert contracts[0].role == "consumer"
        assert contracts[0].origin == ContractOrigin.CLIENT_CALL

    def test_extract_wcf_multiple_channelfactories_binds_per_interface(self) -> None:
        source = """using System.ServiceModel;

namespace Shop.Client
{
    [ServiceContract(Namespace = "http://shop.com/stock")]
    public interface IStockService {}

    [ServiceContract(Namespace = "http://shop.com/order")]
    public interface IOrderService {}

    public class MultiClient
    {
        public void CallServices()
        {
            var stockFactory = new ChannelFactory<IStockService>("*");
            var orderFactory = new ChannelFactory<IOrderService>("*");
            var stockClient = stockFactory.CreateChannel();
            var orderClient = orderFactory.CreateChannel();
            stockClient.GetStock("SKU1");
            orderClient.PlaceOrder("SKU1", 2);
        }
    }
}
"""
        ctx = _ctx("src/MultiClient.cs", source, "wcf_client")
        contracts = WcfDialect().extract(ctx)
        consumers = [c for c in contracts if c.role == "consumer"]
        assert len(consumers) == 2
        c_stock = next(c for c in consumers if "getstock" in c.contract_id)
        assert c_stock.contract_id == "soap::http://shop.com/stock/getstock"
        assert c_stock.symbol_name == "IStockService.GetStock"
        assert c_stock.origin == ContractOrigin.CLIENT_CALL

        c_order = next(c for c in consumers if "placeorder" in c.contract_id)
        assert c_order.contract_id == "soap::http://shop.com/order/placeorder"
        assert c_order.symbol_name == "IOrderService.PlaceOrder"
        assert c_order.origin == ContractOrigin.CLIENT_CALL


class TestSoapMatching:
    def test_wsdl_to_client_match(self) -> None:
        wsdl_ctx = _ctx("StockService.wsdl", WSDL_SOURCE, "soap_stock")
        client_ctx = _ctx("src/main/java/com/shop/client/PriceLookup.java", WSIMPORT_CLIENT_SOURCE, "soap_client")

        providers = SoapExtractor().extract(Path("."), "soap_stock", files=[(wsdl_ctx.rel_path, wsdl_ctx.suffix, wsdl_ctx.content)])
        consumers = SoapExtractor().extract(Path("."), "soap_client", files=[(client_ctx.rel_path, client_ctx.suffix, client_ctx.content)])

        links = match_contracts(providers + consumers)
        assert len(links) >= 1
        assert any(
            link.provider_repo == "soap_stock"
            and link.consumer_repo == "soap_client"
            and link.contract_id == "soap::http://shop.com/stock/getstock"
            for link in links
        )

    def test_interface_to_channelfactory_match(self) -> None:
        svc_ctx = _ctx("StockService/IStockService.cs", WCF_SERVICE_SOURCE, "wcf_svc")
        client_ctx = _ctx("src/PriceClient.cs", WCF_CLIENT_SOURCE, "wcf_client")

        providers = SoapExtractor().extract(Path("."), "wcf_svc", files=[(svc_ctx.rel_path, svc_ctx.suffix, svc_ctx.content)])
        consumers = SoapExtractor().extract(Path("."), "wcf_client", files=[(client_ctx.rel_path, client_ctx.suffix, client_ctx.content)])

        links = match_contracts(providers + consumers)
        assert len(links) == 1
        assert links[0].provider_repo == "wcf_svc"
        assert links[0].consumer_repo == "wcf_client"
        assert links[0].contract_id == "soap::http://shop.com/stock/getstock"
