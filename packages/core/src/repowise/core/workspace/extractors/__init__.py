"""Contract extractors: HTTP routes, gRPC services, sockets, message topics,
database tables, service boundaries."""

from __future__ import annotations

from .data import DataExtractor, normalize_table_name
from .grpc_extractor import GrpcExtractor
from .http_extractor import HttpExtractor, normalize_http_path
from .service_boundary import (
    ServiceBoundary,
    assign_service,
    detect_service_boundaries,
)
from .socket_extractor import SocketExtractor
from .topic_extractor import TopicExtractor

__all__ = [
    "DataExtractor",
    "GrpcExtractor",
    "HttpExtractor",
    "ServiceBoundary",
    "SocketExtractor",
    "TopicExtractor",
    "assign_service",
    "detect_service_boundaries",
    "normalize_http_path",
    "normalize_table_name",
]
