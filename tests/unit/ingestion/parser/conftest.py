"""Unit tests for the unified ASTParser.

Tests parse inline byte strings so no filesystem I/O is needed.
Covers Python, TypeScript, Go, Rust, Java, C++ — one test class per language.
"""

from __future__ import annotations

import pytest

from repowise.core.ingestion.parser import ASTParser


@pytest.fixture(scope="module")
def parser() -> ASTParser:
    return ASTParser()
