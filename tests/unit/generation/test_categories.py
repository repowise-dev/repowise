"""Tests for file-category classification (generation.categories)."""

from __future__ import annotations

from repowise.core.generation.categories import (
    CATEGORY_CODE,
    CATEGORY_CONFIG,
    CATEGORY_DATA,
    CATEGORY_DOC,
    CATEGORY_PIPELINE,
    file_category,
)


def test_doc_files():
    assert file_category("README.md", "markdown") == CATEGORY_DOC
    assert file_category("docs/guide.rst") == CATEGORY_DOC


def test_pipeline_files():
    assert file_category(".github/workflows/ci.yml", "yaml") == CATEGORY_PIPELINE
    assert file_category("Jenkinsfile") == CATEGORY_PIPELINE


def test_data_files():
    assert file_category("alembic/versions/0001_init.py", "python") == CATEGORY_DATA
    assert file_category("app/models/user.py", "python") == CATEGORY_DATA
    assert file_category("schema.sql", "sql") == CATEGORY_DATA


def test_config_files():
    assert file_category("pyproject.toml", "toml") == CATEGORY_CONFIG
    assert file_category("settings.py", "python", is_config=True) == CATEGORY_CONFIG


def test_code_default():
    assert file_category("src/services/billing.py", "python") == CATEGORY_CODE


def test_resolution_order_doc_beats_pipeline():
    # A markdown file inside a workflows dir is still documentation.
    assert file_category(".github/workflows/README.md", "markdown") == CATEGORY_DOC
