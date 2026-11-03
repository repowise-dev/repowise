"""SOAP contract extraction — WSDL schemas, JAX-WS endpoints/clients, and WCF services/clients."""

from __future__ import annotations

from ..dialect import ContractDialect, DialectExtractor
from .jaxws import JaxWsDialect
from .wcf import WcfDialect
from .wsdl import WsdlDialect

DIALECTS: tuple[ContractDialect, ...] = (
    WsdlDialect(),
    JaxWsDialect(),
    WcfDialect(),
)


class SoapExtractor(DialectExtractor):
    """Extract SOAP contracts from WSDL files, JAX-WS, and WCF."""

    dialects = DIALECTS


__all__ = ["DIALECTS", "SoapExtractor"]
