"""The web glossary's labels are generated from core's, so pin the generated file.

A label changed in ``biomarker_labels.py`` without regenerating fails here,
rather than surfacing as a page and an agent prompt naming a marker differently.
"""

from __future__ import annotations

import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[3]
_SCRIPT = _ROOT / "scripts/generate_biomarker_labels.py"


def _generator():
    spec = importlib.util.spec_from_file_location("_generate_biomarker_labels", _SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_committed_labels_match_core() -> None:
    from repowise.core.analysis.health.biomarker_labels import BIOMARKER_LABELS

    generator = _generator()
    assert generator._OUT.read_text(encoding="utf-8") == generator.render(BIOMARKER_LABELS), (
        "packages/ui/src/health/generated/biomarker-labels.ts is stale. "
        "Run: python scripts/generate_biomarker_labels.py"
    )


def test_an_unknown_marker_reads_with_spaces() -> None:
    from repowise.core.analysis.health.biomarker_labels import biomarker_label

    assert biomarker_label("primitive_obsession") == "Long parameter list"
    assert biomarker_label("made_up_marker") == "made up marker"
