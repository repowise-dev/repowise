"""A structurally-keyed page must leave generation with a structural key.

Three page types are keyed on what they group rather than on a path —
``module_page``, ``layer_page`` and ``scc_page`` — and the stale-page sweep
finds them by that key. A keyed page that lands without one is invisible to
the sweep: it can never be retired, so every later run strands it as a
duplicate alongside its replacement.

Nothing downstream can tell that page apart from one of the many types that
carry no key on purpose, which is why the check lives here, at the moment the
key is assigned, and raises.
"""

from __future__ import annotations

import pytest

from repowise.core.generation.models import GeneratedPage
from repowise.core.generation.page_generator.orchestrate import _stamp_structural_keys


def _page(page_id: str, page_type: str, *, target_path: str = "", **metadata) -> GeneratedPage:
    return GeneratedPage(
        page_id=page_id,
        page_type=page_type,
        title=page_id,
        content="Body.",
        source_hash="",
        model_name="mock",
        provider_name="mock",
        input_tokens=0,
        output_tokens=0,
        cached_tokens=0,
        generation_level=1,
        target_path=target_path,
        created_at="2026-01-01T00:00:00Z",
        updated_at="2026-01-01T00:00:00Z",
        summary="",
        metadata=dict(metadata),
    )


def test_a_keyed_page_with_neither_members_nor_a_path_raises():
    """The gap the fallback leaves open.

    The stamp keys a module page on its member list and falls back to the
    target_path. A page with an empty member list *and* an empty target_path
    comes out of both branches with an empty string, which reads downstream as
    "this type is not keyed" — the one thing it is not.
    """
    page = _page("module_page:orphan", "module_page")

    with pytest.raises(ValueError, match="structural key missing"):
        _stamp_structural_keys([page])


def test_the_raise_names_the_page_and_why():
    """A run that stops has to say which page stopped it.

    The page id alone is not enough to act on — the fix differs depending on
    whether the producer lost the member list or the target path.
    """
    page = _page("scc_page:orphan", "scc_page")

    with pytest.raises(ValueError) as exc:
        _stamp_structural_keys([page])

    assert "scc_page:orphan" in str(exc.value)
    assert "scc_page" in str(exc.value)
    assert "no member list" in str(exc.value)


def test_a_member_keyed_page_is_still_keyed_from_its_members():
    page = _page("module_page:one", "module_page", file_paths=["pkg/a.py", "pkg/b.py"])

    _stamp_structural_keys([page])

    assert page.structural_key
    assert page.structural_key.startswith("module-")


def test_a_layer_is_still_keyed_from_its_target_path():
    """Layers have no member list and are keyed by their curated id."""
    page = _page("layer_page:presentation", "layer_page", target_path="presentation")

    _stamp_structural_keys([page])

    assert page.structural_key == "presentation"


def test_a_key_set_by_the_producer_is_kept():
    page = _page("module_page:planned", "module_page", file_paths=["pkg/a.py"])
    page.structural_key = "concept-abc123"

    _stamp_structural_keys([page])

    assert page.structural_key == "concept-abc123"


def test_an_unkeyed_type_is_left_alone_and_does_not_raise():
    """The check must not become an argument for keying everything.

    A file page's identity *is* its path, so a key would be a duplicate
    identity rather than an identity. The sweep deletes by this column, so a
    file page carrying one is a file page the sweep can delete.
    """
    pages = [
        _page("file_page:a.py", "file_page", target_path="a.py"),
        _page("symbol_spotlight:a.py::F", "symbol_spotlight", target_path="a.py"),
        _page("repo_overview:repowise", "repo_overview"),
    ]

    _stamp_structural_keys(pages)

    assert [p.structural_key for p in pages] == [None, None, None]
