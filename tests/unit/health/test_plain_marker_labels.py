"""Marker labels use plain engineering terms."""

from __future__ import annotations

import pytest

from repowise.core.analysis.health.biomarker_labels import biomarker_label, biomarker_phrase


@pytest.mark.parametrize(
    ("marker", "label"),
    [
        ("nested_complexity", "Deep nesting"),
        ("large_method", "Long function"),
        ("complex_conditional", "Compound condition"),
        ("developer_congestion", "Many authors"),
        ("knowledge_loss", "Fading ownership"),
    ],
)
def test_marker_label(marker: str, label: str) -> None:
    assert biomarker_label(marker) == label


@pytest.mark.parametrize(
    ("marker", "phrase"),
    [
        ("nested_complexity", "deep nesting"),
        ("dry_violation", "DRY violation"),
        ("io_in_loop", "I/O in loop"),
        ("sql_select_star", "SELECT * in a view"),
    ],
)
def test_marker_phrase_reads_mid_sentence(marker: str, phrase: str) -> None:
    assert biomarker_phrase(marker) == phrase
