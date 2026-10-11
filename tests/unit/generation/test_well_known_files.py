"""Tests for the well-known-file role map (generation.well_known_files)."""

from __future__ import annotations

from repowise.core.generation.well_known_files import well_known_role


def test_recognized_names_get_a_real_role():
    role = well_known_role("pyproject.toml")
    assert role is not None
    # The role describes purpose, not the filename.
    assert "pyproject" not in role.lower()
    assert well_known_role("package.json").lower().startswith("node package manifest")


def test_lookup_is_case_insensitive_and_path_aware():
    assert well_known_role("README.md") == well_known_role("docs/sub/readme.md")
    assert well_known_role("a/b/c/Dockerfile") is not None


def test_suffix_family_fallback_for_lockfiles():
    assert well_known_role("uv.lock") is not None
    assert well_known_role("Cargo.lock") == well_known_role("poetry.lock")


def test_directory_context_roles_project_specific_filenames():
    # CI workflow filenames vary per repo, but any file under .github/workflows
    # is a workflow definition.
    assert well_known_role(".github/workflows/publish-internal.yml") is not None
    assert "workflow" in well_known_role(".github/workflows/ci.yml").lower()
    # An unrelated yml outside the convention is not recognised by the dir rule.
    assert well_known_role("src/config/values.yml") is None


def test_unrecognized_name_returns_none():
    assert well_known_role("some_random_module.py") is None
    assert well_known_role("internal_widget.tsx") is None
