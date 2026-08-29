"""Characterization of the Extract Method slicer over the archetype corpus.

The golden pins every span the slicer offers per archetype, so a gate change
has to explain each membership delta. The two marker tests state the soundness
contract the golden is only evidence for: a region a corpus source marks
``unsound:`` may never be offered, and one marked ``sound:`` must stay offered.
"""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.dataflow.dialects import get_defuse_dialect

from .refactoring_corpus_fixture import (
    CASE_FILES,
    extractions_for,
    load_golden,
    marked_lines,
    offered_start_lines,
    rewrite_requested,
    write_golden,
)

GOLDEN = "golden_extractions.json"


def _served() -> tuple[tuple[str, str], ...]:
    return tuple((lang, name) for lang, name in CASE_FILES if get_defuse_dialect(lang) is not None)


def test_corpus_extractions_match_the_golden() -> None:
    served = _served()
    if not served:
        pytest.skip("no corpus language has a dataflow dialect here")
    payload = {language: extractions_for(language, name) for language, name in served}
    if rewrite_requested():
        write_golden(GOLDEN, payload)
        pytest.skip("golden rewritten")
    expected = load_golden(GOLDEN)
    assert payload == {key: value for key, value in expected.items() if key in payload}


@pytest.mark.parametrize(("language", "filename"), CASE_FILES)
def test_unsound_regions_are_never_offered(language: str, filename: str) -> None:
    if get_defuse_dialect(language) is None:
        pytest.skip(f"no dataflow dialect for {language} here")
    marked = marked_lines(filename, "unsound")
    assert marked, "corpus lost its unsound markers"
    assert not (set(marked) & offered_start_lines(language, filename))


@pytest.mark.parametrize(("language", "filename"), CASE_FILES)
def test_sound_regions_are_still_offered(language: str, filename: str) -> None:
    if get_defuse_dialect(language) is None:
        pytest.skip(f"no dataflow dialect for {language} here")
    marked = marked_lines(filename, "sound")
    assert marked, "corpus lost its sound markers"
    offered = offered_start_lines(language, filename)
    assert set(marked) <= offered, f"gate suppressed sound regions {sorted(set(marked) - offered)}"
