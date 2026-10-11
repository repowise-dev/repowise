"""The gate that decides a quiet repository is "already up to date".

``update`` returns early when there are no new commits, no config change and no
renderer change. That last term is the only route a template improvement has
into a repository nobody has touched, so whatever it fingerprints is the set of
templates a release can actually ship improvements to. A template left out of
it is a template whose changes never land.
"""

from __future__ import annotations

from pathlib import Path

from repowise.cli.commands.update_cmd.command import _current_renderer_fingerprint
from repowise.core.generation.page_generator.structural import (
    FILE_PAGE_TEMPLATE,
    SYMBOL_SPOTLIGHT_TEMPLATE,
    structural_fingerprint,
)


def _fingerprint_of(template: str, source: str) -> str:
    return structural_fingerprint(template, source=source)


def test_the_gate_covers_every_template_with_no_model_path(tmp_path: Path):
    """Both structural templates have to be inside the gate.

    A spotlight is rendered by a template and nothing else — no model will come
    along later and rewrite it — so if its bytes are outside this value, a
    release that improves only the spotlight template reports "already up to
    date" and does nothing.
    """
    current = _current_renderer_fingerprint(tmp_path)

    assert _fingerprint_of(FILE_PAGE_TEMPLATE, _read(FILE_PAGE_TEMPLATE)) in current
    assert _fingerprint_of(SYMBOL_SPOTLIGHT_TEMPLATE, _read(SYMBOL_SPOTLIGHT_TEMPLATE)) in current


def test_the_gate_is_stable_across_runs_of_one_release(tmp_path: Path):
    """It has to agree with itself, or every update rewrites every page."""
    assert _current_renderer_fingerprint(tmp_path) == _current_renderer_fingerprint(tmp_path)


def test_the_gate_distinguishes_the_two_templates(tmp_path: Path):
    """Folding both in must not collapse them to one value.

    If editing either template produced the same fingerprint, the gate could
    not tell which pages to sweep and would have to re-render both types.
    """
    assert _fingerprint_of(FILE_PAGE_TEMPLATE, "same bytes") != _fingerprint_of(
        SYMBOL_SPOTLIGHT_TEMPLATE, "same bytes"
    )


def _read(template: str) -> str:
    from repowise.core.generation.page_generator.structural import _TEMPLATES_DIR

    return (_TEMPLATES_DIR / template).read_text(encoding="utf-8")
