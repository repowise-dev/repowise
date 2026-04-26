"""Contract extractors — HTTP routes, gRPC services, message topics, service boundaries."""

from __future__ import annotations

from .grpc_extractor import GrpcExtractor
from .http_extractor import HttpExtractor, normalize_http_path
from .service_boundary import (
    ServiceBoundary,
    assign_service,
    detect_service_boundaries,
)
from .topic_extractor import TopicExtractor

__all__ = [
    "GrpcExtractor",
    "HttpExtractor",
    "ServiceBoundary",
    "TopicExtractor",
    "assign_service",
    "detect_service_boundaries",
    "normalize_http_path",
]
