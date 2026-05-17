"""Tests for the dependency-name → C4 category classifier."""

from __future__ import annotations

from repowise.core.ingestion.external_systems.classifier import classify, display_name_for


def test_framework_names():
    assert classify("fastapi") == "framework"
    assert classify("next") == "framework"
    assert classify("react") == "framework"


def test_service_names_and_patterns():
    assert classify("stripe") == "service"
    assert classify("@aws-sdk/client-s3") == "service"
    assert classify("@google-cloud/storage") == "service"
    assert classify("boto3") == "service"


def test_tool_names():
    assert classify("eslint") == "tool"
    assert classify("pytest") == "tool"


def test_unknown_defaults_to_library():
    assert classify("some-random-pkg-23") == "library"


def test_classifier_is_case_insensitive():
    assert classify("FastAPI") == "framework"


def test_display_name_strips_scope_and_normalises():
    assert display_name_for("@aws-sdk/client-s3") == "Client S3"
    assert display_name_for("tree-sitter-python") == "Tree Sitter Python"
