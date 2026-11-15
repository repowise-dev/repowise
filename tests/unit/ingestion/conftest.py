"""Fixtures shared by the ingestion tests."""

from __future__ import annotations

import pytest

from repowise.core.ingestion.document_extractors import document_extractors
from repowise.core.registry import plugins


@pytest.fixture
def extractor_registry(monkeypatch):
    monkeypatch.setattr(document_extractors, "_impls", {})
    monkeypatch.setattr(document_extractors, "_chosen", None)
    monkeypatch.setattr(plugins, "load_plugins", lambda: [])
