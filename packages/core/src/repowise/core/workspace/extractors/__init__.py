"""Contract extractors: HTTP routes, gRPC services, sockets, message topics,
database tables, service boundaries."""

from __future__ import annotations

from .data import DataExtractor, normalize_table_name
from .grpc import GrpcExtractor
from .http import HttpExtractor, normalize_http_path
from .openapi import OpenApiExtractor, merge_openapi_providers
from .service_boundary import (
    ServiceBoundary,
    assign_service,
    detect_service_boundaries,
)
from .socket import SocketExtractor
from .topic import TopicExtractor

__all__ = [
    "DataExtractor",
    "GrpcExtractor",
    "HttpExtractor",
    "OpenApiExtractor",
    "ServiceBoundary",
    "SocketExtractor",
    "TopicExtractor",
    "assign_service",
    "detect_service_boundaries",
    "merge_openapi_providers",
    "normalize_http_path",
    "normalize_table_name",
]
