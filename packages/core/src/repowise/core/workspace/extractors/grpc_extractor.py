"""Backwards-compatible re-exports for the gRPC contract extractor.

The implementation moved into the :mod:`.grpc` dialect package. This shim keeps
``from ...extractors.grpc_extractor import GrpcExtractor, _extract_service_blocks``
working for existing imports.
"""

from __future__ import annotations

from .grpc import GrpcExtractor, _extract_service_blocks

__all__ = ["GrpcExtractor", "_extract_service_blocks"]
