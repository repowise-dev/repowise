"""Shared helpers for SOAP contract extraction."""

from __future__ import annotations


def soap_contract_id(namespace: str, operation: str) -> str:
    """Canonical SOAP contract ID: ``soap::<namespace>/<operation>`` lower-cased."""
    ns = namespace.rstrip("/")
    op = operation.strip("/").lower()
    return f"soap::{ns}/{op}".lower()
