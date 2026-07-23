"""The generation-version salt on the structural renderers.

A file page has no model path, so nothing will ever rewrite it to a higher
standard. ``update`` re-renders the pages of files that changed, which means a
repository nobody has touched keeps whatever its pages said when they were
written, however much the renderer improves in the meantime.

The salt is the only thing that closes that. Each structural page stores a hash
of its subject folded with a fingerprint of the renderer that produced it, and
``update`` regenerates the pages whose stored hash no longer matches. These
tests pin the two halves of that: the fingerprint moves when it should and
stays put when it should, and the staleness sweep built on it selects exactly
the pages that need redoing and no others.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from repowise.core.generation.models import compute_page_id
from repowise.core.generation.page_generator.structural import (
    FILE_PAGE_TEMPLATE,
    RENDER_KEY,
    stale_file_page_paths,
    structural_content_hash,
    structural_fingerprint,
)

# ---------------------------------------------------------------------------
# Minimal ParsedFile stand-in: the sweep reads two attributes and nothing else.
# ---------------------------------------------------------------------------


@dataclass
class _FileInfo:
    path: str


@dataclass
class _Parsed:
    file_info: _FileInfo
    content_hash: str = ""
    symbols: list = field(default_factory=list)


def _parsed(path: str, content_hash: str = "hash-of-bytes") -> _Parsed:
    return _Parsed(file_info=_FileInfo(path=path), content_hash=content_hash)


def _stored(paths_to_hashes: dict[str, str]) -> dict[str, str]:
    return {compute_page_id("file_page", p): h for p, h in paths_to_hashes.items()}


def _current_hash(content_hash: str, **kwargs) -> str:
    return structural_content_hash(
        content_hash, structural_fingerprint(FILE_PAGE_TEMPLATE, **kwargs)
    )


# ---------------------------------------------------------------------------
# The fingerprint itself
# ---------------------------------------------------------------------------


def test_fingerprint_is_stable_for_the_same_renderer():
    """Two runs of the same release must agree, or every update rewrites
    every page forever."""
    assert structural_fingerprint(FILE_PAGE_TEMPLATE) == structural_fingerprint(FILE_PAGE_TEMPLATE)


def test_fingerprint_moves_when_the_template_changes():
    """The substantive case: a release that improves a template has to reach
    wikis that already exist."""
    before = structural_fingerprint(FILE_PAGE_TEMPLATE, source="# old template")
    after = structural_fingerprint(FILE_PAGE_TEMPLATE, source="# improved template")
    assert before != after


def test_fingerprint_moves_with_language_and_style():
    base = structural_fingerprint(FILE_PAGE_TEMPLATE, source="x")
    other_language = structural_fingerprint(FILE_PAGE_TEMPLATE, source="x", language="fr")
    other_style = structural_fingerprint(
        FILE_PAGE_TEMPLATE, source="x", style_fingerprint="caveman"
    )
    assert len({base, other_language, other_style}) == 3


def test_unreadable_template_does_not_pin_every_page_to_one_hash():
    """A missing template hashes to a value that still varies by name, so a
    packaging accident cannot silently freeze the whole file layer."""
    a = structural_fingerprint("does_not_exist_a.j2")
    b = structural_fingerprint("does_not_exist_b.j2")
    assert a != b


def test_content_hash_is_empty_without_a_subject():
    """Layer and cycle pages have no per-file subject. An empty hash keeps
    them out of the sweep rather than re-rendering them every run."""
    assert structural_content_hash("", structural_fingerprint(FILE_PAGE_TEMPLATE)) == ""


# ---------------------------------------------------------------------------
# The staleness sweep update runs
# ---------------------------------------------------------------------------


def test_nothing_is_stale_when_the_renderer_has_not_moved():
    """The everyday case. A second update in a row must find no work."""
    parsed = [_parsed("src/a.py"), _parsed("src/b.py", "other-bytes")]
    stored = _stored(
        {
            "src/a.py": _current_hash("hash-of-bytes"),
            "src/b.py": _current_hash("other-bytes"),
        }
    )
    assert stale_file_page_paths(stored, parsed) == []


def test_every_page_is_stale_exactly_once_after_the_salt_moves():
    """Bump the renderer, and every stored page is selected. Re-store what
    that run would write, and the next update selects none."""
    parsed = [_parsed(f"src/m{i}.py", f"bytes-{i}") for i in range(5)]
    old_fingerprint = structural_fingerprint(FILE_PAGE_TEMPLATE, source="# an older template")
    stored = _stored(
        {p.file_info.path: structural_content_hash(p.content_hash, old_fingerprint) for p in parsed}
    )

    first_pass = stale_file_page_paths(stored, parsed)
    assert sorted(first_pass) == sorted(p.file_info.path for p in parsed)

    rewritten = _stored({p.file_info.path: _current_hash(p.content_hash) for p in parsed})
    assert stale_file_page_paths(rewritten, parsed) == []


def test_pages_predating_the_salt_are_refreshed_once():
    """An existing wiki stores no hash at all. That is the population the
    first release carrying this has to pick up."""
    parsed = [_parsed("src/a.py")]
    assert stale_file_page_paths(_stored({"src/a.py": ""}), parsed) == ["src/a.py"]


def test_a_file_with_no_page_yet_is_not_stale():
    """Absent is not stale. A file added since the last index is the normal
    path's job, and counting it here would double-report the work."""
    parsed = [_parsed("src/brand_new.py")]
    assert stale_file_page_paths({}, parsed) == []


def test_a_file_with_no_content_hash_is_skipped():
    """No stable subject means no stable expectation, so treating it as stale
    would re-render it on every single run."""
    parsed = [_parsed("src/unparsed.py", content_hash="")]
    assert stale_file_page_paths(_stored({"src/unparsed.py": "whatever"}), parsed) == []


def test_only_the_files_whose_bytes_moved_are_stale():
    """Mixed store: the sweep is per page, not all-or-nothing."""
    parsed = [_parsed("src/same.py", "unchanged"), _parsed("src/edited.py", "new-bytes")]
    stored = _stored(
        {
            "src/same.py": _current_hash("unchanged"),
            "src/edited.py": _current_hash("old-bytes"),
        }
    )
    assert stale_file_page_paths(stored, parsed) == ["src/edited.py"]


def test_the_render_key_is_stored_where_it_survives_a_restart():
    """The salt is worthless if it is not persisted.

    ``GeneratedPage.content_hash`` looks like the natural home and is not one:
    nothing writes it to the database and nothing reads it back, so a key kept
    there would be silently absent on the next run and no page would ever look
    stale. Metadata round-trips, so the key lives there.
    """
    from repowise.core.persistence.models import Page

    assert not hasattr(Page, "content_hash")
    assert RENDER_KEY == "render_key"


def test_a_style_switch_makes_every_page_stale():
    """A style change has no prompt to miss on any more, so the salt is what
    carries it to the file layer."""
    parsed = [_parsed("src/a.py")]
    stored = _stored({"src/a.py": _current_hash("hash-of-bytes")})
    assert stale_file_page_paths(stored, parsed, style_fingerprint="caveman") == ["src/a.py"]


def test_a_custom_style_template_override_is_what_gets_fingerprinted(tmp_path):
    """A style shipping its own file_page.j2 must fingerprint that, not the base.

    The generator resolves the override through its Jinja loader; the update
    path has no generator and reads from disk, so it takes a template_dir and
    tries the override first. If the two disagreed, every page a custom style
    rendered would look stale against the base template on every update.
    """
    (tmp_path / FILE_PAGE_TEMPLATE).write_text("OVERRIDE {{ ctx.file_path }}", encoding="utf-8")

    base = structural_fingerprint(FILE_PAGE_TEMPLATE)
    overridden = structural_fingerprint(FILE_PAGE_TEMPLATE, template_dir=tmp_path)
    assert base != overridden

    # A page rendered under the override is not stale against the override.
    parsed = [_parsed("src/a.py")]
    stored = _stored({"src/a.py": structural_content_hash("hash-of-bytes", overridden)})
    assert stale_file_page_paths(stored, parsed, template_dir=tmp_path) == []
    # ...but is stale against the base, which is the mismatch this prevents.
    assert stale_file_page_paths(stored, parsed) == ["src/a.py"]
